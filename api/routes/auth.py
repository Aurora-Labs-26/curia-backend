"""
api/routes/auth.py
Auth-related endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel

from api.auth import current_user, CurrentUser
from api.schemas import AuthUserResponse
from core.db.connection import db_execute, db_fetchrow

router = APIRouter(prefix="/auth", tags=["auth"])


class AppleLinkRequest(BaseModel):
    authorization_code: str
    # Apple returns the user's name (and email) ONLY on the first authorization,
    # and never in the identity token — so the client captures them from the native
    # credential and forwards them here. Optional: absent on later sign-ins.
    name: str | None = None
    email: str | None = None


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


@router.post("/apple", status_code=204)
async def link_apple(req: AppleLinkRequest, user: CurrentUser = Depends(current_user)) -> None:
    """
    Called right after a Sign in with Apple. Does two things:
      1) Persists the one-time name/email Apple hands the client on first auth
         (Apple never resends them, and they're not in the identity token).
      2) Exchanges the one-time authorization code for a long-lived refresh token
         so account deletion can revoke the Apple grant (App Store 5.1.1(v) +
         fixes the ghost-user re-login).
    Both steps are independent and best-effort: a failure in one must not block the
    other, and nothing here blocks sign-in — returns 204 regardless.
    """
    # 1) Name/email — persist FIRST, independent of the (slower, network) code
    # exchange. COALESCE+NULLIF so we only ever FILL a missing value, never null
    # one out on a later sign-in that omits it. current_user has already
    # provisioned the row, so the UPDATE always targets an existing user.
    if req.name or req.email:
        try:
            await db_execute(
                """
                UPDATE users SET
                    name  = COALESCE(NULLIF($name, ''),  name),
                    email = COALESCE(NULLIF($email, ''), email),
                    updated_at = now()
                WHERE id = $id
                """,
                {"name": req.name, "email": req.email, "id": user.id},
            )
            logger.info(f"[auth] stored apple name/email for user_id={user.id}")
        except Exception as exc:
            logger.warning(f"[auth] apple name/email persist failed for user_id={user.id}: {exc}")

    # 2) Refresh token for revoke-on-delete.
    from core.apple import exchange_code

    try:
        refresh_token, client_id = await exchange_code(req.authorization_code)
        if refresh_token and client_id:
            await db_execute(
                "UPDATE users SET apple_refresh_token = $rt, apple_client_id = $cid WHERE id = $id",
                {"rt": refresh_token, "cid": client_id, "id": user.id},
            )
            logger.info(f"[auth] stored apple refresh token for user_id={user.id}")
        else:
            logger.warning(f"[auth] apple code exchange yielded no token for user_id={user.id}")
    except Exception as exc:
        logger.warning(f"[auth] /auth/apple failed for user_id={user.id}: {exc}")
    return None
