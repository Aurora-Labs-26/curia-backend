"""
tests/test_embeddings_facade.py
Tests for core/embeddings.py — thin facade over the embedder.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.embeddings as emb


@pytest.fixture(autouse=True)
def _reset_embedder():
    """Reset the module-level singleton between tests."""
    original = emb._embedder
    emb._embedder = None
    yield
    emb._embedder = original


class TestGetEmbeddingColumn:
    def test_1536_returns_suffixed(self):
        mock_embedder = MagicMock()
        mock_embedder.dimension = 1536
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            assert emb.get_embedding_column() == "embedding_1536"

    def test_1024_returns_default(self):
        mock_embedder = MagicMock()
        mock_embedder.dimension = 1024
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            assert emb.get_embedding_column() == "embedding"

    def test_768_returns_default(self):
        mock_embedder = MagicMock()
        mock_embedder.dimension = 768
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            assert emb.get_embedding_column() == "embedding"


class TestGetEmbeddingDimension:
    def test_returns_dimension(self):
        mock_embedder = MagicMock()
        mock_embedder.dimension = 512
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            assert emb.get_embedding_dimension() == 512


class TestGetEmbedder:
    def test_lazy_init(self):
        mock_embedder = MagicMock()
        with patch("core.embeddings.resolve") as mock_resolve:
            mock_resolve.embedder.return_value = mock_embedder
            result = emb._get_embedder()
        assert result is mock_embedder
        mock_resolve.embedder.assert_called_once()

    def test_cached(self):
        mock_embedder = MagicMock()
        emb._embedder = mock_embedder
        with patch("core.embeddings.resolve") as mock_resolve:
            result = emb._get_embedder()
        assert result is mock_embedder
        mock_resolve.embedder.assert_not_called()


class TestGetEmbedding:
    async def test_delegates_to_embedder(self):
        mock_embedder = MagicMock()
        mock_embedder.embed = AsyncMock(return_value=[0.1, 0.2])
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            result = await emb.get_embedding("hello")
        assert result == [0.1, 0.2]

    async def test_returns_none_on_failure(self):
        mock_embedder = MagicMock()
        mock_embedder.embed = AsyncMock(return_value=None)
        with patch.object(emb, "_get_embedder", return_value=mock_embedder):
            result = await emb.get_embedding("hello")
        assert result is None
