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
import traceback

from dotenv import load_dotenv
from loguru import logger

from core.queue import ack, default_worker_id, dequeue, fail
from worker.handlers import HANDLERS

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


# Polling interval when the queue is empty. Trades latency for DB load.
EMPTY_QUEUE_SLEEP_SECONDS = float(os.getenv("CURIA_WORKER_POLL_SECONDS", "2"))


_shutdown = asyncio.Event()


def _install_signal_handlers() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: _shutdown.set())


async def _process_one(worker_id: str) -> bool:
    """Pull one job and run its handler. Returns True if a job was processed, False if idle."""
    job = await dequeue(worker_id=worker_id)
    if job is None:
        return False

    handler = HANDLERS.get(job.type)
    if handler is None:
        logger.error(f"[worker] no handler for job.type={job.type} id={job.id}; failing.")
        await fail(job.id, f"unknown job type {job.type}")
        return True

    try:
        logger.info(f"[worker] running type={job.type} id={job.id} attempt={job.attempts}/{job.max_attempts}")
        await handler(job.payload)
        await ack(job.id)
        logger.info(f"[worker] ✓ acked id={job.id}")
    except Exception as exc:
        tb = traceback.format_exc()
        logger.error(f"[worker] ✗ id={job.id} type={job.type} failed:\n{tb}")
        await fail(job.id, f"{exc}\n{tb}")
    return True


async def main() -> None:
    worker_id = default_worker_id()
    hostname = socket.gethostname()
    logger.info(f"[worker] starting id={worker_id} host={hostname} pid={os.getpid()}")
    _install_signal_handlers()

    while not _shutdown.is_set():
        try:
            processed = await _process_one(worker_id)
        except Exception as e:
            # Defensive: if dequeue itself errors (DB hiccup), back off and retry.
            logger.exception(f"[worker] dispatch loop error: {e}")
            processed = False

        if not processed:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=EMPTY_QUEUE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass

    logger.info(f"[worker] shutting down id={worker_id}")


if __name__ == "__main__":
    asyncio.run(main())
