"""
api/routes/episodes.py
POST /episodes — create episode row + enqueue generation job.
GET /episodes, GET /episodes/:id, GET /episodes/:id/audio.
"""

import json
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.auth import current_user_id
from api.schemas import (
    CreateEpisodeRequest,
    CreateJobResponse,
    EpisodeDetail,
    EpisodeSourceObject,
    EpisodeSummary,
)
from core.db.connection import db_execute, db_fetchrow, db_query
from core.queue import enqueue

router = APIRouter()


@router.post("/episodes", response_model=CreateJobResponse, status_code=202)
async def create_episode(
    req: CreateEpisodeRequest, user_id: str = Depends(current_user_id)
) -> CreateJobResponse:
    """
    Create a `queued` episode row + enqueue 'generate_episode' job.
    Worker fills in title, outline, transcript, audio_path on completion.
    """
    if req.editorial_direction and req.editorial_direction.strip():
        from core.angle.validator import validate_angle_rules
        angle_check = validate_angle_rules(req.editorial_direction)
        if not angle_check.valid:
            raise HTTPException(422, f"Invalid editorial direction: {angle_check.reason}")

    episode_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, show_idea_id, editorial_direction,
             length_minutes, speaker_override, status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
             $length_minutes, $speaker_override, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": req.show_name,
            "idea_id": str(req.show_idea_id) if req.show_idea_id else None,
            "direction": req.editorial_direction or "",
            "length_minutes": req.length_minutes,
            "speaker_override": req.speaker,
        },
    )
    job_id = await enqueue(
        type="generate_episode",
        payload={"episode_id": episode_id, "user_id": user_id},
        user_id=user_id,
    )
    return CreateJobResponse(id=uuid.UUID(episode_id), status="queued", job_id=job_id)


@router.get("/episodes", response_model=list[EpisodeSummary])
async def list_episodes(
    user_id: str = Depends(current_user_id),
    status: str | None = Query(default=None),
    limit: int = Query(default=50, le=500),
) -> list[EpisodeSummary]:
    if status:
        rows = await db_query(
            """
            SELECT id, show_name, title, status, created_at, error, quality_score,
                   length_minutes, speaker_override, source_ids, outline,
                   play_progress, listened, last_played_at
            FROM episode
            WHERE user_id = $user_id AND status = $status
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "status": status, "limit": limit},
        )
    else:
        rows = await db_query(
            """
            SELECT id, show_name, title, status, created_at, error, quality_score,
                   length_minutes, speaker_override, source_ids, outline,
                   play_progress, listened, last_played_at
            FROM episode
            WHERE user_id = $user_id
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )

    # Batch-fetch source objects for all episodes in one query
    from urllib.parse import urlparse
    seen: set[str] = set()
    all_source_uuids: list[uuid.UUID] = []
    for r in rows:
        for sid in (r.get("source_ids") or []):
            key = str(sid)
            if key not in seen:
                seen.add(key)
                try:
                    all_source_uuids.append(uuid.UUID(key))
                except (ValueError, TypeError):
                    pass

    source_map: dict[str, EpisodeSourceObject] = {}
    if all_source_uuids:
        src_rows = await db_query(
            """
            SELECT id, url, title FROM source
            WHERE id = ANY($ids) AND user_id = $user_id
            """,
            {"ids": all_source_uuids, "user_id": user_id},
        )
        for s in src_rows:
            try:
                domain = urlparse(s["url"]).hostname or s["url"]
                domain = domain.removeprefix("www.")
            except Exception:
                domain = s["url"]
            source_map[str(s["id"])] = EpisodeSourceObject(
                id=s["id"], domain=domain, title=s["title"]
            )

    result = []
    for r in rows:
        data = dict(r)
        seen_ids: set[str] = set()
        deduped: list[EpisodeSourceObject] = []
        for sid in (data.get("source_ids") or []):
            key = str(sid)
            if key in source_map and key not in seen_ids:
                seen_ids.add(key)
                deduped.append(source_map[key])
        data["source_objects"] = deduped
        result.append(EpisodeSummary(**data))
    return result


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
async def get_episode(
    episode_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> EpisodeDetail:
    row = await db_fetchrow(
        """
        SELECT id, show_name, title, status, created_at, error,
               transcript, outline, audio_path, source_ids, editorial_direction,
               quality_score, quality_feedback, quality_violations, regenerated,
               length_minutes, speaker_override, tts_timings
        FROM episode
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")

    from urllib.parse import urlparse
    data = dict(row)
    source_ids = data.get("source_ids") or []
    source_objects: list[EpisodeSourceObject] = []
    if source_ids:
        source_uuids: list[uuid.UUID] = []
        for sid in source_ids:
            try:
                source_uuids.append(uuid.UUID(str(sid)))
            except (ValueError, TypeError):
                pass
        if source_uuids:
            src_rows = await db_query(
                """
                SELECT id, url, title FROM source
                WHERE id = ANY($ids) AND user_id = $user_id
                """,
                {"ids": source_uuids, "user_id": user_id},
            )
            for s in src_rows:
                try:
                    domain = urlparse(s["url"]).hostname or s["url"]
                    domain = domain.removeprefix("www.")
                except Exception:
                    domain = s["url"]
                source_objects.append(EpisodeSourceObject(
                    id=s["id"], domain=domain, title=s["title"]
                ))
    data["source_objects"] = source_objects
    return EpisodeDetail(**data)


@router.put("/episodes/{episode_id}/progress", status_code=204)
async def update_episode_progress(
    episode_id: uuid.UUID,
    body: dict,
    user_id: str = Depends(current_user_id),
) -> None:
    play_progress = float(body.get("play_progress") or 0)
    listened = bool(body.get("listened", False))
    await db_execute(
        """
        UPDATE episode
        SET play_progress = $play_progress,
            listened = $listened,
            last_played_at = NOW()
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {
            "id": str(episode_id),
            "user_id": user_id,
            "play_progress": max(0.0, min(1.0, play_progress)),
            "listened": listened,
        },
    )


class FeedbackRequest(BaseModel):
    rating: str
    note: str | None = None


@router.post("/episodes/{episode_id}/feedback", status_code=204)
async def submit_episode_feedback(
    episode_id: uuid.UUID,
    body: FeedbackRequest,
    user_id: str = Depends(current_user_id),
) -> None:
    await db_execute(
        """
        UPDATE episode
        SET feedback = $feedback
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {
            "id": str(episode_id),
            "user_id": user_id,
            "feedback": json.dumps({"rating": body.rating, "note": body.note}),
        },
    )


@router.get("/episodes/{episode_id}/audio")
async def get_episode_audio(
    episode_id: uuid.UUID, user_id: str = Depends(current_user_id)
):
    row = await db_fetchrow(
        """
        SELECT audio_path, status, title FROM episode
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")
    if row["status"] != "ready":
        raise HTTPException(409, f"episode not ready (status={row['status']})")
    audio_path = row.get("audio_path")
    if not audio_path or not Path(audio_path).exists():
        raise HTTPException(410, "audio file missing on disk")
    filename = f"{row.get('title') or 'episode'}.mp3"
    return FileResponse(
        path=audio_path,
        media_type="audio/mpeg",
        filename=filename,
    )
