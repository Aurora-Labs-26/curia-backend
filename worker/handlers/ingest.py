"""
worker/handlers/ingest.py
Worker handler for the 'ingest' job type.

Payload: { "source_id": "<uuid>", "user_id": "<id>" }
The source row already exists (created by API). Worker just runs process_source().
"""

from loguru import logger

from core.ingest import process_source


async def handle_ingest(payload: dict) -> None:
    source_id = payload.get("source_id")
    if not source_id:
        raise ValueError("ingest job: missing source_id in payload")
    logger.info(f"[handle_ingest] processing source_id={source_id}")
    await process_source(source_id=source_id)
