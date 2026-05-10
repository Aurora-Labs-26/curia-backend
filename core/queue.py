"""
core/queue.py
Postgres-backed job queue. SQS-shaped — the API used here intentionally mirrors
SQS so the v3 AWS migration is `boto3.client('sqs')` substitution.

Public API:
    enqueue(type, payload, user_id?, correlation_id?, max_attempts=3) -> job_id
    dequeue(worker_id) -> Job | None       (atomic FOR UPDATE SKIP LOCKED)
    ack(job_id)                            mark done
    fail(job_id, error)                    bump attempts; requeue if attempts < max,
                                           else mark failed

Schema lives in jobs table (see alembic 0002_users_jobs_and_status).

Usage from API:
    job_id = await queue.enqueue("ingest", {"source_id": ..., "url": ...}, user_id=u)

Usage from worker:
    job = await queue.dequeue(worker_id="worker-1")
    if job: await handler(job.payload); await queue.ack(job.id)
"""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Optional
from uuid import UUID

from loguru import logger

from .db.connection import get_db, get_pool


# ---------------------------------------------------------------------------
# Job dataclass
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: UUID
    type: str
    payload: dict
    user_id: Optional[str]
    attempts: int
    max_attempts: int
    correlation_id: Optional[UUID]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def default_worker_id() -> str:
    """Stable identifier for this worker process — for debugging stuck jobs."""
    return f"{socket.gethostname()}-{os.getpid()}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def enqueue(
    type: str,
    payload: dict[str, Any],
    *,
    user_id: Optional[str] = None,
    correlation_id: Optional[UUID] = None,
    max_attempts: int = 3,
) -> UUID:
    """
    Enqueue a job. Returns the new job id.
    Payload is JSON-serialized.
    """
    payload_json = json.dumps(payload)
    async with get_db() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO jobs (type, payload, user_id, correlation_id, max_attempts)
            VALUES ($1, $2::jsonb, $3, $4, $5)
            RETURNING id
            """,
            type,
            payload_json,
            user_id,
            correlation_id,
            max_attempts,
        )
        job_id = row["id"]
    logger.info(f"[queue] enqueued type={type} id={job_id} user_id={user_id}")
    return job_id


async def dequeue(worker_id: Optional[str] = None) -> Optional[Job]:
    """
    Atomically claim the next queued job for this worker.
    Uses SELECT ... FOR UPDATE SKIP LOCKED so multiple workers can run concurrently
    without seeing each other's in-flight rows.
    Returns None if the queue is empty.
    """
    worker_id = worker_id or default_worker_id()
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, type, payload, user_id, attempts, max_attempts, correlation_id
                FROM jobs
                WHERE status = 'queued' AND attempts < max_attempts
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
                """
            )
            if not row:
                return None
            await conn.execute(
                """
                UPDATE jobs
                SET status = 'running',
                    locked_at = now(),
                    locked_by = $1,
                    attempts = attempts + 1,
                    updated_at = now()
                WHERE id = $2
                """,
                worker_id,
                row["id"],
            )
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)
    return Job(
        id=row["id"],
        type=row["type"],
        payload=payload,
        user_id=row["user_id"],
        attempts=row["attempts"] + 1,  # the row reflects post-increment
        max_attempts=row["max_attempts"],
        correlation_id=row["correlation_id"],
    )


async def ack(job_id: UUID) -> None:
    """Mark a job as successfully completed."""
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = 'done',
                locked_at = NULL,
                locked_by = NULL,
                last_error = NULL,
                updated_at = now()
            WHERE id = $1
            """,
            job_id,
        )
    logger.info(f"[queue] ack id={job_id}")


async def fail(job_id: UUID, error: str) -> None:
    """
    Mark a job as failed for this attempt.
    If attempts < max_attempts, the job is requeued for retry.
    Otherwise it's permanently marked failed.
    """
    async with get_db() as conn:
        # Single statement decides retry vs permanent failure based on attempts vs max_attempts.
        await conn.execute(
            """
            UPDATE jobs
            SET status = CASE
                    WHEN attempts >= max_attempts THEN 'failed'
                    ELSE 'queued'
                END,
                last_error = $2,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = now()
            WHERE id = $1
            """,
            job_id,
            (error or "")[:2000],
        )
    logger.warning(f"[queue] fail id={job_id} error={error[:200]}")


# ---------------------------------------------------------------------------
# Maintenance helpers (optional, called from admin scripts/cron)
# ---------------------------------------------------------------------------


async def reap_stale(stale_after_minutes: int = 30) -> int:
    """
    Recover jobs whose worker died mid-execution. Anything 'running' for longer
    than `stale_after_minutes` gets requeued. Returns the number reaped.

    Run via cron / a background task in long-deployments. Fine to call manually
    during local dev.
    """
    async with get_db() as conn:
        row = await conn.fetchrow(
            """
            UPDATE jobs
            SET status = 'queued',
                locked_at = NULL,
                locked_by = NULL,
                last_error = COALESCE(last_error || E'\n[reaped]', '[reaped]'),
                updated_at = now()
            WHERE status = 'running'
              AND locked_at < now() - ($1 || ' minutes')::interval
            RETURNING (SELECT COUNT(*) AS c FROM jobs WHERE FALSE)
            """,
            str(stale_after_minutes),
        )
    return 0 if not row else int(row.get("c") or 0)
