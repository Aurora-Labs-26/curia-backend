"""
worker/handlers/generate_episode.py
Worker handler for the 'generate_episode' job type.

Payload: { "episode_id": "<uuid>" }
The episode row already exists with status='queued' (created by API).
Worker runs process_episode() which fills in title/outline/transcript/audio_path
and sets status='ready' (or 'failed').
"""

import asyncio

from loguru import logger

from core.db.connection import db_fetchrow
from studio.generator import process_episode


async def handle_generate_episode(payload: dict) -> None:
    episode_id = payload.get("episode_id")
    if not episode_id:
        raise ValueError("generate_episode job: missing episode_id in payload")
    logger.info(f"[handle_generate_episode] processing episode_id={episode_id}")
    await process_episode(episode_id=episode_id)
    await _notify_episode_ready(episode_id)


async def _notify_episode_ready(episode_id: str) -> None:
    try:
        row = await db_fetchrow(
            """
            SELECT e.title, u.fcm_token
            FROM episode e
            JOIN users u ON u.id = e.user_id
            WHERE e.id = $episode_id::uuid AND e.status = 'ready'
            """,
            {"episode_id": episode_id},
        )
        if not row or not row["fcm_token"]:
            return

        from firebase_admin import messaging
        message = messaging.Message(
            notification=messaging.Notification(
                title="Your show is ready",
                body=row["title"] or "Your episode has been generated",
            ),
            data={"episode_id": episode_id},
            token=row["fcm_token"],
        )
        await asyncio.to_thread(messaging.send, message)
        logger.info(f"[handle_generate_episode] push sent for episode_id={episode_id}")
    except Exception as exc:
        logger.warning(f"[handle_generate_episode] push failed for episode_id={episode_id}: {exc}")
