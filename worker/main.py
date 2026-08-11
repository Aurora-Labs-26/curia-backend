"""
worker/main.py
Long-running consumer loop. Polls the Postgres jobs queue, dispatches to the
matching handler, acks on success / fails on exception.

Run:
    python -m worker.main

In docker-compose this is the `worker` service's command.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import time
import traceback

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from loguru import logger

from core.errors import PermanentError
from core.queue import (
    ack,
    default_worker_id,
    dequeue,
    fail,
    fail_permanently,
    get_queue_backend,
    reap_stale,
    sqs_ack,
    sqs_fail,
    sqs_fail_permanently,
    sqs_receive,
)
from worker.handlers import HANDLERS

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


# Polling interval when the queue is empty. Trades latency for DB load.
# (postgres backend only — under sqs, long polling waits server-side instead)
EMPTY_QUEUE_SLEEP_SECONDS = float(os.getenv("CURIA_WORKER_POLL_SECONDS", "2"))

# Which SQS lane this worker consumes (sqs backend only). Each ECS worker
# service is pinned to one lane: interactive (user-watched) or background.
WORKER_LANE = os.getenv("CURIA_WORKER_LANE", "background")

# Hard timeout for any single job handler. Kills the handler if it exceeds this.
HANDLER_TIMEOUT_SECONDS = int(os.getenv("CURIA_HANDLER_TIMEOUT_SECONDS", "600"))

# Safety-net: soft-hide failed sources older than this, regardless of whether the
# user ever opened the app (the client clears on cold-start, this is the backstop).
FAILED_PURGE_DAYS = int(os.getenv("CURIA_FAILED_PURGE_DAYS", "7"))
PURGE_INTERVAL_SECONDS = 3600  # run the purge at most hourly
_last_purge_at: float = 0.0

REAP_INTERVAL_SECONDS = 300  # reap stale jobs at most every 5 minutes
_last_reap_at: float = 0.0


_shutdown = asyncio.Event()


async def _maybe_purge_failed_sources() -> None:
    """Throttled backstop: soft-hide failed sources older than FAILED_PURGE_DAYS."""
    global _last_purge_at
    now = time.time()
    if now - _last_purge_at < PURGE_INTERVAL_SECONDS:
        return
    _last_purge_at = now
    from core.db.connection import db_query
    try:
        rows = await db_query(
            f"""
            UPDATE source SET hidden = true, updated_at = now()
            WHERE status = 'failed' AND hidden = false
              AND created_at < now() - interval '{FAILED_PURGE_DAYS} days'
            RETURNING id
            """,
            {},
        )
        if rows:
            logger.info(f"[worker] safety-net purge: soft-hid {len(rows)} stale failed sources")
    except Exception as e:
        logger.warning(f"[worker] safety-net purge failed: {e}")


async def _maybe_reap_stale() -> None:
    """Throttled: requeue jobs stuck in 'running' longer than 30 minutes."""
    global _last_reap_at
    now = time.time()
    if now - _last_reap_at < REAP_INTERVAL_SECONDS:
        return
    _last_reap_at = now
    try:
        reaped = await reap_stale(stale_after_minutes=30)
        if reaped:
            logger.info(f"[worker] reaped {reaped} stale jobs")
    except Exception as e:
        logger.warning(f"[worker] reap_stale failed: {e}")


def _install_signal_handlers() -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: _shutdown.set())


async def _execute(job) -> tuple[str, str]:
    """
    Run a job's handler. Returns (outcome, error) where outcome is one of
    "ok" | "no_handler" | "timeout" | "permanent" | "error". Transport-agnostic —
    callers map the outcome to their backend's ack/fail semantics.
    """
    from core.logging import worker_logger

    handler = HANDLERS.get(job.type)
    if handler is None:
        logger.error(f"[worker] no handler for job.type={job.type} id={job.id}; failing.")
        worker_logger.error(f"JOB_NO_HANDLER | type={job.type} id={job.id}")
        return "no_handler", f"unknown job type {job.type}"

    start = time.time()
    worker_logger.info(
        f"JOB_START | type={job.type} id={job.id} "
        f"attempt={job.attempts}/{job.max_attempts} "
        f"payload_keys={list(job.payload.keys()) if job.payload else []}"
    )

    try:
        logger.info(f"[worker] running type={job.type} id={job.id} attempt={job.attempts}/{job.max_attempts}")
        if isinstance(job.payload, dict):
            job.payload["__attempt__"] = job.attempts
            job.payload["__max_attempts__"] = job.max_attempts
        await asyncio.wait_for(handler(job.payload), timeout=HANDLER_TIMEOUT_SECONDS)
        elapsed = time.time() - start
        worker_logger.info(f"JOB_SUCCESS | type={job.type} id={job.id} duration={elapsed:.2f}s")
        return "ok", ""
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        logger.error(f"[worker] id={job.id} type={job.type} timed out after {HANDLER_TIMEOUT_SECONDS}s")
        worker_logger.error(
            f"JOB_TIMEOUT | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s timeout={HANDLER_TIMEOUT_SECONDS}s"
        )
        return "timeout", f"handler timed out after {HANDLER_TIMEOUT_SECONDS}s"
    except PermanentError as exc:
        elapsed = time.time() - start
        tb = traceback.format_exc()
        logger.warning(f"[worker] id={job.id} type={job.type} permanent failure (no retry): {exc}")
        worker_logger.error(
            f"JOB_PERMANENT_FAIL | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s error={exc}"
        )
        return "permanent", f"{exc}\n{tb}"
    except Exception as exc:
        elapsed = time.time() - start
        tb = traceback.format_exc()
        logger.error(f"[worker] id={job.id} type={job.type} failed:\n{tb}")
        worker_logger.error(
            f"JOB_FAIL | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s error={exc}"
        )
        return "error", f"{exc}\n{tb}"


async def _process_one(worker_id: str) -> bool:
    """Postgres backend: claim one job via SKIP LOCKED, run it, ack/fail the row."""
    job = await dequeue(worker_id=worker_id)
    if job is None:
        return False

    outcome, error = await _execute(job)
    if outcome == "ok":
        await ack(job.id)
        logger.info(f"[worker] acked id={job.id}")
    elif outcome == "permanent":
        await fail_permanently(job.id, error)
    else:  # no_handler / timeout / error → retry while attempts remain
        await fail(job.id, error)
    return True


async def _process_one_sqs(worker_id: str) -> bool:
    """
    SQS backend: long-poll this worker's lane for one message, run it.
    Success/permanent → delete the message. Retryable failure → leave it; the
    visibility timeout redelivers, and maxReceiveCount redrives to the DLQ.
    """
    received = await sqs_receive(WORKER_LANE, worker_id=worker_id)
    if received is None:
        return False
    job, receipt = received

    outcome, error = await _execute(job)
    if outcome == "ok":
        await sqs_ack(job.id, receipt, WORKER_LANE)
        logger.info(f"[worker] acked id={job.id} lane={WORKER_LANE}")
    elif outcome in ("permanent", "no_handler"):
        await sqs_fail_permanently(job.id, receipt, WORKER_LANE, error)
    else:  # timeout / error → stamp audit row; message retries via visibility expiry
        final = job.attempts >= job.max_attempts
        await sqs_fail(job.id, error, attempts=job.attempts, final=final)
    return True


# Cross-task exclusivity for scheduler jobs: the background service
# autoscales (1-6 tasks) and EVERY task runs APScheduler — without a lock,
# N tasks means N concurrent Pre-Opt runs (Sonnet cost xN) every 6 hours.
# Postgres advisory locks are session-scoped: the winning task holds one
# connection for the job's duration; losers skip silently. (Dispatch is
# row-guarded and safe either way — locked here anyway to avoid N pollers.)
LOCK_BRIEF_PREOPT = 0x42524A01
LOCK_BRIEF_DISPATCH = 0x42524A02


async def _run_exclusive(lock_id: int, name: str, job) -> None:
    from core.db.connection import get_pool
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            got = await conn.fetchval("SELECT pg_try_advisory_lock($1)", lock_id)
            if not got:
                logger.info(f"[worker] {name}: another task holds the lock — skipping")
                return
            try:
                await job()
            finally:
                await conn.fetchval("SELECT pg_advisory_unlock($1)", lock_id)
    except Exception as e:
        logger.error(f"[worker] {name} failed: {e}")


async def _run_brief_preopt_job() -> None:
    await _run_exclusive(LOCK_BRIEF_PREOPT, "brief preopt", _brief_preopt_body)


async def _run_brief_dispatch_job() -> None:
    await _run_exclusive(LOCK_BRIEF_DISPATCH, "brief dispatch", _brief_dispatch_body)


async def _brief_preopt_body() -> None:
    """Warms brief/store.py's article_segment_cache for all 7 Beats. Runs
    every 6h rather than once/day: users can set any local delivery time in
    any timezone, so there's no single UTC hour that's safely "before
    everyone's slot" — a few-hours-stale cache is the tradeoff instead of
    per-timezone preopt scheduling."""
    from brief.preopt_runner import run_preopt
    try:
        result = await run_preopt()
        logger.info(f"[worker] brief preopt: {result['totals']}")
    except Exception as e:
        logger.error(f"[worker] brief preopt failed: {e}")


async def _brief_dispatch_body() -> None:
    """Enqueues generate_brief (background lane — no one's watching this
    happen) for every user whose local delivery time has passed and who
    doesn't have today's (their local today's) brief yet. See
    store.list_due_users_for_generation for why this is safe to over-run."""
    from brief.store import list_due_users_for_generation
    from core.queue import enqueue
    try:
        due = await list_due_users_for_generation()
        for u in due:
            await enqueue(
                type="generate_brief",
                payload={"user_id": u["user_id"], "date": u["local_date"]},
                user_id=u["user_id"],
                lane="background",
            )
        if due:
            logger.info(f"[worker] brief dispatch: enqueued {len(due)} due user(s)")
    except Exception as e:
        logger.error(f"[worker] brief dispatch failed: {e}")


def _start_scheduler() -> AsyncIOScheduler:
    from core.notifications import send_listen_reminders, send_reengagement_reminders

    scheduler = AsyncIOScheduler(timezone="UTC")
    # 8:00 PM IST = 14:30 UTC
    scheduler.add_job(send_listen_reminders, CronTrigger(hour=14, minute=30, timezone="UTC"))
    # 11:00 AM IST = 05:30 UTC
    scheduler.add_job(send_reengagement_reminders, CronTrigger(hour=5, minute=30, timezone="UTC"))
    # Both worker services call _start_scheduler; the brief jobs must run in
    # exactly ONE of them (Pre-Opt is Sonnet-heavy — two schedulers doubles
    # its cost, and double-dispatch races waste full pipeline runs). Set
    # CURIA_BRIEF_JOBS=0 on every worker service except one.
    # (v3.1 review v1.md §4.3)
    if os.getenv("CURIA_BRIEF_JOBS", "1") != "0":
        scheduler.add_job(_run_brief_preopt_job, IntervalTrigger(hours=6))
        scheduler.add_job(_run_brief_dispatch_job, IntervalTrigger(minutes=15))
    scheduler.start()
    logger.info(
        "[worker] scheduler started: listen_reminder@14:30UTC reengagement@05:30UTC "
        "brief_preopt@6h brief_dispatch@15m"
    )
    return scheduler


async def main() -> None:
    from core.logging import setup_logging
    from core.prompt_watcher import check_prompt_changes, init_prompt_hashes
    from core.prompts.loader import PROMPTS_DIR
    from core.firebase import init_firebase

    setup_logging(service="worker")
    init_firebase()
    init_prompt_hashes(PROMPTS_DIR)

    worker_id = default_worker_id()
    hostname = socket.gethostname()
    backend = get_queue_backend()
    logger.info(
        f"[worker] starting id={worker_id} host={hostname} pid={os.getpid()} "
        f"backend={backend}" + (f" lane={WORKER_LANE}" if backend == "sqs" else "")
    )
    _install_signal_handlers()

    scheduler = _start_scheduler()

    while not _shutdown.is_set():
        try:
            if backend == "sqs":
                # Long poll blocks server-side (≤20s) when idle — no sleep needed.
                processed = await _process_one_sqs(worker_id)
            else:
                processed = await _process_one(worker_id)
        except Exception as e:
            # Defensive: if receive/dequeue itself errors (DB/SQS hiccup), back off and retry.
            logger.exception(f"[worker] dispatch loop error: {e}")
            processed = False
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

        # Check for prompt file changes each poll cycle
        check_prompt_changes(PROMPTS_DIR)

        # Requeue jobs stuck in 'running' (postgres only; SQS visibility replaces it)
        if backend != "sqs":
            await _maybe_reap_stale()

        # Backstop cleanup of stale failed sources (throttled internally)
        await _maybe_purge_failed_sources()

        if not processed and backend != "sqs":
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=EMPTY_QUEUE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass

    scheduler.shutdown(wait=False)
    logger.info(f"[worker] shutting down id={worker_id}")


if __name__ == "__main__":
    asyncio.run(main())
