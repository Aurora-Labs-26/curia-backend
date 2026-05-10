"""
optimization/guidelines
=======================
Per-task company guidelines. Quality floor every output must meet regardless of
user preferences.

Storage: DB-first (table optimization_guidelines), with Python-module fallback.
QA can edit via the API; the Python constants below stay as the safety net for:
  - first boot before any DB row exists
  - migrations seeding placeholders ('__SEED_FROM_PYTHON__' marker is replaced)
  - unit tests that don't touch the DB

Public API:
    get_guidelines(task: str) -> str          (sync; uses cached DB value or fallback)
    get_guidelines_async(task: str) -> str    (async; refreshes DB cache)
    list_tasks() -> list[str]
    GUIDELINES_VERSION                         bumped when the in-code defaults change
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from loguru import logger

from .transcript import TRANSCRIPT_GUIDELINES_V1
from .outline import OUTLINE_GUIDELINES_V1


GUIDELINES_VERSION = 1   # bump when the Python defaults change

# Python-module fallback — used if DB row is missing or contains the seed marker.
_FALLBACKS: dict[str, str] = {
    "transcript": TRANSCRIPT_GUIDELINES_V1,
    "outline": OUTLINE_GUIDELINES_V1,
}

# In-memory cache: task → (body, version, fetched_at). Refreshed on TTL or invalidation.
_CACHE: dict[str, tuple[str, int, float]] = {}
_CACHE_TTL_SECONDS = 30


def _cache_get(task: str) -> Optional[str]:
    entry = _CACHE.get(task)
    if entry is None:
        return None
    body, _version, fetched_at = entry
    if time.time() - fetched_at > _CACHE_TTL_SECONDS:
        return None
    return body


def _cache_set(task: str, body: str, version: int) -> None:
    _CACHE[task] = (body, version, time.time())


def invalidate_cache(task: Optional[str] = None) -> None:
    """Clear cache for a single task or all tasks (called by PUT /admin/guidelines/{task})."""
    if task:
        _CACHE.pop(task, None)
    else:
        _CACHE.clear()


# ---------------------------------------------------------------------------
# DB-backed read with fallback
# ---------------------------------------------------------------------------


async def _read_db(task: str) -> Optional[tuple[str, int]]:
    """Read body+version from DB. Returns None if no row or seed-placeholder."""
    # Imported lazily to avoid making this module require a configured DB at import time.
    from core.db.connection import db_fetchrow

    try:
        row = await db_fetchrow(
            "SELECT body, version FROM optimization_guidelines WHERE task = $task",
            {"task": task},
        )
    except Exception as e:
        logger.warning(f"[guidelines] DB read failed for task={task}: {e}; using fallback")
        return None

    if not row:
        return None
    body = (row.get("body") or "").strip()
    if not body or body == "__SEED_FROM_PYTHON__":
        return None
    return body, int(row.get("version") or 1)


async def get_guidelines_async(task: str) -> str:
    """Async fetch — DB first, falls back to Python module value."""
    cached = _cache_get(task)
    if cached is not None:
        return cached

    db = await _read_db(task)
    if db is not None:
        body, version = db
        _cache_set(task, body, version)
        return body

    fallback = _FALLBACKS.get(task)
    if fallback is None:
        raise KeyError(
            f"No guidelines defined for task '{task}'. "
            f"Available: {sorted(_FALLBACKS)}"
        )
    _cache_set(task, fallback, 0)
    return fallback


def get_guidelines(task: str) -> str:
    """
    Sync facade. Fast path uses cache; cold path runs the async DB read on a
    nested loop. Most callers should prefer get_guidelines_async() in async code.
    """
    cached = _cache_get(task)
    if cached is not None:
        return cached

    try:
        return asyncio.run(get_guidelines_async(task))
    except RuntimeError:
        # Already inside a running event loop — caller must use the async variant
        # or accept the Python fallback.
        fallback = _FALLBACKS.get(task)
        if fallback is None:
            raise KeyError(f"No guidelines for task '{task}'")
        return fallback


def list_tasks() -> list[str]:
    return sorted(_FALLBACKS.keys())


__all__ = [
    "get_guidelines",
    "get_guidelines_async",
    "invalidate_cache",
    "list_tasks",
    "GUIDELINES_VERSION",
]
