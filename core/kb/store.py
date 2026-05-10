"""
core/kb/store.py
Read/write helpers around the users.user_kb JSONB column.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from core.db.connection import db_execute, db_fetchrow

from .schema import UserKB


def empty_kb() -> UserKB:
    """A KB with all defaults — useful for new users before onboarding."""
    return UserKB()


def _coerce_kb_payload(raw: Any) -> dict:
    """asyncpg may return JSONB as a dict OR a string depending on codec setup.
    Normalize to a dict so Pydantic can parse it cleanly.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(f"Could not parse user_kb string as JSON; treating as empty.")
            return {}
    return {}


async def load_kb(user_id: str) -> UserKB:
    """Load the user's KB. Returns an empty KB if user has none yet."""
    row = await db_fetchrow(
        "SELECT user_kb FROM users WHERE id = $id",
        {"id": user_id},
    )
    if not row:
        raise ValueError(f"user {user_id} not found")
    payload = _coerce_kb_payload(row.get("user_kb"))
    if not payload:
        return empty_kb()
    try:
        return UserKB.model_validate(payload)
    except Exception as e:
        # KB shape changed since this row was written? Don't block reads;
        # warn and return whatever subset is valid plus defaults.
        logger.warning(
            f"user_kb for {user_id} failed validation ({e}); "
            f"returning empty defaults. Re-run onboarding to repopulate."
        )
        return empty_kb()


async def save_kb(user_id: str, kb: UserKB) -> None:
    """Validate + persist. Raises if validation fails."""
    payload = kb.model_dump()
    await db_execute(
        """
        UPDATE users
        SET user_kb = $payload::jsonb,
            updated_at = now()
        WHERE id = $id
        """,
        {"id": user_id, "payload": json.dumps(payload)},
    )
    logger.info(f"[kb] saved KB for user {user_id} (version={kb.version})")
