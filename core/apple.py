"""
core/apple.py
Sign in with Apple — server-to-server token exchange + revocation.

Required by App Store Guideline 5.1.1(v): apps offering account deletion AND
Sign in with Apple must REVOKE the Apple token on deletion. Revocation also fixes
the "ghost user" symptom — Apple only returns name/email on the *first*
authorization, and won't resend them until the app is de-authorized; revoking on
delete makes the next sign-in a fresh first-time grant.

Flow:
  at sign-in   : frontend sends the one-time `authorizationCode` → exchange_code()
                 trades it for a long-lived refresh_token (stored on the user).
  at delete    : revoke(refresh_token) tells Apple to forget the app for that user.

Config (env / Secrets Manager):
  APPLE_TEAM_ID            10-char Apple Team ID
  APPLE_KEY_ID             10-char Key ID of the "Sign in with Apple" key
  APPLE_PRIVATE_KEY        the .p8 private key contents (PEM, with header/footer)
  APPLE_CLIENT_IDS         comma-separated bundle ids to try as client_id
                           (e.g. "com.bhabani1999.curia,com.bhabani1999.curia.dev")
"""

from __future__ import annotations

import os
import time

import httpx
from loguru import logger

_TOKEN_URL = "https://appleid.apple.com/auth/token"
_REVOKE_URL = "https://appleid.apple.com/auth/revoke"
_AUD = "https://appleid.apple.com"


def is_configured() -> bool:
    return bool(
        os.getenv("APPLE_TEAM_ID")
        and os.getenv("APPLE_KEY_ID")
        and os.getenv("APPLE_PRIVATE_KEY")
        and _client_ids()
    )


def _client_ids() -> list[str]:
    raw = os.getenv("APPLE_CLIENT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def _client_secret(client_id: str) -> str:
    """Build the short-lived ES256 JWT Apple requires as the OAuth client secret."""
    import jwt  # PyJWT (with cryptography) — signs ES256 from the .p8

    now = int(time.time())
    payload = {
        "iss": os.getenv("APPLE_TEAM_ID"),
        "iat": now,
        "exp": now + 600,  # max 6 months; 10 min is plenty for a single call
        "aud": _AUD,
        "sub": client_id,
    }
    private_key = os.getenv("APPLE_PRIVATE_KEY", "").replace("\\n", "\n")
    return jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers={"kid": os.getenv("APPLE_KEY_ID")},
    )


async def exchange_code(authorization_code: str) -> tuple[str | None, str | None]:
    """
    Exchange a one-time authorization code for a refresh token.
    Tries each configured client_id (dev/prod bundle ids) until one works.
    Returns (refresh_token, client_id) or (None, None) — best-effort, never raises.
    """
    if not is_configured() or not authorization_code:
        return None, None
    async with httpx.AsyncClient(timeout=15) as client:
        for client_id in _client_ids():
            try:
                resp = await client.post(
                    _TOKEN_URL,
                    data={
                        "client_id": client_id,
                        "client_secret": _client_secret(client_id),
                        "code": authorization_code,
                        "grant_type": "authorization_code",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code == 200:
                    rt = resp.json().get("refresh_token")
                    if rt:
                        logger.info(f"[apple] exchanged code → refresh_token (client_id={client_id})")
                        return rt, client_id
                else:
                    logger.debug(f"[apple] exchange client_id={client_id} → {resp.status_code} {resp.text[:120]}")
            except Exception as exc:
                logger.warning(f"[apple] exchange error (client_id={client_id}): {exc}")
    logger.warning("[apple] code exchange failed for all client_ids")
    return None, None


async def revoke(refresh_token: str, client_id: str) -> bool:
    """Revoke a user's Apple token on account deletion. Best-effort, never raises."""
    if not is_configured() or not refresh_token or not client_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _REVOKE_URL,
                data={
                    "client_id": client_id,
                    "client_secret": _client_secret(client_id),
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        # Apple returns 200 with empty body on success.
        if resp.status_code == 200:
            logger.info(f"[apple] revoked token (client_id={client_id})")
            return True
        logger.warning(f"[apple] revoke → {resp.status_code} {resp.text[:120]}")
        return False
    except Exception as exc:
        logger.warning(f"[apple] revoke error: {exc}")
        return False
