"""
asyncpg connection pool for the harness's own pre-opt/per-user-brief cache
schema (`harness.*` tables — see db/001_schema.sql).

Separate, unrelated concern from the retired production pipeline's
`legacy/db_service.py`, which talked to the old `public.daily_briefs` table
via its own bare `asyncpg.connect` calls against `os.environ["DATABASE_URL"]`.
This module is never imported by anything under `legacy/`, and vice versa.

Every query in cache_service.py schema-qualifies table/type names as
`harness.<name>` explicitly rather than relying on a pooled connection's
`search_path` — a `SET search_path` issued via asyncpg's pool `init` hook
was found (empirically, in this environment) to not reliably survive
connection reuse across separate pool.fetch*() calls, silently reverting to
`public` and raising `UndefinedTableError` on the second call onward.
Explicit qualification sidesteps that entirely and is more robust regardless
of pool/driver session-reset behavior.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from app.config import settings

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.DATABASE_URL,
            min_size=1,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
