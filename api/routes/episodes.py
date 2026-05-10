"""
api/routes/episodes.py
POST /episodes — create episode row + enqueue generation job.
GET /episodes, GET /episodes/:id, GET /episodes/:id/audio.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from api.auth import current_user_id
from api.schemas import (
    CreateEpisodeRequest,
    CreateJobResponse,
    EpisodeDetail,
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
            (id, user_id, show_name, show_idea_id, editorial_direction, status)
        VALUES
            ($id::uuid, $user_id, $show, $idea_id::uuid, $direction, 'queued')
        """,
        {
            "id": episode_id,
            "user_id": user_id,
            "show": req.show_name,
            "idea_id": str(req.show_idea_id) if req.show_idea_id else None,
            "direction": req.editorial_direction or "",
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
            SELECT id, show_name, title, status, created_at, error, quality_score
            FROM episode
            WHERE user_id = $user_id AND status = $status
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "status": status, "limit": limit},
        )
    else:
        rows = await db_query(
            """
            SELECT id, show_name, title, status, created_at, error, quality_score
            FROM episode
            WHERE user_id = $user_id
            ORDER BY created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )
    return [EpisodeSummary(**r) for r in rows]


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
async def get_episode(
    episode_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> EpisodeDetail:
    row = await db_fetchrow(
        """
        SELECT id, show_name, title, status, created_at, error,
               transcript, outline, audio_path, source_ids, editorial_direction,
               quality_score, quality_feedback, quality_violations, regenerated
        FROM episode
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")
    return EpisodeDetail(**row)


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
