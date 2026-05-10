"""
api/auth.py
Bearer-token auth + role gating.

Header format:  Authorization: Bearer <api_token>
Lookup: users.api_token == token → (user_id, role)

Exposed dependencies:
    current_user_id    → str  (just the user_id, for routes that don't care about role)
    current_user       → CurrentUser  (id + role, for routes that need to branch)
    qa_required        → CurrentUser  (raises 403 unless role='qa')

Swap to Cognito later: only this file changes; route signatures stay identical.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, Header, HTTPException, status

from core.db.connection import db_fetchrow


@dataclass
class CurrentUser:
    id: str
    role: str  # 'user' | 'qa'

    @property
    def is_qa(self) -> bool:
        return self.role == "qa"


async def _resolve_token(authorization: Optional[str]) -> CurrentUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header (expected 'Bearer <token>')",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Empty bearer token",
        )
    row = await db_fetchrow(
        "SELECT id, role FROM users WHERE api_token = $token",
        {"token": token},
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
        )
    return CurrentUser(id=str(row["id"]), role=str(row.get("role") or "user"))


# ---------------------------------------------------------------------------
# Dependencies — for FastAPI's `Depends(...)`
# ---------------------------------------------------------------------------


async def current_user(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> CurrentUser:
    """Resolve the bearer token to a CurrentUser (id + role)."""
    return await _resolve_token(authorization)


async def current_user_id(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> str:
    """Just the user_id. Use this when a route doesn't need to branch on role."""
    user = await _resolve_token(authorization)
    return user.id


async def qa_required(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> CurrentUser:
    """Gate routes to QA only. Raises 403 for regular users."""
    user = await _resolve_token(authorization)
    if not user.is_qa:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="QA role required for this endpoint",
        )
    return user
