"""
tests/test_jina_reader.py
Jina Reader scrape tier (core/scraper/cascade.py::_scrape_jina) — key-gating,
cascade ordering (trafilatura → jina → firecrawl), response parsing.
All HTTP mocked; no network.
"""

from unittest.mock import AsyncMock, patch

import pytest

from core.scraper.cascade import HeadCheckResult, _scrape_jina, scrape
from core.scraper.validator import ValidationResult

LONG = "word " * 100  # > MIN_CONTENT_LENGTH after strip
URL = "https://example.com/article"


def _cascade_patches(traf=("tiny", "t", "a", "")):
    """Force the article path: valid URL, HEAD ok, trafilatura returns `traf`."""
    return [
        patch("core.scraper.cascade.validate_url", return_value=ValidationResult(True)),
        patch("core.scraper.cascade.is_youtube_url", return_value=False),
        patch("core.scraper.cascade.is_twitter_url", return_value=False),
        patch("core.scraper.cascade.resolve_redirects", AsyncMock(return_value=URL)),
        patch("core.scraper.cascade.head_check",
              AsyncMock(return_value=HeadCheckResult(ok=True, status_code=200))),
        patch("core.scraper.cascade._scrape_trafilatura", AsyncMock(return_value=traf)),
    ]


class TestScrapeJina:
    async def test_parses_reader_json(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina_key")
        captured = {}

        class _Resp:
            status_code = 200
            text = ""
            def json(self):
                return {"code": 200, "data": {"title": "A Title", "content": "The body."}}

        class FakeAsyncClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, headers=None):
                captured.update(url=url, headers=headers)
                return _Resp()

        with patch("core.scraper.cascade.httpx.AsyncClient", FakeAsyncClient):
            content, title, author, og = await _scrape_jina(URL)

        assert (content, title, author, og) == ("The body.", "A Title", "", "")
        assert captured["url"] == f"https://r.jina.ai/{URL}"
        assert captured["headers"]["Authorization"] == "Bearer jina_key"
        assert captured["headers"]["Accept"] == "application/json"

    async def test_non_200_raises(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina_key")

        class _Resp:
            status_code = 451
            text = "blocked"
            def json(self): return {}

        class FakeAsyncClient:
            def __init__(self, *a, **k): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, *a, **k): return _Resp()

        with patch("core.scraper.cascade.httpx.AsyncClient", FakeAsyncClient):
            with pytest.raises(RuntimeError, match="451"):
                await _scrape_jina(URL)


class TestCascadeOrdering:
    async def test_jina_used_when_trafilatura_thin_and_key_set(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina_key")
        firecrawl = AsyncMock(side_effect=AssertionError("firecrawl must not be called"))
        patches = _cascade_patches() + [
            patch("core.scraper.cascade._scrape_jina",
                  AsyncMock(return_value=(LONG, "Jina Title", "", ""))),
            patch("core.scraper.cascade._try_firecrawl_or_fail", firecrawl),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            result = await scrape(URL)

        assert len(result) == 5
        assert result[1] == "Jina Title"
        firecrawl.assert_not_called()

    async def test_jina_failure_falls_through_to_firecrawl(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina_key")
        firecrawl = AsyncMock(return_value=(LONG, "FC Title", "a", "img", {}))
        patches = _cascade_patches() + [
            patch("core.scraper.cascade._scrape_jina",
                  AsyncMock(side_effect=RuntimeError("reader down"))),
            patch("core.scraper.cascade._try_firecrawl_or_fail", firecrawl),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            result = await scrape(URL)

        assert result[1] == "FC Title"
        firecrawl.assert_called_once()

    async def test_no_key_skips_jina_entirely(self, monkeypatch):
        monkeypatch.delenv("JINA_API_KEY", raising=False)
        jina = AsyncMock(side_effect=AssertionError("jina must not be called without a key"))
        firecrawl = AsyncMock(return_value=(LONG, "FC Title", "a", "img", {}))
        patches = _cascade_patches() + [
            patch("core.scraper.cascade._scrape_jina", jina),
            patch("core.scraper.cascade._try_firecrawl_or_fail", firecrawl),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            await scrape(URL)

        jina.assert_not_called()
        firecrawl.assert_called_once()

    async def test_trafilatura_success_never_reaches_jina(self, monkeypatch):
        monkeypatch.setenv("JINA_API_KEY", "jina_key")
        jina = AsyncMock(side_effect=AssertionError("jina must not be called"))
        patches = _cascade_patches(traf=(LONG, "Traf Title", "au", "img")) + [
            patch("core.scraper.cascade._scrape_jina", jina),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await scrape(URL)

        assert result[1] == "Traf Title"
        jina.assert_not_called()
