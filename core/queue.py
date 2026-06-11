"""
core/queue.py
Job queue with two switchable transports behind one interface:

  CURIA_QUEUE_BACKEND=postgres (default)  jobs table + FOR UPDATE SKIP LOCKED
  CURIA_QUEUE_BACKEND=sqs                 two SQS queues (interactive/background)

In BOTH modes every job writes a row to the `jobs` table. Under postgres that row
IS the queue; under sqs it is a write-only audit/history record (powers /jobs/{id}
polling, /admin/jobs, and enqueue-side dedup) while SQS carries the message.

Lanes (sqs only): the producer picks `lane="interactive"` (a user is watching) or
`lane="background"` (pipeline-chained; result announced by push). Defaults per type
in _DEFAULT_LANE. Queue URLs come from CURIA_SQS_INTERACTIVE_URL / CURIA_SQS_BACKGROUND_URL.

Retry model under sqs: a failed handler does NOT delete the message — the visibility
timeout (900s, > the 600s handler cap) expires and SQS redelivers; after
maxReceiveCount=3 the message lands in the lane's DLQ. PermanentError deletes
immediately. reap_stale() is postgres-only (visibility expiry replaces it).

Usage from API:
    job_id = await queue.enqueue("ingest", {...}, user_id=u, lane="interactive")

Usage from worker (sqs):
    job, receipt = await queue.sqs_receive(lane, worker_id) or (None, None)
    ... run handler ...
    await queue.sqs_ack(job.id, receipt, lane)
"""

from __future__ import annotations

import asyncio
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


# Lower number = higher priority (processed first). Postgres backend only —
# under sqs, priority is expressed by the lane split instead.
_JOB_PRIORITY: dict[str, int] = {
    "ingest": 1,
    "generate_ideas": 2,
    "generate_episode": 10,  # long-running; yields to ingest
}

# Default lane per job type (sqs backend). Producers override at the call site:
# user-watched actions pass lane="interactive"; pipeline chains pass "background".
_DEFAULT_LANE: dict[str, str] = {
    "ingest": "interactive",
    "generate_ideas": "background",
    "generate_episode": "background",
    "optimize": "background",
}

LANES = ("interactive", "background")


def get_queue_backend() -> str:
    return os.getenv("CURIA_QUEUE_BACKEND", "postgres")


def _queue_url(lane: str) -> str:
    env = "CURIA_SQS_INTERACTIVE_URL" if lane == "interactive" else "CURIA_SQS_BACKGROUND_URL"
    url = os.getenv(env)
    if not url:
        raise RuntimeError(f"CURIA_QUEUE_BACKEND=sqs but {env} is not set")
    return url


_sqs = None


def _sqs_client():
    global _sqs
    if _sqs is None:
        import boto3

        _sqs = boto3.client("sqs", region_name=os.getenv("CURIA_S3_REGION", "us-east-1"))
    return _sqs


async def enqueue(
    type: str,
    payload: dict[str, Any],
    *,
    user_id: Optional[str] = None,
    correlation_id: Optional[UUID] = None,
    max_attempts: int = 3,
    lane: Optional[str] = None,
) -> UUID:
    """
    Enqueue a job. Returns the new job id.
    Always writes the jobs row (queue under postgres; audit record under sqs),
    then publishes to the lane's SQS queue when the sqs backend is active.
    """
    lane = lane or _DEFAULT_LANE.get(type, "background")
    if lane not in LANES:
        raise ValueError(f"unknown lane {lane!r}")
    priority = _JOB_PRIORITY.get(type, 10)
    payload_json = json.dumps(payload)
    async with get_db() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO jobs (type, payload, user_id, correlation_id, max_attempts, priority)
            VALUES ($1, $2::jsonb, $3, $4, $5, $6)
            RETURNING id
            """,
            type,
            payload_json,
            user_id,
            correlation_id,
            max_attempts,
            priority,
        )
        job_id = row["id"]

    if get_queue_backend() == "sqs":
        body = json.dumps(
            {
                "job_id": str(job_id),
                "type": type,
                "payload": payload,
                "user_id": user_id,
                "correlation_id": str(correlation_id) if correlation_id else None,
            }
        )
        try:
            url = _queue_url(lane)
            await asyncio.to_thread(
                lambda: _sqs_client().send_message(QueueUrl=url, MessageBody=body)
            )
        except Exception as exc:
            # The row exists but no message will ever arrive — mark it failed so
            # enqueue-side dedup doesn't wedge on a job that can never run.
            await fail_permanently(job_id, f"sqs send_message failed: {exc}")
            raise

    logger.info(f"[queue] enqueued type={type} id={job_id} lane={lane} user_id={user_id}")
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
                ORDER BY priority ASC, created_at ASC
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


async def fail_permanently(job_id: UUID, error: str) -> None:
    """
    Immediately mark a job as permanently failed — no retry.
    Used for deterministic failures (404, validation reject, etc.) where retrying is pointless.
    """
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = 'failed',
                attempts = max_attempts,
                last_error = $2,
                locked_at = NULL,
                locked_by = NULL,
                updated_at = now()
            WHERE id = $1
            """,
            job_id,
            (error or "")[:2000],
        )
    logger.warning(f"[queue] fail_permanently id={job_id} error={error[:200]}")


# ---------------------------------------------------------------------------
# SQS consumer path (CURIA_QUEUE_BACKEND=sqs)
# ---------------------------------------------------------------------------


async def sqs_receive(lane: str, worker_id: Optional[str] = None) -> Optional[tuple[Job, str]]:
    """
    Long-poll the lane's queue for one message (waits up to the queue's 20s).
    On receipt: stamps the audit row 'running' and returns (Job, receipt_handle).
    Returns None when the queue is empty. attempts = SQS ApproximateReceiveCount.
    """
    worker_id = worker_id or default_worker_id()
    url = _queue_url(lane)
    resp = await asyncio.to_thread(
        lambda: _sqs_client().receive_message(
            QueueUrl=url,
            MaxNumberOfMessages=1,
            AttributeNames=["ApproximateReceiveCount"],
        )
    )
    messages = resp.get("Messages", [])
    if not messages:
        return None
    msg = messages[0]
    receipt = msg["ReceiptHandle"]
    try:
        body = json.loads(msg["Body"])
        job_id = UUID(body["job_id"])
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        # Unparseable message — delete it; there is no job row to recover.
        logger.error(f"[queue] sqs malformed message dropped: {exc}")
        await asyncio.to_thread(
            lambda: _sqs_client().delete_message(QueueUrl=url, ReceiptHandle=receipt)
        )
        return None
    attempts = int(msg.get("Attributes", {}).get("ApproximateReceiveCount", "1"))

    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = 'running', locked_at = now(), locked_by = $1,
                attempts = $2, updated_at = now()
            WHERE id = $3
            """,
            worker_id,
            attempts,
            job_id,
        )
    job = Job(
        id=job_id,
        type=body["type"],
        payload=body.get("payload") or {},
        user_id=body.get("user_id"),
        attempts=attempts,
        max_attempts=3,  # mirrors the queue's maxReceiveCount redrive policy
        correlation_id=UUID(body["correlation_id"]) if body.get("correlation_id") else None,
    )
    return job, receipt


async def sqs_ack(job_id: UUID, receipt_handle: str, lane: str) -> None:
    """Success: delete the message, stamp the audit row done."""
    await asyncio.to_thread(
        lambda: _sqs_client().delete_message(QueueUrl=_queue_url(lane), ReceiptHandle=receipt_handle)
    )
    await ack(job_id)


async def sqs_fail(job_id: UUID, error: str, *, attempts: int, final: bool) -> None:
    """
    Failure: do NOT delete — visibility expiry redelivers (or redrives to the DLQ
    after maxReceiveCount). Stamp the audit row so /jobs polling and dedup stay
    truthful: 'queued' while retries remain, 'failed' on the final attempt.
    """
    status = "failed" if final else "queued"
    async with get_db() as conn:
        await conn.execute(
            """
            UPDATE jobs
            SET status = $1, last_error = $2, locked_at = NULL, locked_by = NULL,
                updated_at = now()
            WHERE id = $3
            """,
            status,
            (error or "")[:2000],
            job_id,
        )
    logger.warning(f"[queue] sqs_fail id={job_id} attempts={attempts} final={final} error={error[:200]}")


async def sqs_fail_permanently(job_id: UUID, receipt_handle: str, lane: str, error: str) -> None:
    """Deterministic failure (404 etc.): delete the message — no retry, no DLQ."""
    await asyncio.to_thread(
        lambda: _sqs_client().delete_message(QueueUrl=_queue_url(lane), ReceiptHandle=receipt_handle)
    )
    await fail_permanently(job_id, error)


# ---------------------------------------------------------------------------
# Maintenance helpers (postgres backend only — under sqs, visibility expiry
# replaces reaping)
# ---------------------------------------------------------------------------


async def reap_stale(stale_after_minutes: int = 30) -> int:
    """
    Recover jobs whose worker died mid-execution. Anything 'running' for longer
    than `stale_after_minutes` gets requeued. Returns the number reaped.
    """
    async with get_db() as conn:
        rows = await conn.fetch(
            """
            UPDATE jobs
            SET status = 'queued',
                locked_at = NULL,
                locked_by = NULL,
                last_error = COALESCE(last_error || E'\n[reaped]', '[reaped]'),
                updated_at = now()
            WHERE status = 'running'
              AND locked_at < now() - ($1 || ' minutes')::interval
            RETURNING id
            """,
            str(stale_after_minutes),
        )
    reaped = len(rows)
    if reaped:
        logger.warning(f"[queue] reaped {reaped} stale jobs: {[r['id'] for r in rows]}")
    return reaped
