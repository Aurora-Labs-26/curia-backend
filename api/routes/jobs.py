"""
api/routes/jobs.py
GET /jobs/{job_id} — poll job status for the current user.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException

from api.auth import current_user_id
from core.db.connection import db_fetchrow

router = APIRouter()


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> dict:
    row = await db_fetchrow(
        """
        SELECT id, type, status, attempts, max_attempts, last_error,
               created_at, updated_at
        FROM jobs
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(job_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "job not found")
    return dict(row)
