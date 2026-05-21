"""
core/db/connection.py
Postgres connection layer (asyncpg + pgvector).

This replaces the previous SurrealDB client. Public surface is intentionally similar:
    db_query(sql, params)        → list of dicts
    db_create(table, data)       → inserted dict (single row helper)
    db_update(record, data)      → updated dict
    db_select(table, record_id?) → list of dicts (optionally one row by id)
    db_delete(record)            → None
    db_upsert(table, id, data)   → upserted dict
    db_execute(sql, params)      → execute non-returning SQL

Differences from SurrealDB layer:
  - Uses a connection pool (asyncpg.Pool)
  - Named-param dict ($name) is supported via a small translator that converts
    to positional ($1, $2, ...). All existing call sites that pass dict params
    work unchanged.
  - Records are addressed by UUID, not "table:id" strings. Helpers strip an
    optional "table:" prefix on input for backward compatibility.
  - pgvector vectors are auto-registered on each connection via the pool init hook.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from uuid import UUID

import asyncpg
from dotenv import load_dotenv
from loguru import logger

# pgvector's asyncpg integration registers the vector type adapter
try:
    from pgvector.asyncpg import register_vector
except ImportError:  # pragma: no cover
    register_vector = None  # the dependency is in pyproject.toml; should always import


load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../../.env"))

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://curia:curia@localhost:5432/curia",
)

# Module-level pool — created lazily on first use, shared by all callers.
_pool: asyncpg.Pool | None = None


# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


_vector_registered_warned = False


async def _init_connection(conn: asyncpg.Connection) -> None:
    """
    Configure each new pool connection:
      - jsonb codec → dicts (so callers don't need to json.loads() row values)
      - json codec  → dicts (same — for the rare json column)
      - pgvector type adapter, tolerant of the extension not being installed yet
    """
    global _vector_registered_warned

    # JSONB / JSON: return dict (or list) directly instead of raw text.
    await conn.set_type_codec(
        "jsonb",
        encoder=lambda v: json.dumps(v) if not isinstance(v, str) else v,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )
    await conn.set_type_codec(
        "json",
        encoder=lambda v: json.dumps(v) if not isinstance(v, str) else v,
        decoder=json.loads,
        schema="pg_catalog",
        format="text",
    )

    if register_vector is None:
        return
    try:
        await register_vector(conn)
    except Exception as e:
        if not _vector_registered_warned:
            logger.warning(
                f"register_vector failed ({type(e).__name__}: {e}). "
                "The 'vector' extension is likely not installed yet — "
                "run `alembic upgrade head` and restart. "
                "Continuing; embedding queries will fail until this is resolved."
            )
            _vector_registered_warned = True


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        logger.info(f"Connecting to Postgres → {_safe_url(DATABASE_URL)}")
        import ssl as _ssl
        ssl_ctx = None
        is_local = "localhost" in DATABASE_URL or "127.0.0.1" in DATABASE_URL
        is_internal = ".railway.internal" in DATABASE_URL
        if not is_local and not is_internal:
            ssl_ctx = _ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = _ssl.CERT_NONE
        _pool = await asyncpg.create_pool(
            DATABASE_URL,
            min_size=0,
            max_size=10,
            init=_init_connection,
            ssl=ssl_ctx,
            timeout=10,
            command_timeout=10,
        )
        logger.info(f"Postgres pool created → {_safe_url(DATABASE_URL)}")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def _safe_url(url: str) -> str:
    """Mask password in URL for safe logging."""
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)


@asynccontextmanager
async def get_db() -> AsyncIterator[asyncpg.Connection]:
    """Context manager yielding an asyncpg connection from the pool."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


# ---------------------------------------------------------------------------
# Named-param translator — keeps the SurrealDB-style $name DX for callers
# while asyncpg uses $1, $2 positional internally.
# ---------------------------------------------------------------------------


_NAMED_PARAM_RE = re.compile(r"\$([a-zA-Z_]\w*)")


def _convert_named_params(sql: str, params: dict[str, Any] | None) -> tuple[str, list[Any]]:
    """
    Convert $name placeholders to $1, $2 positional, returning (sql, positional_list).
    If params is None or sql contains only positional placeholders, returns sql unchanged.
    """
    if not params:
        return sql, []

    name_to_index: dict[str, int] = {}
    positional: list[Any] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in name_to_index:
            if name not in params:
                raise KeyError(f"SQL references ${name} but params has no such key")
            name_to_index[name] = len(positional) + 1
            positional.append(params[name])
        return f"${name_to_index[name]}"

    converted = _NAMED_PARAM_RE.sub(_replace, sql)
    return converted, positional


# ---------------------------------------------------------------------------
# Query helpers — preserve the surface area of the old surreal layer
# ---------------------------------------------------------------------------


async def db_query(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """SELECT → list of dicts. Uses $name params for compatibility with old call sites."""
    sql, positional = _convert_named_params(sql, params)
    async with get_db() as conn:
        rows = await conn.fetch(sql, *positional)
        return [dict(r) for r in rows]


async def db_execute(sql: str, params: dict[str, Any] | None = None) -> str:
    """Execute non-returning SQL (INSERT/UPDATE/DELETE without RETURNING)."""
    sql, positional = _convert_named_params(sql, params)
    async with get_db() as conn:
        return await conn.execute(sql, *positional)


async def db_fetchrow(sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    sql, positional = _convert_named_params(sql, params)
    async with get_db() as conn:
        row = await conn.fetchrow(sql, *positional)
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Convenience CRUD — mirror the old API
# ---------------------------------------------------------------------------


def _strip_record_prefix(rec: str | UUID) -> str:
    """'source:abc-123' → 'abc-123'.   Plain UUID strings pass through."""
    if isinstance(rec, UUID):
        return str(rec)
    s = str(rec)
    if ":" in s:
        return s.split(":", 1)[1]
    return s


def _split_record(rec: str) -> tuple[str, str]:
    """'source:abc' → ('source', 'abc'). Bare ids return ('', id)."""
    if ":" in rec:
        table, _, rid = rec.partition(":")
        return table, rid
    return "", rec


async def db_create(table: str, data: dict[str, Any]) -> dict[str, Any]:
    """INSERT INTO {table} (...) VALUES (...) RETURNING * — single row helper."""
    cols = list(data.keys())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    col_list = ", ".join(cols)
    sql = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING *"
    async with get_db() as conn:
        row = await conn.fetchrow(sql, *data.values())
        return dict(row) if row else {}


async def db_select(table: str, record_id: str | UUID | None = None) -> list[dict[str, Any]] | dict[str, Any] | None:
    if record_id is not None:
        rid = _strip_record_prefix(record_id)
        async with get_db() as conn:
            row = await conn.fetchrow(f"SELECT * FROM {table} WHERE id = $1", rid)
            return dict(row) if row else None
    async with get_db() as conn:
        rows = await conn.fetch(f"SELECT * FROM {table}")
        return [dict(r) for r in rows]


async def db_update(record: str, data: dict[str, Any]) -> dict[str, Any]:
    """UPDATE {table} SET ... WHERE id = $id RETURNING *.

    `record` is "table:id" (Surreal style — backward compat) or "table" + uuid arg.
    For old call sites that pass 'source:abc-123' this still works.
    """
    table, rid = _split_record(record)
    if not table:
        raise ValueError("db_update requires 'table:id' record string")
    set_clauses = []
    values = []
    for i, (k, v) in enumerate(data.items(), start=2):
        set_clauses.append(f"{k} = ${i}")
        values.append(v)
    sql = f"UPDATE {table} SET {', '.join(set_clauses)} WHERE id = $1 RETURNING *"
    async with get_db() as conn:
        row = await conn.fetchrow(sql, rid, *values)
        return dict(row) if row else {}


async def db_delete(record: str) -> None:
    table, rid = _split_record(record)
    if not table:
        raise ValueError("db_delete requires 'table:id' record string")
    async with get_db() as conn:
        await conn.execute(f"DELETE FROM {table} WHERE id = $1", rid)


async def db_upsert(table: str, record_id: str, data: dict[str, Any]) -> dict[str, Any]:
    rid = _strip_record_prefix(record_id)
    cols = ["id"] + list(data.keys())
    values = [rid] + list(data.values())
    placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
    update_clauses = ", ".join(f"{c} = EXCLUDED.{c}" for c in data.keys())
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT (id) DO UPDATE SET {update_clauses} RETURNING *"
    )
    async with get_db() as conn:
        row = await conn.fetchrow(sql, *values)
        return dict(row) if row else {}
