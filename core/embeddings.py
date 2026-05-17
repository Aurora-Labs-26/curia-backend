"""
core/embeddings.py
Thin facade. Real implementation lives in core/llm_config/adapters/embedding.py.

Public surface (unchanged for callers):
    await get_embedding(text) -> list[float] | None

Configuration (model id, provider, dimension) is owned by config/models.yaml
under bindings.task.embedding. Override at call time:
    from core.llm_config import resolve
    embedder = resolve.embedder()  # default 'embedding' binding
    vec = await embedder.embed(text)
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

from .llm_config import resolve

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))


_embedder = None


def _get_embedder():
    """Lazy-init the configured embedder (one per process, like an LM)."""
    global _embedder
    if _embedder is None:
        _embedder = resolve.embedder()
    return _embedder


def get_embedding_dimension() -> int:
    """Return the configured embedder's dimension (e.g. 1024, 1536)."""
    return _get_embedder().dimension


def get_embedding_column() -> str:
    """Return the DB column name for the active dimension: 'embedding' (1024) or 'embedding_1536'."""
    dim = get_embedding_dimension()
    if dim == 1536:
        return "embedding_1536"
    return "embedding"


async def get_embedding(text: str) -> list[float] | None:
    """Generate an embedding for `text`. Returns list[float] (real or stub) or None on hard failure."""
    return await _get_embedder().embed(text)
