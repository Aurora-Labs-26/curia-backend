"""
tests/test_ingest_pipeline.py
Tests for core/ingest.py — embed_chunks, embed_primitive, process_source, get_or_create_source.
All DB and external calls mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import core.ingest as ingest


# ---------------------------------------------------------------------------
# embed_chunks
# ---------------------------------------------------------------------------


class TestEmbedChunks:
    async def test_single_chunk_for_short_text(self):
        words = " ".join(["word"] * 50)
        mock_embed = AsyncMock(return_value=[0.1, 0.2])
        mock_exec = AsyncMock()
        with patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_chunks("sid-1", words)
        assert mock_embed.await_count == 1
        assert mock_exec.await_count == 1

    async def test_multiple_chunks(self):
        words = " ".join(["word"] * 500)  # >200 words → multiple chunks
        mock_embed = AsyncMock(return_value=[0.1])
        mock_exec = AsyncMock()
        with patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_chunks("sid-2", words)
        assert mock_embed.await_count > 1
        assert mock_exec.await_count == mock_embed.await_count

    async def test_empty_text(self):
        mock_embed = AsyncMock()
        mock_exec = AsyncMock()
        with patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_chunks("sid-3", "")
        mock_embed.assert_not_awaited()  # no chunks

    async def test_embedding_failure_skipped(self):
        words = " ".join(["word"] * 50)
        mock_embed = AsyncMock(return_value=None)  # embedding fails
        mock_exec = AsyncMock()
        with patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_chunks("sid-4", words)
        mock_exec.assert_not_awaited()  # skipped, no DB insert


# ---------------------------------------------------------------------------
# embed_primitive
# ---------------------------------------------------------------------------


class TestEmbedPrimitive:
    async def test_no_insights_skips(self):
        mock_query = AsyncMock(return_value=[])
        mock_embed = AsyncMock()
        with patch("core.ingest.db_query", mock_query), \
             patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"):
            await ingest.embed_primitive("sid-1")
        mock_embed.assert_not_awaited()

    async def test_null_content_filtered(self):
        rows = [
            {"insight_type": "core_tensions", "content": None},
            {"insight_type": "counterpoints", "content": ""},
            {"insight_type": "key_insights", "content": "null"},
        ]
        mock_query = AsyncMock(return_value=rows)
        mock_embed = AsyncMock()
        with patch("core.ingest.db_query", mock_query), \
             patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"):
            await ingest.embed_primitive("sid-2")
        mock_embed.assert_not_awaited()  # all filtered out

    async def test_primary_insights_preferred(self):
        rows = [
            {"insight_type": "core_tensions", "content": "tensions content"},
            {"insight_type": "key_insights", "content": "insights content"},
        ]
        mock_query = AsyncMock(return_value=rows)
        mock_embed = AsyncMock(return_value=[0.1])
        mock_exec = AsyncMock()
        with patch("core.ingest.db_query", mock_query), \
             patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_primitive("sid-3")
        # Should use primary (core_tensions) only, not fallback
        call_text = mock_embed.call_args[0][0]
        assert "tensions content" in call_text

    async def test_fallback_when_no_primary(self):
        rows = [
            {"insight_type": "key_insights", "content": "insights only"},
        ]
        mock_query = AsyncMock(return_value=rows)
        mock_embed = AsyncMock(return_value=[0.1])
        mock_exec = AsyncMock()
        with patch("core.ingest.db_query", mock_query), \
             patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_primitive("sid-4")
        call_text = mock_embed.call_args[0][0]
        assert "insights only" in call_text

    async def test_embedding_none_skips_insert(self):
        rows = [{"insight_type": "core_tensions", "content": "real"}]
        mock_query = AsyncMock(return_value=rows)
        mock_embed = AsyncMock(return_value=None)
        mock_exec = AsyncMock()
        with patch("core.ingest.db_query", mock_query), \
             patch("core.embeddings.get_embedding", mock_embed), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"), \
             patch("core.ingest.db_execute", mock_exec):
            await ingest.embed_primitive("sid-5")
        mock_exec.assert_not_awaited()


# ---------------------------------------------------------------------------
# process_source
# ---------------------------------------------------------------------------


class TestProcessSource:
    async def test_source_not_found_raises(self):
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found"):
                await ingest.process_source("missing-id")

    async def test_skips_scrape_when_full_text_exists(self):
        row = {"url": "https://example.com", "user_id": "u1", "full_text": "existing text"}
        mock_scrape = AsyncMock()
        mock_set_status = AsyncMock()
        mock_embed = AsyncMock()
        mock_primitive = AsyncMock()
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=row)), \
             patch("core.ingest.scrape_url", mock_scrape), \
             patch("core.ingest._set_status", mock_set_status), \
             patch("core.ingest.db_execute", new=AsyncMock()), \
             patch("core.ingest.run_transformation", return_value="result"), \
             patch("core.ingest.embed_chunks", mock_embed), \
             patch("core.ingest.embed_primitive", mock_primitive), \
             patch("core.ingest.TRANSFORMATION_NAMES", ["summary"]):
            await ingest.process_source("sid-1")
        mock_scrape.assert_not_awaited()  # skipped

    async def test_null_string_transformation_converted(self):
        row = {"url": "https://example.com", "user_id": "u1", "full_text": "text"}
        mock_exec = AsyncMock()
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=row)), \
             patch("core.ingest._set_status", new=AsyncMock()), \
             patch("core.ingest.db_execute", mock_exec), \
             patch("core.ingest.run_transformation", return_value="null"), \
             patch("core.ingest.embed_chunks", new=AsyncMock()), \
             patch("core.ingest.embed_primitive", new=AsyncMock()), \
             patch("core.ingest.TRANSFORMATION_NAMES", ["summary"]):
            await ingest.process_source("sid-2")
        # The "null" string should be converted to None in the DB insert
        insert_call = mock_exec.call_args_list[0]
        assert insert_call[0][1]["content"] is None

    async def test_final_attempt_sets_failed(self):
        row = {"url": "https://example.com", "user_id": "u1", "full_text": None}
        mock_set_status = AsyncMock()
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=row)), \
             patch("core.ingest._set_status", mock_set_status), \
             patch("core.ingest.scrape_url", new=AsyncMock(side_effect=RuntimeError("scrape fail"))), \
             patch("core.ingest.db_execute", new=AsyncMock()):
            with pytest.raises(RuntimeError):
                await ingest.process_source("sid-3", is_final_attempt=True)
        # Should have called _set_status with "failed"
        last_call = mock_set_status.call_args_list[-1]
        assert last_call[0][1] == "failed"

    async def test_non_final_attempt_keeps_status(self):
        row = {"url": "https://example.com", "user_id": "u1", "full_text": None}
        mock_exec = AsyncMock()
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=row)), \
             patch("core.ingest._set_status", new=AsyncMock()), \
             patch("core.ingest.scrape_url", new=AsyncMock(side_effect=RuntimeError("fail"))), \
             patch("core.ingest.db_execute", mock_exec):
            with pytest.raises(RuntimeError):
                await ingest.process_source("sid-4", is_final_attempt=False)
        # Should record error but NOT set status to "failed"
        error_update = [c for c in mock_exec.call_args_list if "error" in str(c)]
        assert len(error_update) > 0


# ---------------------------------------------------------------------------
# get_or_create_source
# ---------------------------------------------------------------------------


class TestGetOrCreateSource:
    async def test_existing_source_reused(self):
        existing = {"id": "existing-id", "status": "ready"}
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=existing)), \
             patch("core.ingest.db_execute", new=AsyncMock()):
            sid = await ingest.get_or_create_source("https://example.com", "u1")
        assert sid == "existing-id"

    async def test_new_source_created(self):
        with patch("core.ingest.db_fetchrow", new=AsyncMock(return_value=None)), \
             patch("core.ingest.db_execute", new=AsyncMock()), \
             patch("core.ingest.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "new-uuid")):
            sid = await ingest.get_or_create_source("https://example.com", "u1")
        assert sid == "new-uuid"

    async def test_url_normalized_before_lookup(self):
        mock_fetch = AsyncMock(return_value=None)
        mock_exec = AsyncMock()
        with patch("core.ingest.db_fetchrow", mock_fetch), \
             patch("core.ingest.db_execute", mock_exec), \
             patch("core.ingest.uuid.uuid4", return_value=MagicMock(__str__=lambda s: "id")):
            await ingest.get_or_create_source("https://EXAMPLE.COM/path/?utm_source=x", "u1")
        # The URL in the DB query should be normalized (lowercased, UTM stripped)
        query_params = mock_fetch.call_args[0][1]
        assert "example.com" in query_params["url"]
        assert "utm_source" not in query_params["url"]
