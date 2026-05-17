"""
api/routes/auth.py
Auth-related endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import current_user, CurrentUser
from api.schemas import AuthUserResponse
from core.db.connection import db_fetchrow

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me", response_model=AuthUserResponse)
async def auth_me(user: CurrentUser = Depends(current_user)) -> AuthUserResponse:
    row = await db_fetchrow(
        "SELECT id, email, name, role FROM users WHERE id = $id",
        {"id": user.id},
    )
    if not row:
        return AuthUserResponse(id=user.id, role=user.role)
    return AuthUserResponse(
        id=str(row["id"]),
        email=row.get("email"),
        name=row.get("name"),
        role=str(row.get("role") or "user"),
        avatar_url=None,  # TODO: store from Firebase picture
    )
