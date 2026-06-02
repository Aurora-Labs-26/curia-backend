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

from dotenv import load_dotenv
from loguru import logger

from core.errors import PermanentError
from core.queue import ack, default_worker_id, dequeue, fail, fail_permanently, reap_stale
from worker.handlers import HANDLERS

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


# Polling interval when the queue is empty. Trades latency for DB load.
EMPTY_QUEUE_SLEEP_SECONDS = float(os.getenv("CURIA_WORKER_POLL_SECONDS", "2"))

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
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: _shutdown.set())


async def _process_one(worker_id: str) -> bool:
    """Pull one job and run its handler. Returns True if a job was processed, False if idle."""
    from core.logging import worker_logger

    job = await dequeue(worker_id=worker_id)
    if job is None:
        return False

    handler = HANDLERS.get(job.type)
    if handler is None:
        logger.error(f"[worker] no handler for job.type={job.type} id={job.id}; failing.")
        worker_logger.error(f"JOB_NO_HANDLER | type={job.type} id={job.id}")
        await fail(job.id, f"unknown job type {job.type}")
        return True

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
        await ack(job.id)
        elapsed = time.time() - start
        logger.info(f"[worker] acked id={job.id}")
        worker_logger.info(f"JOB_SUCCESS | type={job.type} id={job.id} duration={elapsed:.2f}s")
    except asyncio.TimeoutError:
        elapsed = time.time() - start
        logger.error(f"[worker] id={job.id} type={job.type} timed out after {HANDLER_TIMEOUT_SECONDS}s")
        worker_logger.error(
            f"JOB_TIMEOUT | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s timeout={HANDLER_TIMEOUT_SECONDS}s"
        )
        await fail(job.id, f"handler timed out after {HANDLER_TIMEOUT_SECONDS}s")
    except PermanentError as exc:
        elapsed = time.time() - start
        tb = traceback.format_exc()
        logger.warning(f"[worker] id={job.id} type={job.type} permanent failure (no retry): {exc}")
        worker_logger.error(
            f"JOB_PERMANENT_FAIL | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s error={exc}"
        )
        await fail_permanently(job.id, f"{exc}\n{tb}")
    except Exception as exc:
        elapsed = time.time() - start
        tb = traceback.format_exc()
        logger.error(f"[worker] id={job.id} type={job.type} failed:\n{tb}")
        worker_logger.error(
            f"JOB_FAIL | type={job.type} id={job.id} "
            f"duration={elapsed:.2f}s error={exc}"
        )
        await fail(job.id, f"{exc}\n{tb}")
    return True


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
    logger.info(f"[worker] starting id={worker_id} host={hostname} pid={os.getpid()}")
    _install_signal_handlers()

    # Reap stale jobs from crashed workers on startup
    from core.queue import reap_stale
    try:
        reaped = await reap_stale(stale_after_minutes=30)
        if reaped:
            logger.info(f"[worker] recovered {reaped} stale jobs on boot")
    except Exception as e:
        logger.warning(f"[worker] stale reap on boot failed: {e}")

    while not _shutdown.is_set():
        try:
            processed = await _process_one(worker_id)
        except Exception as e:
            # Defensive: if dequeue itself errors (DB hiccup), back off and retry.
            logger.exception(f"[worker] dispatch loop error: {e}")
            processed = False

        # Check for prompt file changes each poll cycle
        check_prompt_changes(PROMPTS_DIR)

        # Requeue jobs stuck in 'running' (throttled internally)
        await _maybe_reap_stale()

        # Backstop cleanup of stale failed sources (throttled internally)
        await _maybe_purge_failed_sources()

        if not processed:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=EMPTY_QUEUE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info(f"[worker] shutting down id={worker_id}")


if __name__ == "__main__":
    asyncio.run(main())
