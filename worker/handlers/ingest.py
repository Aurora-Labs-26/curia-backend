"""
worker/handlers/ingest.py
Worker handler for the 'ingest' job type.

Payload: { "source_id": "<uuid>", "user_id": "<id>" }
The source row already exists (created by API). Worker just runs process_source().
"""

from loguru import logger

from core.db.connection import db_fetchrow
from core.ingest import process_source
from core.queue import enqueue


async def handle_ingest(payload: dict) -> None:
    source_id = payload.get("source_id")
    if not source_id:
        raise ValueError("ingest job: missing source_id in payload")
    logger.info(f"[handle_ingest] processing source_id={source_id}")
    await process_source(source_id=source_id)

    source_row = await db_fetchrow(
        "SELECT user_id FROM source WHERE id = $source_id::uuid",
        {"source_id": source_id},
    )
    if not source_row:
        logger.warning(f"[handle_ingest] could not find source row for source_id={source_id}; skipping auto-trigger")
        return

    user_id = source_row["user_id"]

    # Skip auto-generate when the caller (e.g. share sheet) will trigger generation explicitly
    if not payload.get("auto_generate", True):
        logger.info(f"[handle_ingest] auto_generate=false for source_id={source_id}; skipping generate_ideas")
        return

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
    logger.info(f"[handle_ingest] auto-enqueued generate_ideas job_id={job_id} for user_id={user_id}")
