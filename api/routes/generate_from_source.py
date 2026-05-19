"""
api/routes/generate_from_source.py
POST /generate-from-source — validate ownership, enqueue job, return job_id.

Used by the share sheet's "Queue It" action after the user has customised
how they want the episode generated (standalone vs. full-pipeline, format,
host, duration, angle override).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Literal, Optional

from api.auth import current_user_id
from core.db.connection import db_fetchrow
from core.queue import enqueue

router = APIRouter()


class GenerateFromSourceRequest(BaseModel):
    source_id: uuid.UUID
    standalone: bool = True
    show_name: Optional[str] = Field(
        default=None,
        description="Backend format name or frontend slug. If null, LLM picks.",
    )
    speaker: Optional[Literal["kenji", "arjun", "emeka"]] = None
    length_minutes: Optional[int] = Field(default=None, ge=3, le=30)
    angle_override: Optional[str] = Field(default=None, max_length=500)


class GenerateFromSourceResponse(BaseModel):
    job_id: uuid.UUID


@router.post("/generate-from-source", response_model=GenerateFromSourceResponse, status_code=202)
async def generate_from_source(
    req: GenerateFromSourceRequest,
    user_id: str = Depends(current_user_id),
) -> GenerateFromSourceResponse:
    """
    Validate that the source belongs to the current user, then enqueue a
    'generate_from_source' job and return its job_id.

    The worker handles two paths:
    - standalone=true:  evaluate this source alone → one episode with overrides
    - standalone=false: run full idea pipeline → apply overrides to episodes
                        containing this source_id
    """
    row = await db_fetchrow(
        "SELECT id FROM source WHERE id = $id::uuid AND user_id = $user_id",
        {"id": str(req.source_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "source not found")

    # Resolve frontend slug to backend name if provided
    show_name: str | None = None
    if req.show_name:
        from studio.formats import resolve_format_name
        try:
            show_name = resolve_format_name(req.show_name)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    existing_job = await db_fetchrow(
        """
        SELECT id
        FROM jobs
        WHERE type = 'generate_from_source'
          AND status IN ('queued', 'running')
          AND user_id = $user_id
          AND payload->>'source_id' = $source_id
        ORDER BY created_at ASC
        LIMIT 1
        """,
        {"user_id": user_id, "source_id": str(req.source_id)},
    )
    if existing_job:
        return GenerateFromSourceResponse(job_id=uuid.UUID(str(existing_job["id"])))

    job_id = await enqueue(
        type="generate_from_source",
        payload={
            "user_id": user_id,
            "source_id": str(req.source_id),
            "standalone": req.standalone,
            "show_name": show_name,
            "speaker": req.speaker,
            "length_minutes": req.length_minutes,
            "angle_override": req.angle_override,
        },
        user_id=user_id,
    )
    return GenerateFromSourceResponse(job_id=uuid.UUID(str(job_id)))
