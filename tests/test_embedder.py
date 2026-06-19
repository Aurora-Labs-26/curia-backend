"""
tests/test_embedder.py
Unit tests for core/llm_config/adapters/embedding.py — Embedder class + build_embedder.
All HTTP calls mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.llm_config.schema import ModelConfig, ProviderConfig
from core.llm_config.adapters.embedding import Embedder, build_embedder


def _make_embedder(provider_type="voyage", dimension=1024):
    provider = ProviderConfig(type=provider_type, api_key_env="TEST_API_KEY")
    model = ModelConfig(provider="test", model_id="test-model", kind="embedding", dimension=dimension)
    return Embedder(provider=provider, model=model, settings={})


# ---------------------------------------------------------------------------
# build_embedder
# ---------------------------------------------------------------------------


class TestBuildEmbedder:
    def test_returns_embedder(self):
        provider = ProviderConfig(type="voyage", api_key_env="K")
        model = ModelConfig(provider="p", model_id="m", kind="embedding", dimension=512)
        emb = build_embedder(provider, model, {})
        assert isinstance(emb, Embedder)

    def test_wrong_kind_raises(self):
        provider = ProviderConfig(type="voyage", api_key_env="K")
        model = ModelConfig(provider="p", model_id="m", kind="llm")
        with pytest.raises(ValueError, match="expected 'embedding'"):
            build_embedder(provider, model, {})


# ---------------------------------------------------------------------------
# Embedder basics
# ---------------------------------------------------------------------------


class TestEmbedderBasics:
    def test_dimension_from_model(self):
        emb = _make_embedder(dimension=768)
        assert emb.dimension == 768

    def test_dimension_default_1024(self):
        provider = ProviderConfig(type="voyage", api_key_env="K")
        model = ModelConfig(provider="p", model_id="m", kind="embedding", dimension=None)
        emb = Embedder(provider=provider, model=model, settings={})
        assert emb.dimension == 1024

    def test_stub_vector(self):
        emb = _make_embedder(dimension=3)
        assert emb._stub_vector() == [0.0, 0.0, 0.0]


# ---------------------------------------------------------------------------
# embed() orchestration
# ---------------------------------------------------------------------------


class TestEmbed:
    async def test_empty_text_returns_stub(self):
        emb = _make_embedder(dimension=4)
        result = await emb.embed("")
        assert result == [0.0] * 4

    async def test_whitespace_text_returns_stub(self):
        emb = _make_embedder(dimension=4)
        result = await emb.embed("   ")
        assert result == [0.0] * 4

    @patch.dict("os.environ", {}, clear=True)
    async def test_missing_api_key_returns_stub(self):
        emb = _make_embedder(dimension=2)
        result = await emb.embed("hello world")
        assert result == [0.0, 0.0]
        assert emb._warned_stub is True

    @patch.dict("os.environ", {}, clear=True)
    async def test_stub_warning_only_once(self):
        emb = _make_embedder(dimension=2)
        await emb.embed("a")
        await emb.embed("b")
        assert emb._warned_stub is True

    @patch.dict("os.environ", {"TEST_API_KEY": "key-123"})
    async def test_unknown_provider_raises(self):
        emb = _make_embedder(provider_type="voyage")
        # Override provider type to something invalid
        emb.provider = ProviderConfig(type="cartesia", api_key_env="TEST_API_KEY")
        with pytest.raises(ValueError, match="not implemented"):
            await emb.embed("hello")


# ---------------------------------------------------------------------------
# Provider-specific embed methods
# ---------------------------------------------------------------------------


class TestEmbedProviders:
    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_voyage_success(self):
        emb = _make_embedder(provider_type="voyage", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test text")
        assert result == [0.1, 0.2, 0.3]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_voyage_error_returns_none(self):
        emb = _make_embedder(provider_type="voyage")
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.text = "rate limited"

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result is None

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_openai_success(self):
        emb = _make_embedder(provider_type="openai", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [1.0, 2.0, 3.0]}]}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result == [1.0, 2.0, 3.0]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_cohere_success(self):
        emb = _make_embedder(provider_type="cohere", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embeddings": {"float": [[0.4, 0.5, 0.6]]}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result == [0.4, 0.5, 0.6]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_jina_success(self):
        emb = _make_embedder(provider_type="jina", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [0.7, 0.8, 0.9]}]}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result == [0.7, 0.8, 0.9]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_mistral_success(self):
        emb = _make_embedder(provider_type="mistral", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"data": [{"embedding": [1.1, 1.2, 1.3]}]}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result == [1.1, 1.2, 1.3]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_gemini_success(self):
        emb = _make_embedder(provider_type="gemini", dimension=3)
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"embedding": {"values": [2.1, 2.2, 2.3]}}

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result == [2.1, 2.2, 2.3]

    @patch.dict("os.environ", {"TEST_API_KEY": "key"})
    async def test_voyage_exception_returns_none(self):
        emb = _make_embedder(provider_type="voyage")
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError("down")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.llm_config.adapters.embedding.httpx.AsyncClient", return_value=mock_client):
            result = await emb.embed("test")
        assert result is None
