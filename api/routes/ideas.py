"""
api/routes/ideas.py
POST /ideas/generate — kicks off LangGraph idea-generation job.
GET /ideas + GET /ideas/:id — read.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from api.auth import current_user_id
from api.schemas import GenerateIdeasResponse, ShowIdeaSummary
from core.db.connection import db_fetchrow, db_query
from core.queue import enqueue

router = APIRouter()


@router.post("/ideas/generate", response_model=GenerateIdeasResponse, status_code=202)
async def generate_ideas(user_id: str = Depends(current_user_id)) -> GenerateIdeasResponse:
    job_id = await enqueue(
        type="generate_ideas",
        payload={"user_id": user_id},
        user_id=user_id,
        lane="interactive",  # explicit user action — they're waiting on the result
    )
    return GenerateIdeasResponse(job_id=job_id, status="queued")


@router.get("/ideas", response_model=list[ShowIdeaSummary])
async def list_ideas(
    user_id: str = Depends(current_user_id),
    limit: int = Query(default=50, le=500),
) -> list[ShowIdeaSummary]:
    rows = await db_query(
        """
        SELECT id, angle, idea_type, format, source_ids, generated, created_at
        FROM show_idea
        WHERE user_id = $user_id
        ORDER BY created_at DESC LIMIT $limit
        """,
        {"user_id": user_id, "limit": limit},
    )
    return [ShowIdeaSummary(**r) for r in rows]


@router.get("/ideas/{idea_id}", response_model=ShowIdeaSummary)
async def get_idea(
    idea_id: uuid.UUID, user_id: str = Depends(current_user_id)
) -> ShowIdeaSummary:
    row = await db_fetchrow(
        """
        SELECT id, angle, idea_type, format, source_ids, generated, created_at
        FROM show_idea
        WHERE id = $id::uuid AND user_id = $user_id
        """,
        {"id": str(idea_id), "user_id": user_id},
    )
    if not row:
        raise HTTPException(404, "show_idea not found")
    return ShowIdeaSummary(**row)
