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
from .brief import handle_preopt_brief, handle_generate_brief


HANDLERS: dict[str, Callable[[dict], Awaitable[None]]] = {
    "ingest": handle_ingest,
    "generate_ideas": handle_generate_ideas,
    "generate_episode": handle_generate_episode,
    "optimize": handle_optimize,
    "preopt_brief": handle_preopt_brief,
    "generate_brief": handle_generate_brief,
}

__all__ = [
    "HANDLERS",
    "handle_ingest",
    "handle_generate_ideas",
    "handle_generate_episode",
    "handle_optimize",
    "handle_preopt_brief",
    "handle_generate_brief",
]
