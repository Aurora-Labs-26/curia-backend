"""
tests/test_brief_enrichment.py — publisher-URL resolution chain
(brief/enrichment.py): decode → fast fetch → cascade-on-publisher →
cascade-on-google-url, and resolved_url threading through
pipeline.run_content_fetch_step. Network fully mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brief import enrichment


GOOGLE = "https://news.google.com/rss/articles/CBMitoken"
PUB = "https://publisher.com/story"
LONG = "real article body text " * 50


def _sem():
    return asyncio.Semaphore(2)


class TestTryOne:
    async def test_fast_path_returns_text_and_resolved_url(self):
        with patch.object(enrichment, "_decode_and_fetch_text",
                          return_value=(PUB, LONG)):
            hit = await enrichment._try_one(GOOGLE, _sem())
        assert hit == (LONG, PUB)

    async def test_decoded_but_blocked_rescues_via_cascade_on_publisher(self):
        scrape = AsyncMock(return_value=(LONG, "jina", {}))
        with patch.object(enrichment, "_decode_and_fetch_text",
                          return_value=(PUB, None)), \
             patch("core.scraper.cascade.scrape", scrape):
            hit = await enrichment._try_one(GOOGLE, _sem())
        assert hit == (LONG, PUB)
        scrape.assert_awaited_once_with(PUB)

    async def test_decode_failure_falls_back_to_cascade_on_google_url(self):
        scrape = AsyncMock(return_value=(LONG, "jina", {}))
        with patch.object(enrichment, "_decode_and_fetch_text",
                          return_value=(None, None)), \
             patch("core.scraper.cascade.scrape", scrape):
            hit = await enrichment._try_one(GOOGLE, _sem())
        assert hit == (LONG, None)          # text won, publisher unknown
        scrape.assert_awaited_once_with(GOOGLE)

    async def test_everything_fails_returns_none(self):
        scrape = AsyncMock(return_value=("", "none", {}))
        with patch.object(enrichment, "_decode_and_fetch_text",
                          return_value=(None, None)), \
             patch("core.scraper.cascade.scrape", scrape):
            assert await enrichment._try_one(GOOGLE, _sem()) is None

    async def test_short_cascade_content_rejected(self):
        scrape = AsyncMock(return_value=("too short", "jina", {}))
        with patch.object(enrichment, "_decode_and_fetch_text",
                          return_value=(PUB, None)), \
             patch("core.scraper.cascade.scrape", scrape):
            assert await enrichment._try_one(GOOGLE, _sem()) is None


class TestFetchFullTexts:
    async def test_resolved_url_included_in_result(self):
        with patch.object(enrichment, "_try_one",
                          AsyncMock(return_value=(LONG, PUB))):
            out = await enrichment.fetch_full_texts([{"url": GOOGLE, "source": "CNN"}])
        assert out[0]["resolved_url"] == PUB
        assert out[0]["url"] == GOOGLE       # identity key unchanged

    async def test_alternate_win_has_no_resolved_url(self):
        with patch.object(enrichment, "_try_one", AsyncMock(return_value=None)), \
             patch.object(enrichment, "_race_urls",
                          AsyncMock(return_value=(LONG, "https://alt/2"))):
            out = await enrichment.fetch_full_texts([{
                "url": GOOGLE, "cluster_alternates": [{"url": "https://alt/2", "source": "BBC"}]}])
        assert out[0]["resolved_url"] is None
        assert out[0]["source"] == "BBC"

    async def test_total_failure_yields_none_entry(self):
        with patch.object(enrichment, "_try_one", AsyncMock(return_value=None)):
            out = await enrichment.fetch_full_texts([{"url": GOOGLE}])
        assert out == [None]


class TestContentFetchStepThreading:
    async def test_resolved_url_reaches_selection(self):
        from brief.pipeline import pipeline_manager, ContentFetchRequest
        with patch.object(enrichment, "fetch_full_texts", AsyncMock(return_value=[
                {"text": LONG, "url": GOOGLE, "source": "CNN", "resolved_url": PUB}])), \
             patch("brief.pipeline.enrichment_service.fetch_full_texts",
                   AsyncMock(return_value=[
                       {"text": LONG, "url": GOOGLE, "source": "CNN", "resolved_url": PUB}])):
            out = await pipeline_manager.run_content_fetch_step(
                ContentFetchRequest(selections=[{"url": GOOGLE, "title": "t"}]))
        sel = out["selections"][0]
        assert sel["resolved_url"] == PUB
        assert sel["content_fetched"] is True

    async def test_failed_fetch_has_no_resolved_url_and_falls_back(self):
        from brief.pipeline import pipeline_manager, ContentFetchRequest
        with patch("brief.pipeline.enrichment_service.fetch_full_texts",
                   AsyncMock(return_value=[None])):
            out = await pipeline_manager.run_content_fetch_step(
                ContentFetchRequest(selections=[{"url": GOOGLE, "title": "t",
                                                 "description": "desc"}]))
        sel = out["selections"][0]
        assert sel["content_fetched"] is False
        assert "resolved_url" not in sel
        assert sel["full_text"] == "desc"


class TestStoreResolvedUrl:
    async def test_update_sql_targets_resolved_url_by_id(self):
        from brief import store
        pool = MagicMock(); pool.execute = AsyncMock()
        with patch.object(store.harness_db, "get_pool", AsyncMock(return_value=pool)):
            await store.set_article_resolved_url("a1", PUB)
        sql, *args = pool.execute.await_args.args
        assert "resolved_url" in sql and "WHERE id" in sql
        assert args == ["a1", PUB]

    async def test_detail_query_selects_resolved_url(self):
        from brief import store
        import inspect
        src = inspect.getsource(store.get_daily_brief_detail)
        assert "a.resolved_url" in src


class TestMigrationChain:
    def test_0036_links_to_0035(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "m0036", "alembic/versions/0036_article_resolved_url.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        assert m.revision == "0036_article_resolved_url"
        assert m.down_revision == "0035_brief_schema"
