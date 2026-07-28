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

from fastapi import Depends, Header, HTTPException, Query, status
from loguru import logger

from core.db.connection import db_execute, db_fetchrow


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

    # Try Firebase first if initialized
    from core.firebase import is_initialized, verify_id_token
    if is_initialized():
        try:
            decoded = verify_id_token(token)
            firebase_uid = decoded["uid"]
            email = decoded.get("email")
            name = decoded.get("name")

            # Known user by firebase_uid → done.
            row = await db_fetchrow(
                "SELECT id, role FROM users WHERE firebase_uid = $uid",
                {"uid": firebase_uid},
            )
            if row:
                return CurrentUser(id=str(row["id"]), role=str(row.get("role") or "user"))

            # New firebase_uid. The same person may already have a row under this
            # email with a DIFFERENT uid — Firebase mints a fresh uid after account
            # deletion or Apple/Google de-authorization. Re-link that row to the new
            # uid instead of failing on the users_email unique constraint (which was
            # caught and surfaced as 401 → ghost user).
            if email:
                existing = await db_fetchrow(
                    "SELECT id, role FROM users WHERE email = $email",
                    {"email": email},
                )
                if existing:
                    await db_execute(
                        """
                        UPDATE users SET firebase_uid = $uid,
                            name = COALESCE($name, name), updated_at = now()
                        WHERE id = $id
                        """,
                        {"uid": firebase_uid, "name": name, "id": str(existing["id"])},
                    )
                    return CurrentUser(id=str(existing["id"]), role=str(existing.get("role") or "user"))

            # Genuinely new account. Apple only sends email/name on the FIRST
            # authorization (and "Hide My Email" users may send neither, just a
            # relay or nothing) — so on the conflict path COALESCE keeps any
            # value already stored instead of nulling it out on a later sign-in.
            import uuid
            user_id = str(uuid.uuid4())
            await db_execute(
                """
                INSERT INTO users (id, email, name, firebase_uid, role)
                VALUES ($id, $email, $name, $uid, 'user')
                ON CONFLICT (firebase_uid) DO UPDATE SET
                    email = COALESCE(EXCLUDED.email, users.email),
                    name = COALESCE(EXCLUDED.name, users.name),
                    updated_at = now()
                """,
                {"id": user_id, "email": email, "name": name, "uid": firebase_uid},
            )
            return CurrentUser(id=user_id, role="user")
        except Exception as exc:
            logger.warning(f"[auth] firebase provisioning failed: {type(exc).__name__}: {exc}")
            pass  # fall through to api_token lookup

    # Legacy fallback: lookup by api_token
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


async def audio_user_id(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
    token: Optional[str] = Query(default=None),
) -> str:
    """Like current_user_id but also accepts ?token= — native audio players
    (expo-av / ExoPlayer on Android, RNTP generally) can't set custom headers
    on the URL they're handed, so the local-storage-backend audio routes
    (GET /episodes/{id}/audio, GET /brief/today/audio) fall back to a query
    param for that one request. Shared here rather than duplicated per route
    now that a second route needs it."""
    auth_header = authorization or (f"Bearer {token}" if token else None)
    user = await _resolve_token(auth_header)
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
