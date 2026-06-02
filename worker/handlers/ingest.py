"""
worker/handlers/ingest.py
Worker handler for the 'ingest' job type.

Payload: { "source_id": "<uuid>", "user_id": "<id>", "standalone": true|false }
The source row already exists (created by API). Worker scrapes → transforms → embeds,
then triggers generation:
  standalone=True  → evaluate this source alone (_run_standalone)
  standalone=False → run full cluster pipeline (generate_ideas)
"""

from loguru import logger

from core.db.connection import db_fetchrow
from core.ingest import process_source
from core.queue import enqueue


async def handle_ingest(payload: dict) -> None:
    source_id = payload.get("source_id")
    if not source_id:
        raise ValueError("ingest job: missing source_id in payload")

    attempt = payload.get("__attempt__", 1)
    max_attempts = payload.get("__max_attempts__", 1)
    is_final_attempt = attempt >= max_attempts
    standalone = payload.get("standalone", True)

    logger.info(f"[handle_ingest] source_id={source_id} attempt={attempt}/{max_attempts} standalone={standalone}")

    # Raises on failure — generation code below never runs if scraping fails
    await process_source(source_id=source_id, is_final_attempt=is_final_attempt)

    source_row = await db_fetchrow(
        "SELECT user_id FROM source WHERE id = $source_id::uuid",
        {"source_id": source_id},
    )
    if not source_row:
        logger.warning(f"[handle_ingest] could not find source row for source_id={source_id}; skipping generation")
        return

    user_id = source_row["user_id"]

    if standalone:
        # Evaluate this source alone — one focused episode
        from worker.handlers.generate_from_source import _run_standalone
        logger.info(f"[handle_ingest] standalone=True — running _run_standalone for source_id={source_id}")
        await _run_standalone(
            user_id=user_id,
            source_id=source_id,
            show_name=None,
            speaker=None,
            length_minutes=None,
            angle_override=None,
        )
    else:
        # Run the full cluster pipeline across all user sources
        existing_job = await db_fetchrow(
            """
            SELECT id FROM jobs
            WHERE type = 'generate_ideas'
              AND user_id = $user_id
              AND status IN ('queued', 'running')
            LIMIT 1
            """,
            {"user_id": user_id},
        )
        if existing_job:
            logger.info(f"[handle_ingest] generate_ideas already queued/running for user_id={user_id}; skipping")
            return

        job_id = await enqueue(
            type="generate_ideas",
            payload={"user_id": user_id},
            user_id=user_id,
        )
        logger.info(f"[handle_ingest] standalone=False — enqueued generate_ideas job_id={job_id} for user_id={user_id}")
