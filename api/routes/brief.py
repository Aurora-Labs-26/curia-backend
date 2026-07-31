"""
api/routes/brief.py
Daily-brief API — the harness's FastAPI shell replaced by routes under the
Curia app (Firebase auth via current_user_id; the harness's CORS-*/Basic-Auth
surface and admin/dashboard endpoints do not exist here).

  GET  /brief/topics            the 7 system Beats (for the prefs UI)
  GET  /brief/preferences       current saved prefs, to pre-fill the edit sheet
  PUT  /brief/preferences       upsert prefs: display name, beats, custom topics,
                                delivery time + timezone; city derived from the
                                request IP unless explicitly overridden
  POST /brief/generate          enqueue today's brief (interactive lane) — 202 + job id
  GET  /brief/today             today's brief manifest (status + segments + audio when ready)
  GET  /brief/today/audio       presigned URL (s3) or range-streamed file (local) — mirrors GET /episodes/{id}/audio
  PUT  /brief/{id}/progress     playback progress (resume) — matches PUT /episodes/{id}/progress
  POST /brief/preopt            trigger the batch Pre-Opt (background lane)
"""

import asyncio
import uuid
from datetime import date
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator

from api.auth import audio_user_id, current_user_id
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

    @field_validator("timezone")
    @classmethod
    def _valid_iana(cls, v: str) -> str:
        """Postgres validates zone names AT QUERY TIME inside
        list_due_users_for_generation — a single garbage row would make that
        one query (and everyone's scheduling) throw every poll. Refuse it here.
        (v3.1 review v1.md §4.1)"""
        from zoneinfo import ZoneInfo
        try:
            ZoneInfo(v)
        except Exception:
            raise ValueError(f"unknown IANA timezone {v!r}")
        return v
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


@router.get("/preferences")
async def get_preferences(user_id: str = Depends(current_user_id)) -> dict:
    """Current saved prefs, for the profile screen's edit sheet to pre-fill
    before the user changes anything. Nothing else returns this whole shape
    together — GET /brief/today's response varies by brief state and never
    includes custom_topics/timezone/location_name at all."""
    user = await store.get_user(user_id)
    if not user:
        raise HTTPException(404, "Brief preferences not set")
    topics = await store.get_user_topics(user_id)
    return {
        "display_name": user["display_name"],
        "beats": [t["name"] for t in topics["chosen"]],
        "custom_topics": [t["name"] for t in topics["custom"]],
        "scheduled_time": str(user["scheduled_time"]),
        "timezone": user["timezone"],
        "location_name": user["location_name"],
    }


@router.put("/preferences")
async def put_preferences(
    prefs: BriefPreferences, request: Request, user_id: str = Depends(current_user_id)
) -> dict:
    """City comes from the request IP so the client never has to ask for a
    location permission or make the user type one; an explicit location_name
    (the "wrong city?" correction in the prefs UI) always wins. Either may be
    empty — the brief then generates without a Local Pulse segment.

    Returns the resolved city (200 with a body, not a bare 204) so the prefs
    screen can show "Local news: <city>" with a correction affordance right
    after the first save — there's no other endpoint that echoes
    location_name back to the client."""
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
    return {"location_name": location_name}


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
    """A daily_briefs row only exists once generation actually starts (see
    get_or_create_daily_brief) — before the user's scheduled time, "prefs set,
    not due yet" and "prefs never set" are otherwise indistinguishable from
    this table alone. The client needs to tell them apart (show a pending
    card with the user's chosen beats vs. show nothing), so this checks
    harness.users directly rather than 404ing the same way for both."""
    today = date.today().isoformat()
    pool_brief = await store.get_daily_brief_for_date(user_id, today)
    if not pool_brief:
        user = await store.get_user(user_id)
        if not user:
            raise HTTPException(404, "Brief preferences not set — PUT /brief/preferences first")
        topics = await store.get_user_topics(user_id)
        beats = [t["name"] for t in topics["chosen"]] + [t["name"] for t in topics["custom"]]
        return {"status": "pending", "beats": beats, "scheduled_time": str(user["scheduled_time"])}
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


def _iter_file(path: str, start: int, end: int, chunk: int = 1024 * 64):
    with open(path, "rb") as f:
        f.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            data = f.read(min(chunk, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


@router.get("/today/audio")
async def get_today_audio(request: Request, user_id: str = Depends(audio_user_id)):
    """daily_briefs.stitched_mp3_url always holds the bare key brief/audio.py
    passed to upload_file (e.g. "audio/brief/<id>.mp3") — it stores that key
    literally rather than upload_file's return value, on either backend (same
    pattern studio/generator.py uses for episodes: audio_url = r2_key, not the
    upload call's result). So which key means depends entirely on
    get_storage_backend(), not on the string's shape — on s3 it's presignable
    directly; on local it's a path relative to CURIA_STORAGE_LOCAL_DIR that
    core.storage.blob._upload_local actually wrote the bytes to. Branches the
    same way GET /episodes/{id}/audio does (S3 presign vs. local range-
    streamed file)."""
    today = date.today().isoformat()
    brief = await store.get_daily_brief_for_date(user_id, today)
    if not brief:
        raise HTTPException(404, "No brief for today")
    key = brief.get("stitched_mp3_url")
    if not key:
        raise HTTPException(409, "Brief audio is not ready yet")

    from core.storage.blob import get_storage_backend

    if get_storage_backend() != "s3":
        import os
        audio_path = str(Path(os.getenv("CURIA_STORAGE_LOCAL_DIR", "data/blobs")) / key)
        if not await asyncio.to_thread(Path(audio_path).exists):
            raise HTTPException(410, "audio file missing on disk")
        file_size = await asyncio.to_thread(lambda: Path(audio_path).stat().st_size)
        range_header = request.headers.get("range")

        if range_header:
            try:
                range_val = range_header.strip().replace("bytes=", "")
                range_start, range_end = range_val.split("-")
                start = int(range_start)
                end = int(range_end) if range_end else file_size - 1
            except (ValueError, AttributeError):
                raise HTTPException(416, "invalid Range header")
            end = min(end, file_size - 1)
            if start > end or start < 0:
                raise HTTPException(416, "range not satisfiable")
            return StreamingResponse(
                _iter_file(audio_path, start, end),
                status_code=206,
                media_type="audio/mpeg",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(end - start + 1),
                    "Content-Disposition": 'inline; filename="brief.mp3"',
                },
            )
        return StreamingResponse(
            _iter_file(audio_path, 0, file_size - 1),
            status_code=200,
            media_type="audio/mpeg",
            headers={
                "Accept-Ranges": "bytes",
                "Content-Length": str(file_size),
                "Content-Disposition": 'inline; filename="brief.mp3"',
            },
        )

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
