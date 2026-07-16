"""
asyncpg helpers for reading/writing daily_briefs table.
"""

from __future__ import annotations

import json
import os
from datetime import date
from typing import Any, Optional

import asyncpg


async def _get_conn() -> asyncpg.Connection:
    return await asyncpg.connect(os.environ["DATABASE_URL"])


def _to_date(brief_date: str) -> date:
    return date.fromisoformat(brief_date)


async def brief_set_generating(user_id: str, brief_date: str) -> bool:
    """
    INSERT daily_briefs row with status=generating.
    Returns True if inserted, False if row already exists (idempotent).
    """
    conn = await _get_conn()
    try:
        result = await conn.execute(
            """
            INSERT INTO daily_briefs (user_id, date, status)
            VALUES ($1::text, $2, 'generating')
            ON CONFLICT (user_id, date) DO NOTHING
            """,
            user_id,
            _to_date(brief_date),
        )
        return result == "INSERT 0 1"
    finally:
        await conn.close()


async def brief_set_ready(
    user_id: str,
    brief_date: str,
    transcript: str,
    audio_url: str,
    audio_duration_seconds: Optional[float],
    articles: Any,
    outline: Any,
) -> None:
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE daily_briefs
            SET status = 'ready',
                transcript = $3,
                audio_url = $4,
                audio_duration_seconds = $5,
                articles_json = $6::jsonb,
                outline_json = $7::jsonb,
                updated_at = now()
            WHERE user_id = $1::text AND date = $2
            """,
            user_id,
            _to_date(brief_date),
            transcript,
            audio_url,
            audio_duration_seconds,
            json.dumps(articles),
            json.dumps(outline),
        )
    finally:
        await conn.close()


async def brief_set_failed(user_id: str, brief_date: str, error: str) -> None:
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE daily_briefs
            SET status = 'failed',
                error_text = $3,
                updated_at = now()
            WHERE user_id = $1::text AND date = $2
            """,
            user_id,
            _to_date(brief_date),
            error[:2000],
        )
    finally:
        await conn.close()


async def brief_mark_pn_sent(user_id: str, brief_date: str) -> None:
    conn = await _get_conn()
    try:
        await conn.execute(
            """
            UPDATE daily_briefs SET pn_sent = TRUE
            WHERE user_id = $1::text AND date = $2
            """,
            user_id,
            _to_date(brief_date),
        )
    finally:
        await conn.close()
