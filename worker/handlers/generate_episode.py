"""
worker/handlers/generate_episode.py
Worker handler for the 'generate_episode' job type.

Payload: { "episode_id": "<uuid>" }
The episode row already exists with status='queued' (created by API).
Worker runs process_episode() which fills in title/outline/transcript/audio_path
and sets status='ready' (or 'failed').
"""

from loguru import logger

from studio.generator import process_episode


async def handle_generate_episode(payload: dict) -> None:
    episode_id = payload.get("episode_id")
    if not episode_id:
        raise ValueError("generate_episode job: missing episode_id in payload")
    logger.info(f"[handle_generate_episode] processing episode_id={episode_id}")
    await process_episode(episode_id=episode_id)
