"""
worker/handlers/generate_ideas.py
Worker handler for the 'generate_ideas' job type.

Payload: { "user_id": "<id>" }
Runs the existing LangGraph idea-generation workflow against the user's archive.
"""

from loguru import logger

from intelligence.idea_generator import run_idea_generator


async def handle_generate_ideas(payload: dict) -> None:
    user_id = payload.get("user_id", "default")
    logger.info(f"[handle_generate_ideas] user_id={user_id}")
    await run_idea_generator(user_id=user_id)
