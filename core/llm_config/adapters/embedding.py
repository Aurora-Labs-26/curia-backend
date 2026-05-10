"""
core/llm_config/adapters/embedding.py
Embedding clients. Currently Voyage; pluggable for OpenAI/Cohere later.

Stub fallback when API key is missing — returns a zero vector at the model's
configured dimension. Pipeline keeps running so dev workflows survive without
a Voyage key (cluster scores are meaningless until the key is set).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import httpx
from loguru import logger

if TYPE_CHECKING:
    from ..schema import ModelConfig, ProviderConfig


class Embedder:
    """
    Generic embedder wrapper. The shape of `embed(text)` is provider-agnostic:
    callers always get list[float] or None on hard failure.
    """

    def __init__(
        self,
        provider: "ProviderConfig",
        model: "ModelConfig",
        settings: dict,
    ):
        self.provider = provider
        self.model = model
        self.settings = settings
        self._warned_stub = False

    @property
    def dimension(self) -> int:
        return self.model.dimension or 1024

    def _stub_vector(self) -> list[float]:
        return [0.0] * self.dimension

    async def embed(self, text: str) -> list[float] | None:
        """
        Returns:
            list[float]  — the embedding (real or zero-vector stub)
            None         — hard error from the provider
        """
        if not text or not text.strip():
            return self._stub_vector()

        api_key = os.getenv(self.provider.api_key_env)
        if not api_key:
            if not self._warned_stub:
                logger.warning(
                    f"{self.provider.api_key_env} not set — embedding STUB active "
                    f"({self.dimension}-dim zero vectors). Cluster similarities "
                    f"will be meaningless until you set the key."
                )
                self._warned_stub = True
            return self._stub_vector()

        if self.provider.type == "voyage":
            return await self._embed_voyage(text, api_key)

        # Future: openai, cohere, etc.
        raise ValueError(f"Embedding provider type '{self.provider.type}' not implemented")

    async def _embed_voyage(self, text: str, api_key: str) -> list[float] | None:
        url = (self.provider.base_url or "https://api.voyageai.com/v1").rstrip("/") + "/embeddings"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.model.model_id,
                        "input": [text[:30_000]],
                        "input_type": "document",
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"Voyage error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["data"][0]["embedding"]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"Voyage returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"Voyage request failed: {e}")
            return None


def build_embedder(
    provider: "ProviderConfig",
    model: "ModelConfig",
    settings: dict,
) -> Embedder:
    if model.kind != "embedding":
        raise ValueError(
            f"build_embedder called with model kind={model.kind} (expected 'embedding') "
            f"for {model.model_id}"
        )
    return Embedder(provider=provider, model=model, settings=settings)
