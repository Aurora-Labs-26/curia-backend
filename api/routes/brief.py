"""
api/routes/brief.py
Daily-brief API — the harness's FastAPI shell replaced by routes under the
Curia app (Firebase auth via current_user_id; the harness's CORS-*/Basic-Auth
surface and admin/dashboard endpoints do not exist here).

  GET  /brief/topics            the 7 system Beats (for the prefs UI)
  PUT  /brief/preferences       upsert prefs: display name, beats, custom topics,
                                delivery time + timezone; city derived from the
                                request IP unless explicitly overridden
  POST /brief/generate          enqueue today's brief (interactive lane) — 202 + job id
  GET  /brief/today             today's brief manifest (status + segments + audio when ready)
  GET  /brief/today/audio       presigned URL for the stitched MP3
  PUT  /brief/{id}/progress     playback progress (resume) — matches PUT /episodes/{id}/progress
  POST /brief/preopt            trigger the batch Pre-Opt (background lane)
"""

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.auth import current_user_id
from api.schemas import CreateJobResponse
from brief import geo, store
from core.queue import enqueue

router = APIRouter(prefix="/brief")


class BriefPreferences(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    beats: list[str] = Field(default_factory=list, max_length=7)
    custom_topics: list[str] = Field(default_factory=list, max_length=5)
    # "HH:MM" local delivery time, interpreted in `timezone`.
    scheduled_time: str = Field(default="09:00", pattern=r"^\d{2}:\d{2}$")
    # IANA zone from the client (Intl.DateTimeFormat().resolvedOptions().timeZone).
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    # Normally omitted — the city is derived from the request IP. Sent only when
    # the user corrects a wrong guess.
    location_name: Optional[str] = Field(default=None, max_length=120)


class BriefProgress(BaseModel):
    play_progress: Optional[float] = Field(default=None, ge=0, le=1)
    listened: bool = False


@router.get("/topics")
async def list_beats(user_id: str = Depends(current_user_id)) -> list[dict]:
    topics = await store.list_system_topics()
    return [{"id": str(t["id"]), "name": t["name"]} for t in topics]


@router.put("/preferences", status_code=204)
async def put_preferences(
    prefs: BriefPreferences, request: Request, user_id: str = Depends(current_user_id)
) -> None:
    """City comes from the request IP so the client never has to ask for a
    location permission or make the user type one; an explicit location_name
    (the "wrong city?" correction in the prefs UI) always wins. Either may be
    empty — the brief then generates without a Local Pulse segment."""
    if prefs.location_name is not None:
        location_name = prefs.location_name.strip()
    else:
        existing = await store.get_user(user_id)
        location_name = await geo.city_from_request(request)
        # Don't let a failed lookup wipe a city we already resolved (or the
        # user already corrected) on an earlier save.
        if not location_name and existing:
            location_name = existing.get("location_name") or ""

    await store.create_user_with_topics(
        user_id, prefs.display_name, location_name, prefs.scheduled_time,
        prefs.beats, prefs.custom_topics, prefs.timezone,
    )
    logger.info(
        f"[brief] prefs saved user={user_id} beats={len(prefs.beats)} "
        f"city={location_name or '(none)'} at={prefs.scheduled_time} {prefs.timezone}"
    )


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
    if not detail:
        return dict(pool_brief)

    # The articles rows carry the 5 story segments, but intro/outro text lives
    # only in the persisted manifest (transcript_records) — the client needs the
    # full ordered run to build chapter offsets, so merge it in here rather than
    # making the client stitch two calls together.
    manifest = await store.get_latest_manifest(user_id, today) or []
    detail["segments"] = manifest
    detail["total_duration_s"] = round(
        sum(s.get("duration_s") or 0 for s in manifest), 1
    )
    return detail


@router.get("/today/audio")
async def get_today_audio(user_id: str = Depends(current_user_id)) -> JSONResponse:
    """Presigned URL for the stitched brief MP3. daily_briefs.stitched_mp3_url
    holds an S3 key, not a playable URL — same shape as GET /episodes/{id}/audio
    so the client's existing playback path works unchanged."""
    today = date.today().isoformat()
    brief = await store.get_daily_brief_for_date(user_id, today)
    if not brief:
        raise HTTPException(404, "No brief for today")
    key = brief.get("stitched_mp3_url")
    if not key:
        raise HTTPException(409, "Brief audio is not ready yet")

    from core.storage.blob import generate_presigned_url

    url = generate_presigned_url(key, expires_in=3600)
    if not url:
        raise HTTPException(500, "Could not sign brief audio URL")
    return JSONResponse({"url": url})


@router.put("/{brief_id}/progress", status_code=204)
async def set_progress(
    brief_id: uuid.UUID, body: BriefProgress, user_id: str = Depends(current_user_id)
) -> None:
    ok = await store.set_daily_brief_progress(
        str(brief_id), user_id, body.play_progress, body.listened
    )
    if not ok:
        raise HTTPException(404, "brief not found")


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
