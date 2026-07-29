"""
api/routes/episodes.py
POST /episodes — create episode row + enqueue generation job.
GET /episodes, GET /episodes/:id, GET /episodes/:id/audio.
"""

import asyncio
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api.auth import audio_user_id, current_user_id
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
    episode_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO episode
            (id, user_id, show_name, show_idea_id, editorial_direction,
             length_minutes, speaker_override, speaker_pair, status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction,
             $length_minutes, $speaker_override, $speaker_pair, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": req.show_name,
            "idea_id": str(req.show_idea_id) if req.show_idea_id else None,
            "direction": req.editorial_direction or "",
            "length_minutes": req.length_minutes,
            "speaker_override": req.speaker,
            "speaker_pair": req.speaker_pair if not req.speaker else None,
        },
    )
    job_id = await enqueue(
        type="generate_episode",
        payload={"episode_id": episode_id, "user_id": user_id},
        user_id=user_id,
        lane="interactive",  # explicit create/remix — user watches the generating screen
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
                   play_progress, listened, last_played_at, show_idea_id
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
                   play_progress, listened, last_played_at, show_idea_id
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
            SELECT id, url, title, author FROM source
            WHERE id = ANY($ids) AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
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
                id=s["id"], domain=domain, title=s["title"], author=s["author"]
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
               length_minutes, speaker_override, tts_timings, bgm_plan
        FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
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
                SELECT id, url, title, author FROM source
                WHERE id = ANY($ids) AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
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
                    id=s["id"], domain=domain, title=s["title"], author=s["author"]
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
    episode_id: uuid.UUID,
    request: Request,
    user_id: str = Depends(audio_user_id),
):
    row = await db_fetchrow(
        """
        SELECT audio_path, audio_url, status, title FROM episode
        WHERE id = $id::uuid AND (user_id = $user_id OR (is_seed = true AND user_id = 'seed'))
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")
    if row["status"] != "ready":
        raise HTTPException(409, f"episode not ready (status={row['status']})")

    # R2 path — return a presigned URL as JSON so the client can stream directly
    audio_url = row.get("audio_url")
    if audio_url:
        from core.storage.blob import generate_presigned_url
        from fastapi.responses import JSONResponse
        presigned = generate_presigned_url(audio_url, expires_in=3600)
        if presigned:
            return JSONResponse({"url": presigned})

    # Local path — stream file with range support
    audio_path = row.get("audio_path")
    if not audio_path or not await asyncio.to_thread(Path(audio_path).exists):
        raise HTTPException(410, "audio file missing on disk")

    file_size = await asyncio.to_thread(lambda: Path(audio_path).stat().st_size)
    filename = f"{row.get('title') or 'episode'}.mp3"
    range_header = request.headers.get("range")

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
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    return StreamingResponse(
        _iter_file(audio_path, 0, file_size - 1),
        status_code=200,
        media_type="audio/mpeg",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
            "Content-Disposition": f'inline; filename="{filename}"',
        },
    )
