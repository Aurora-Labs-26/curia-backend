"""
api/routes/brief.py
Daily-brief API — the harness's FastAPI shell replaced by routes under the
Curia app (Firebase auth via current_user_id; the harness's CORS-*/Basic-Auth
surface and admin/dashboard endpoints do not exist here).

  GET  /brief/topics            the 7 system Beats (for the prefs UI)

  GET  /brief/preferences       current saved prefs, to pre-fill the edit sheet
  PUT  /brief/preferences       upsert prefs: display name, beats, custom topics,
                                delivery time + timezone; location ONLY as device
                                coordinates (reverse-geocoded server-side)
  POST /brief/generate          enqueue today's brief (interactive lane) — 202 + job id
  GET  /brief/today             today's brief manifest (status + segments + audio when ready)
  GET  /brief/today/audio       presigned URL (s3) or range-streamed file (local) — mirrors GET /episodes/{id}/audio
  PUT  /brief/{id}/progress     playback progress (resume) — matches PUT /episodes/{id}/progress
  POST /brief/preopt            trigger the batch Pre-Opt (background lane)
"""

import asyncio
import hashlib
import json
import uuid
from datetime import date, datetime, timezone as _utc_tz
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator

from api.auth import audio_user_id, current_user_id
from api.schemas import CreateJobResponse
from brief import cities, store
from core.queue import enqueue

router = APIRouter(prefix="/brief")


def _user_local_date(user: dict, now_utc: Optional[datetime] = None) -> str:
    """The user's own calendar date. Briefs are keyed by user-local date
    everywhere else (the dispatcher's local_date, transcript records), so
    routes must look up / create / enqueue by the same key — date.today() is
    the container's UTC date and goes off-by-one for users east of UTC
    between their midnight and UTC midnight. Falls back to UTC on a bad or
    missing timezone (pre-0037 rows default to 'UTC' anyway)."""
    from zoneinfo import ZoneInfo
    now = now_utc or datetime.now(_utc_tz.utc)
    try:
        return now.astimezone(ZoneInfo(str(user.get("timezone") or "UTC"))).date().isoformat()
    except Exception:
        return now.date().isoformat()


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
    # Location enters ONLY as a device GPS fix (the app's "local news" toggle
    # + OS permission) — the picker/free-text path was removed 2026-08-11.
    #   coords present        -> reverse-geocoded server-side, stored verified
    #   explicit nulls        -> toggle OFF: clear location (no Local Pulse)
    #   fields omitted        -> keep whatever is stored
    latitude: Optional[float] = Field(default=None, ge=-90, le=90)
    longitude: Optional[float] = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _coords_paired(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be sent together")
        return self


class BriefProgress(BaseModel):
    play_progress: Optional[float] = Field(default=None, ge=0, le=1)
    listened: bool = False
    # Event time on the CLIENT, for offline-first replay: stale queued
    # updates are silently ignored instead of rewinding newer progress.
    client_ts: Optional[datetime] = None


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
    """Location semantics (device-coordinates ONLY, 2026-08-11 — the picker/
    free-text path is gone; the app's toggle is the single control):
      - latitude+longitude present   reverse-geocoded server-side; the DEVICE
                                     coordinates are stored (422 if the fix
                                     resolves to no locality, 503 on geocoder
                                     outage — nothing unverified is stored)
      - explicit nulls               toggle OFF: location cleared
      - fields omitted               keep whatever is stored
    Returns the stored city so the app can show "Local news: <city>"."""
    location_country = None
    latitude = longitude = None
    coords_sent = "latitude" in prefs.model_fields_set or "longitude" in prefs.model_fields_set
    if coords_sent and prefs.latitude is None:
        location_name = ""                       # toggle OFF
    elif prefs.latitude is not None:
        try:
            match = await cities.reverse_geocode(prefs.latitude, prefs.longitude)
        except cities.GeocoderUnavailable:
            raise HTTPException(
                503, "Location resolution is temporarily unavailable — try again")
        if not match:
            raise HTTPException(
                422, "Could not resolve a city from that location")
        location_name = match["display"]
        location_country = match["country_code"] or None
        latitude, longitude = match["latitude"], match["longitude"]
    else:
        existing = await store.get_user(user_id)
        location_name = (existing or {}).get("location_name") or ""
        location_country = (existing or {}).get("location_country")
        latitude = (existing or {}).get("latitude")
        longitude = (existing or {}).get("longitude")

    await store.create_user_with_topics(
        user_id, prefs.display_name, location_name, prefs.scheduled_time,
        prefs.beats, prefs.custom_topics, prefs.timezone, location_country,
        latitude, longitude,
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
    today = _user_local_date(user)
    job_id = await enqueue(
        type="generate_brief",
        payload={"user_id": user_id, "date": today},
        user_id=user_id,
        lane="interactive",
    )
    brief = await store.get_or_create_daily_brief(user_id, today)
    return CreateJobResponse(id=uuid.UUID(brief["id"]), status="queued", job_id=job_id)


@router.get("/today")
async def get_today(request: Request, response: Response,
                    user_id: str = Depends(current_user_id)) -> dict:
    """A daily_briefs row only exists once generation actually starts (see
    get_or_create_daily_brief) — before the user's scheduled time, "prefs set,
    not due yet" and "prefs never set" are otherwise indistinguishable from
    this table alone. The client needs to tell them apart (show a pending
    card with the user's chosen beats vs. show nothing), so this checks
    harness.users directly rather than 404ing the same way for both."""
    user = await store.get_user(user_id)
    if not user:
        raise HTTPException(404, "Brief preferences not set — PUT /brief/preferences first")
    today = _user_local_date(user)
    pool_brief = await store.get_daily_brief_for_date(user_id, today)
    if not pool_brief:
        # Delivery time already passed today (e.g. signed up mid-afternoon,
        # default 9am slot is already behind) — don't make the user wait up
        # to 15 minutes for the next dispatch poll. Trigger generation right
        # here instead of showing a stale "Ready at 9:00 AM" for a time
        # that's already gone. get_or_create_daily_brief's (user_id, date)
        # uniqueness means this only fires once — the next call finds the
        # row this created and takes the normal path below.
        if await store.is_user_due_now(user_id):
            brief = await store.get_or_create_daily_brief(user_id, today)
            if brief["created"]:
                await enqueue(
                    type="generate_brief",
                    payload={"user_id": user_id, "date": today},
                    user_id=user_id,
                    lane="interactive",
                )
            pool_brief = brief
        else:
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
    # ETag/304 so the app's offline-first revalidation costs headers, not a
    # full body (it re-checks on every foreground with cached data on screen).
    etag = 'W/"' + hashlib.md5(
        json.dumps(detail, sort_keys=True, default=str).encode()
    ).hexdigest() + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    response.headers["ETag"] = etag
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
    user = await store.get_user(user_id)
    today = _user_local_date(user) if user else date.today().isoformat()
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
    outcome = await store.set_daily_brief_progress(
        str(brief_id), user_id, body.play_progress, body.listened, body.client_ts
    )
    if outcome == "not_found":
        raise HTTPException(404, "brief not found")
    # "stale" is success from the client's outbox point of view: the queued
    # event was superseded, nothing to retry.


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
