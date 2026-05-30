"""api/routes/brief.py — Daily brief endpoints (test UI + app-facing)."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from api.auth import CurrentUser, current_user, current_user_id
from core.db.connection import db_fetchrow, db_query
from intelligence.brief_generator import (
    generate_brief,
    generate_brief_stream,
    run_step_user_context,
    run_step_news_fetch,
    run_step_interest_filter,
    run_step_curation,
    run_step_outline,
    run_step_transcript,
)

router = APIRouter(prefix="/brief", tags=["brief"])

_HTML_PATH = Path(__file__).parent.parent.parent / "assets" / "brief_test.html"


class BriefTodayResponse(BaseModel):
    status: str
    date: str
    audio_url: Optional[str] = None
    audio_duration_seconds: Optional[float] = None
    transcript: Optional[str] = None
    articles: Optional[Any] = None
    outline: Optional[Any] = None


@router.get("/today", response_model=BriefTodayResponse)
async def brief_today(user: CurrentUser = Depends(current_user)) -> BriefTodayResponse:
    today = datetime.date.today().isoformat()
    row = await db_fetchrow(
        """
        SELECT status, audio_url, audio_duration_seconds, transcript,
               articles_json, outline_json
        FROM daily_briefs
        WHERE user_id = $user_id AND date = $date::date
        """,
        {"user_id": user.id, "date": today},
    )
    if not row:
        return BriefTodayResponse(status="pending", date=today)
    return BriefTodayResponse(
        status=row["status"],
        date=today,
        audio_url=row.get("audio_url"),
        audio_duration_seconds=row.get("audio_duration_seconds"),
        transcript=row.get("transcript"),
        articles=row.get("articles_json"),
        outline=row.get("outline_json"),
    )


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def brief_ui():
    if not _HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="brief_test.html not found in assets/")
    return HTMLResponse(_HTML_PATH.read_text())


@router.get("/users")
async def brief_users() -> list[dict]:
    rows = await db_query("SELECT id, email, name FROM users ORDER BY created_at DESC")
    return [{"id": r["id"], "email": r["email"], "name": r.get("name")} for r in rows]


@router.get("/stream")
async def brief_stream(user_id: str = Query(...)) -> StreamingResponse:
    return StreamingResponse(
        generate_brief_stream(user_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/generate")
async def brief_generate(user_id: Annotated[str, Depends(current_user_id)]) -> dict:
    result = await generate_brief(user_id)
    return {
        "user_name": result.user_name,
        "topics": result.topics,
        "save_count_7d": result.save_count_7d,
        "segments": [
            {"type": s.type, "title": s.title, "text": s.text,
             **({"source_url": s.source_url} if s.source_url else {})}
            for s in result.segments
        ],
        "debug": {"news_items": result.news_items},
    }


# ── Per-step endpoints ────────────────────────────────────────────────────────

@router.get("/step/1/user_context")
async def step1_user_context(user_id: str = Query(...)) -> dict:
    return await run_step_user_context(user_id)


@router.get("/step/2/news_fetch")
async def step2_news_fetch(topics: str = Query(...)) -> dict:
    topic_list = [t.strip() for t in topics.split(",") if t.strip()]
    return await run_step_news_fetch(topic_list)


class FilterBody(BaseModel):
    user_id: str
    news_items: list[dict]
    topics: list[str]


@router.post("/step/3/interest_filter")
async def step3_interest_filter(body: FilterBody) -> dict:
    return await run_step_interest_filter(body.user_id, body.news_items, body.topics)


class CurationBody(BaseModel):
    user_id: str
    filtered_items: list[dict]
    topics: list[str]


@router.post("/step/4/curation")
async def step4_curation(body: CurationBody) -> dict:
    return await run_step_curation(body.user_id, body.filtered_items, body.topics)


class OutlineBody(BaseModel):
    user_id: str
    curated_items: list[dict]


@router.post("/step/5/outline")
async def step5_outline(body: OutlineBody) -> dict:
    return await run_step_outline(body.user_id, body.curated_items)


class TranscriptBody(BaseModel):
    user_id: str
    outline: dict
    curated_items: list[dict]


@router.post("/step/6/transcript")
async def step6_transcript(body: TranscriptBody) -> dict:
    return await run_step_transcript(body.user_id, body.outline, body.curated_items)
