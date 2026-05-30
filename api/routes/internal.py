"""
api/routes/internal.py — Internal endpoints for the daily-brief service.

Protected by INTERNAL_SECRET header so only the brief service can call them.
Not included in public API docs.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from core.db.connection import db_fetchrow, db_query

router = APIRouter(prefix="/internal", include_in_schema=False)


def _check_secret(x_internal_key: Optional[str]) -> None:
    secret = os.environ.get("INTERNAL_SECRET")
    if secret and x_internal_key != secret:
        raise HTTPException(status_code=403, detail="forbidden")


class UserBriefContext(BaseModel):
    user_id: str
    name: Optional[str]
    interests: list[str]
    location_city: Optional[str]
    brief_notify_time: Optional[str]
    brief_enabled: bool
    fcm_token: Optional[str]
    recent_saves: list[dict[str, Any]]


@router.get("/brief-context/all", response_model=list[UserBriefContext])
async def brief_context_all(
    x_internal_key: Optional[str] = Header(None),
) -> list[UserBriefContext]:
    _check_secret(x_internal_key)
    rows = await db_query(
        """
        SELECT id, name, interests, location_city,
               brief_notify_time::text, brief_enabled, fcm_token
        FROM users
        WHERE brief_enabled = TRUE
          AND interests != '{}'
          AND interests IS NOT NULL
        """,
        {},
    )
    results = []
    for row in rows:
        saves = await _fetch_recent_saves(str(row["id"]))
        results.append(UserBriefContext(
            user_id=str(row["id"]),
            name=row.get("name"),
            interests=list(row.get("interests") or []),
            location_city=row.get("location_city"),
            brief_notify_time=row.get("brief_notify_time"),
            brief_enabled=bool(row.get("brief_enabled", True)),
            fcm_token=row.get("fcm_token"),
            recent_saves=saves,
        ))
    return results


@router.get("/brief-context/{user_id}", response_model=UserBriefContext)
async def brief_context_single(
    user_id: str,
    x_internal_key: Optional[str] = Header(None),
) -> UserBriefContext:
    _check_secret(x_internal_key)
    row = await db_fetchrow(
        """
        SELECT id, name, interests, location_city,
               brief_notify_time::text, brief_enabled, fcm_token
        FROM users
        WHERE id = $id        """,
        {"id": user_id},
    )
    if not row:
        raise HTTPException(status_code=404, detail="user not found")
    saves = await _fetch_recent_saves(user_id)
    return UserBriefContext(
        user_id=str(row["id"]),
        name=row.get("name"),
        interests=list(row.get("interests") or []),
        location_city=row.get("location_city"),
        brief_notify_time=row.get("brief_notify_time"),
        brief_enabled=bool(row.get("brief_enabled", True)),
        fcm_token=row.get("fcm_token"),
        recent_saves=saves,
    )


async def _fetch_recent_saves(user_id: str) -> list[dict[str, Any]]:
    rows = await db_query(
        """
        SELECT title, url, description
        FROM source
        WHERE user_id = $user_id          AND created_at >= now() - interval '7 days'
          AND hidden = FALSE
        ORDER BY created_at DESC
        LIMIT 20
        """,
        {"user_id": user_id},
    )
    return [
        {"title": r.get("title"), "url": r.get("url"), "description": r.get("description")}
        for r in rows
    ]
