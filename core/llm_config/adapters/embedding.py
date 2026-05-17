"""
core/llm_config/adapters/embedding.py
Embedding clients: Voyage, OpenAI, Cohere, Jina, Mistral, Gemini.

Stub fallback when API key is missing — returns a zero vector at the model's
configured dimension. Pipeline keeps running so dev workflows survive without
an embedding key (cluster scores are meaningless until the key is set).
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
            list[float]  -- the embedding (real or zero-vector stub)
            None         -- hard error from the provider
        """
        import time as _time

        llm_log = logger.bind(log_type="llm")

        if not text or not text.strip():
            return self._stub_vector()

        api_key = os.getenv(self.provider.api_key_env)
        if not api_key:
            if not self._warned_stub:
                logger.warning(
                    f"{self.provider.api_key_env} not set -- embedding STUB active "
                    f"({self.dimension}-dim zero vectors). Cluster similarities "
                    f"will be meaningless until you set the key."
                )
                self._warned_stub = True
            return self._stub_vector()

        llm_log.info(
            f"EMBED_START | provider={self.provider.type} "
            f"model={self.model.model_id} text_len={len(text)}"
        )
        start = _time.time()

        if self.provider.type == "voyage":
            embedding = await self._embed_voyage(text, api_key)
        elif self.provider.type == "openai":
            embedding = await self._embed_openai(text, api_key)
        elif self.provider.type == "cohere":
            embedding = await self._embed_cohere(text, api_key)
        elif self.provider.type == "jina":
            embedding = await self._embed_jina(text, api_key)
        elif self.provider.type == "mistral":
            embedding = await self._embed_mistral(text, api_key)
        elif self.provider.type == "gemini":
            embedding = await self._embed_gemini(text, api_key)
        else:
            raise ValueError(f"Embedding provider type '{self.provider.type}' not implemented")

        elapsed = _time.time() - start
        if embedding is not None:
            llm_log.info(
                f"EMBED_END | provider={self.provider.type} "
                f"model={self.model.model_id} dim={len(embedding)} "
                f"duration={elapsed:.2f}s"
            )
        else:
            llm_log.warning(
                f"EMBED_FAIL | provider={self.provider.type} "
                f"model={self.model.model_id} duration={elapsed:.2f}s"
            )
        return embedding

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

    async def _embed_openai(self, text: str, api_key: str) -> list[float] | None:
        url = (self.provider.base_url or "https://api.openai.com/v1").rstrip("/") + "/embeddings"
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
                        "input": text[:30_000],
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"OpenAI embedding error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["data"][0]["embedding"]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"OpenAI returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"OpenAI embedding request failed: {e}")
            return None

    async def _embed_cohere(self, text: str, api_key: str) -> list[float] | None:
        url = (self.provider.base_url or "https://api.cohere.com/v2").rstrip("/") + "/embed"
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
                        "texts": [text[:30_000]],
                        "input_type": "search_document",
                        "embedding_types": ["float"],
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"Cohere embedding error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["embeddings"]["float"][0]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"Cohere returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"Cohere embedding request failed: {e}")
            return None

    async def _embed_jina(self, text: str, api_key: str) -> list[float] | None:
        url = (self.provider.base_url or "https://api.jina.ai/v1").rstrip("/") + "/embeddings"
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
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"Jina embedding error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["data"][0]["embedding"]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"Jina returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"Jina embedding request failed: {e}")
            return None

    async def _embed_mistral(self, text: str, api_key: str) -> list[float] | None:
        url = (self.provider.base_url or "https://api.mistral.ai/v1").rstrip("/") + "/embeddings"
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
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"Mistral embedding error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["data"][0]["embedding"]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"Mistral returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"Mistral embedding request failed: {e}")
            return None

    async def _embed_gemini(self, text: str, api_key: str) -> list[float] | None:
        base_url = (self.provider.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        url = f"{base_url}/models/{self.model.model_id}:embedContent"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    params={"key": api_key},
                    headers={"Content-Type": "application/json"},
                    json={
                        "model": f"models/{self.model.model_id}",
                        "content": {"parts": [{"text": text[:30_000]}]},
                    },
                )
            if resp.status_code != 200:
                logger.warning(f"Gemini embedding error {resp.status_code}: {resp.text[:200]}")
                return None
            data = resp.json()
            embedding = data["embedding"]["values"]
            if len(embedding) != self.dimension:
                logger.warning(
                    f"Gemini returned {len(embedding)}-dim, schema expects {self.dimension}. "
                    f"Check model alignment with migration."
                )
            return embedding
        except Exception as e:
            logger.warning(f"Gemini embedding request failed: {e}")
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
