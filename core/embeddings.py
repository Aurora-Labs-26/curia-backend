"""
core/embeddings.py
Local embedding via Ollama nomic-embed-text (768 dimensions).
"""

import httpx
from loguru import logger

OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768


async def get_embedding(text: str) -> list[float] | None:
    """Generate embedding vector for a text string via Ollama."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                OLLAMA_URL,
                json={"model": EMBED_MODEL, "prompt": text}
            )
        if response.status_code == 200:
            return response.json()["embedding"]
        logger.warning(f"Ollama embedding error: {response.text}")
        return None
    except Exception as e:
        logger.warning(f"Ollama embedding failed: {e}")
        return None
