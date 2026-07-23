"""
api/routes/brief.py
Daily-brief API — the harness's FastAPI shell replaced by routes under the
Curia app (Firebase auth via current_user_id; the harness's CORS-*/Basic-Auth
surface and admin/dashboard endpoints do not exist here).

  GET  /brief/topics            the 7 system Beats (for the prefs UI)
  PUT  /brief/preferences       upsert prefs: display name, location, beats, custom topics
  POST /brief/generate          enqueue today's brief (interactive lane) — 202 + job id
  GET  /brief/today             today's brief manifest (status + segments + audio when ready)
  POST /brief/preopt            trigger the batch Pre-Opt (background lane)
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

from api.auth import current_user_id
from api.schemas import CreateJobResponse
from brief import store
from core.queue import enqueue

router = APIRouter(prefix="/brief")


class BriefPreferences(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    location_name: str = Field(min_length=1, max_length=120)
    beats: list[str] = Field(default_factory=list, max_length=7)
    custom_topics: list[str] = Field(default_factory=list, max_length=5)


@router.get("/topics")
async def list_beats(user_id: str = Depends(current_user_id)) -> list[dict]:
    topics = await store.list_system_topics()
    return [{"id": str(t["id"]), "name": t["name"]} for t in topics]


@router.put("/preferences", status_code=204)
async def put_preferences(
    prefs: BriefPreferences, user_id: str = Depends(current_user_id)
) -> None:
    await store.create_user_with_topics(
        user_id, prefs.display_name, prefs.location_name, "07:00",
        prefs.beats, prefs.custom_topics,
    )
    logger.info(f"[brief] prefs saved user={user_id} beats={len(prefs.beats)}")


@router.post("/generate", response_model=CreateJobResponse, status_code=202)
async def generate_brief(user_id: str = Depends(current_user_id)) -> CreateJobResponse:
    user = await store.get_user(user_id)
    if not user:
        raise HTTPException(409, "Set brief preferences first (PUT /brief/preferences)")
    today = date.today().isoformat()
    job_id = await enqueue(
        type="generate_brief",
        payload={"user_id": user_id, "date": today},
        user_id=user_id,
        lane="interactive",
    )
    brief = await store.get_or_create_daily_brief(user_id, today)
    return CreateJobResponse(id=uuid.UUID(brief["id"]), status="queued", job_id=job_id)


@router.get("/today")
async def get_today(user_id: str = Depends(current_user_id)) -> dict:
    today = date.today().isoformat()
    pool_brief = await store.get_daily_brief_for_date(user_id, today)
    if not pool_brief:
        raise HTTPException(404, "No brief for today — POST /brief/generate first")
    detail = await store.get_daily_brief_detail(pool_brief["id"])
    return detail or dict(pool_brief)


@router.post("/preopt", response_model=CreateJobResponse, status_code=202)
async def trigger_preopt(
    user_id: str = Depends(current_user_id), topic_id: Optional[str] = None
) -> CreateJobResponse:
    job_id = await enqueue(
        type="preopt_brief",
        payload={"topic_id": topic_id} if topic_id else {},
        user_id=user_id,
        lane="background",
    )
    return CreateJobResponse(id=uuid.uuid4(), status="queued", job_id=job_id)
