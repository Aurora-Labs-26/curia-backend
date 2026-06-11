"""
worker/handlers — registry of job-type handlers.

Each handler is `async def handle(payload: dict) -> None`. Raising bubbles up to the
queue's fail() which decides retry vs permanent failure based on attempts.
"""

from typing import Awaitable, Callable

from .ingest import handle_ingest
from .generate_ideas import handle_generate_ideas
from .generate_episode import handle_generate_episode
from .optimization import handle_optimize


HANDLERS: dict[str, Callable[[dict], Awaitable[None]]] = {
    "ingest": handle_ingest,
    "generate_ideas": handle_generate_ideas,
    "generate_episode": handle_generate_episode,
    "optimize": handle_optimize,
}

__all__ = [
    "HANDLERS",
    "handle_ingest",
    "handle_generate_ideas",
    "handle_generate_episode",
    "handle_optimize",
]
