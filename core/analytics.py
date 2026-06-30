"""
core/analytics.py
PostHog backend analytics — fire-and-forget event capture.

distinct_id is always firebase_uid (not the internal user UUID) so backend events
join with the frontend's existing PostHog identity. Falls back to the internal
user_id only when no firebase_uid is on file (legacy api_token-only accounts).

No-ops entirely when POSTHOG_PROJECT_TOKEN is unset — safe to call from anywhere,
including local dev / tests, without configuring PostHog.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from dotenv import load_dotenv
from loguru import logger

from .db.connection import db_fetchrow

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

_client = None
_client_initialized = False


def _get_client():
    global _client, _client_initialized
    if _client_initialized:
        return _client
    _client_initialized = True

    token = os.getenv("POSTHOG_PROJECT_TOKEN")
    if not token:
        logger.info("[analytics] POSTHOG_PROJECT_TOKEN not set — analytics disabled")
        return None

    import posthog
    _client = posthog.Posthog(
        project_api_key=token,
        host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
    )
    return _client


async def resolve_distinct_id(user_id: str) -> str:
    """firebase_uid if the user has one on file, else the internal user_id."""
    row = await db_fetchrow("SELECT firebase_uid FROM users WHERE id = $id", {"id": user_id})
    firebase_uid = row.get("firebase_uid") if row else None
    return firebase_uid or user_id


def capture(distinct_id: str, event: str, properties: Optional[dict[str, Any]] = None) -> None:
    client = _get_client()
    if not client:
        return
    try:
        client.capture(distinct_id=distinct_id, event=event, properties=properties or {})
    except Exception as e:
        logger.warning(f"[analytics] capture failed for event={event}: {e}")


async def track(user_id: str, event: str, properties: Optional[dict[str, Any]] = None) -> None:
    """Resolve user_id → distinct_id (firebase_uid) and fire the event. Never raises."""
    try:
        distinct_id = await resolve_distinct_id(user_id)
        capture(distinct_id, event, properties)
    except Exception as e:
        logger.warning(f"[analytics] track failed for event={event}: {e}")
