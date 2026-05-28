"""
Tests for embed_primitive() insight fallback logic.
Verifies that sources without core_tensions/counterpoints still get embedded
using other available insights (summary, key_insights, etc.).
"""

import pytest
from unittest.mock import AsyncMock, patch


@pytest.fixture(autouse=True)
def _patch_all():
    """Patch DB + embedding deps for every test."""
    with patch("core.ingest.db_execute", new_callable=AsyncMock) as mock_exec, \
         patch("core.ingest.db_query", new_callable=AsyncMock) as mock_query, \
         patch("core.embeddings.get_embedding_column", return_value="embedding_1536"), \
         patch("core.embeddings.get_embedding", new_callable=AsyncMock) as mock_embed:
        _patch_all.mock_exec = mock_exec
        _patch_all.mock_query = mock_query
        _patch_all.mock_embed = mock_embed
        yield


def _q():
    return _patch_all.mock_query

def _e():
    return _patch_all.mock_embed

def _x():
    return _patch_all.mock_exec


@pytest.mark.asyncio
async def test_uses_core_tensions_when_available():
    """When core_tensions and counterpoints exist, use only those."""
    _q().return_value = [
        {"insight_type": "core_tensions", "content": "tension A"},
        {"insight_type": "counterpoints", "content": "counter B"},
        {"insight_type": "summary", "content": "some summary"},
    ]
    _e().return_value = [0.1] * 1536

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    call_text = _e().call_args[0][0]
    assert "tension A" in call_text
    assert "counter B" in call_text
    assert "some summary" not in call_text
    assert _x().called


@pytest.mark.asyncio
async def test_falls_back_to_all_insights():
    """When core_tensions/counterpoints are empty, use all other insights."""
    _q().return_value = [
        {"insight_type": "core_tensions", "content": ""},
        {"insight_type": "counterpoints", "content": None},
        {"insight_type": "summary", "content": "celery worker guide summary"},
        {"insight_type": "key_insights", "content": "concurrency prefork gevent"},
        {"insight_type": "examples", "content": "example code snippet"},
    ]
    _e().return_value = [0.2] * 1536

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    call_text = _e().call_args[0][0]
    assert "celery worker guide summary" in call_text
    assert "concurrency prefork gevent" in call_text
    assert "example code snippet" in call_text
    assert _x().called


@pytest.mark.asyncio
async def test_skips_when_no_insights():
    """When no usable insights exist at all, skip without error."""
    _q().return_value = [
        {"insight_type": "core_tensions", "content": ""},
        {"insight_type": "summary", "content": "null"},
    ]

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    _e().assert_not_called()
    _x().assert_not_called()


@pytest.mark.asyncio
async def test_skips_when_no_rows():
    """When source has no insights at all, skip without error."""
    _q().return_value = []

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    _e().assert_not_called()
    _x().assert_not_called()


@pytest.mark.asyncio
async def test_handles_null_string_in_primary():
    """core_tensions with 'null' string should be treated as empty."""
    _q().return_value = [
        {"insight_type": "core_tensions", "content": "null"},
        {"insight_type": "counterpoints", "content": "  "},
        {"insight_type": "key_insights", "content": "real insight content"},
    ]
    _e().return_value = [0.3] * 1536

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    call_text = _e().call_args[0][0]
    assert "real insight content" in call_text


@pytest.mark.asyncio
async def test_embedding_none_skips_db_write():
    """When the embedder returns None, don't write to DB."""
    _q().return_value = [
        {"insight_type": "summary", "content": "some content"},
    ]
    _e().return_value = None

    from core.ingest import embed_primitive
    await embed_primitive("test-id")

    _x().assert_not_called()
