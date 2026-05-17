"""
api/routes/episodes.py
POST /episodes — create episode row + enqueue generation job.
GET /episodes, GET /episodes/:id, GET /episodes/:id/audio.
"""

import os
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse

from api.auth import current_user_id
from api.schemas import (
    CreateEpisodeRequest,
    CreateJobResponse,
    EpisodeDetail,
    EpisodeSummary,
    FormatEntry,
)
from pydantic import BaseModel, Field as PydanticField
from core.db.connection import db_execute, db_fetchrow, db_query
from core.queue import enqueue
from studio.formats import FORMATS, resolve_format_name

router = APIRouter()


def _enrich_row(row: dict) -> dict:
    """Add format_frontend_name / format_display_name from the registry."""
    show_name = row.get("show_name")
    fmt = FORMATS.get(show_name)
    row["format_frontend_name"] = fmt.frontend_name if fmt else None
    row["format_display_name"] = fmt.display_name if fmt else None
    return row


@router.get("/formats", response_model=list[FormatEntry])
async def list_formats() -> list[FormatEntry]:
    """
    Return all available episode formats with both backend name and frontend slug.
    No auth required — clients use this to build format pickers.
    """
    return [
        FormatEntry(
            name=fmt.name,
            frontend_name=fmt.frontend_name,
            display_name=fmt.display_name,
            default_length_minutes=fmt.default_length_minutes,
            default_segment_count=fmt.default_segment_count,
        )
        for fmt in FORMATS.values()
    ]


@router.post("/episodes", response_model=CreateJobResponse, status_code=202)
async def create_episode(
    req: CreateEpisodeRequest, user_id: str = Depends(current_user_id)
) -> CreateJobResponse:
    """
    Create a `queued` episode row + enqueue 'generate_episode' job.
    Worker fills in title, outline, transcript, audio_path on completion.
    """
    try:
        show_name = resolve_format_name(req.show_name)
    except ValueError as exc:
        raise HTTPException(422, str(exc))

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
            "show": show_name,
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
    base_select = """
        SELECT
            e.id, e.show_name, e.title, e.description, e.status, e.created_at, e.error,
            e.quality_score, e.duration_seconds, e.length_minutes, e.speaker_override,
            e.source_ids, e.chapters, e.play_progress, e.listened,
            ARRAY(
                SELECT regexp_replace(
                           regexp_replace(s.url, '^https?://', ''),
                           '/.*$', ''
                       )
                FROM source s
                WHERE s.id::text = ANY(
                    SELECT unnest(e.source_ids)::text
                )
                AND s.url IS NOT NULL
                LIMIT 3
            ) AS source_domains
        FROM episode e
    """
    if status:
        rows = await db_query(
            base_select + """
            WHERE e.user_id = $user_id AND e.status = $status
            ORDER BY e.created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "status": status, "limit": limit},
        )
    else:
        rows = await db_query(
            base_select + """
            WHERE e.user_id = $user_id
            ORDER BY e.created_at DESC LIMIT $limit
            """,
            {"user_id": user_id, "limit": limit},
        )
    return [EpisodeSummary(**_enrich_row(dict(r))) for r in rows]


@router.get("/episodes/{episode_id}", response_model=EpisodeDetail)
async def get_episode(
    episode_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> EpisodeDetail:
    row = await db_fetchrow(
        """
        SELECT id, show_name, title, description, status, created_at, error,
               transcript, outline, audio_path, duration_seconds, source_ids,
               chapters, play_progress, listened, editorial_direction,
               quality_score, quality_feedback, quality_violations, regenerated,
               length_minutes, speaker_override
        FROM episode
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")
    return EpisodeDetail(**_enrich_row(dict(row)))


@router.get("/episodes/{episode_id}/audio")
async def get_episode_audio(
    episode_id: uuid.UUID,
    request: Request,
    user_id: str = Depends(current_user_id),
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

    file_size = Path(audio_path).stat().st_size
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
        # Parse "bytes=start-end"
        try:
            range_val = range_header.strip().replace("bytes=", "")
            range_start, range_end = range_val.split("-")
            start = int(range_start)
            end   = int(range_end) if range_end else file_size - 1
        except (ValueError, AttributeError):
            raise HTTPException(416, "invalid Range header")

        end = min(end, file_size - 1)
        if start > end or start < 0:
            raise HTTPException(416, "range not satisfiable")

        content_length = end - start + 1
        headers = {
            "Content-Range":  f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges":  "bytes",
            "Content-Length": str(content_length),
            "Content-Disposition": f'inline; filename="{row.get("title") or "episode"}.mp3"',
        }
        return StreamingResponse(
            _iter_file(audio_path, start, end),
            status_code=206,
            media_type="audio/mpeg",
            headers=headers,
        )

    # Full file response
    headers = {
        "Accept-Ranges":  "bytes",
        "Content-Length": str(file_size),
        "Content-Disposition": f'inline; filename="{row.get("title") or "episode"}.mp3"',
    }
    return StreamingResponse(
        _iter_file(audio_path, 0, file_size - 1),
        status_code=200,
        media_type="audio/mpeg",
        headers=headers,
    )


class UpdateProgressRequest(BaseModel):
    play_progress: float = PydanticField(..., ge=0.0, le=1.0)
    listened: bool = False


@router.put("/episodes/{episode_id}/progress", status_code=204)
async def update_episode_progress(
    episode_id: uuid.UUID,
    req: UpdateProgressRequest,
    user_id: str = Depends(current_user_id),
):
    """
    Save playback position. Called by the client periodically during playback
    and on completion (play_progress=1.0, listened=true).
    """
    row = await db_fetchrow(
        "SELECT id FROM episode WHERE id = $id::uuid AND user_id = $user_id",
        {"id": str(episode_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "episode not found")
    await db_execute(
        """
        UPDATE episode
        SET play_progress = $progress,
            listened = $listened
        WHERE id = $id::uuid
        """,
        {"id": str(episode_id), "progress": req.play_progress, "listened": req.listened},
    )
    from fastapi.responses import Response
    return Response(status_code=204)
