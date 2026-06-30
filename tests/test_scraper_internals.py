"""
tests/test_scraper_internals.py
Tests for _scrape_trafilatura and _scrape_firecrawl in core/scraper/cascade.py.
All HTTP + library calls mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.scraper.cascade import _scrape_firecrawl, _scrape_trafilatura


# ---------------------------------------------------------------------------
# _scrape_firecrawl
# ---------------------------------------------------------------------------


class TestScrapeFirecrawl:
    @patch.dict("os.environ", {}, clear=True)
    async def test_missing_api_key_raises(self):
        with pytest.raises(RuntimeError, match="FIRECRAWL_API_KEY"):
            await _scrape_firecrawl("https://example.com")

    @patch.dict("os.environ", {"FIRECRAWL_API_KEY": "key-123"})
    async def test_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "markdown": "# Article content\nLong text here",
                "metadata": {"title": "Great Article", "author": "Jane Doe"},
            }
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            content, title, author, og_image = await _scrape_firecrawl("https://example.com")
        assert "Article content" in content
        assert title == "Great Article"
        assert author == "Jane Doe"

    @patch.dict("os.environ", {"FIRECRAWL_API_KEY": "key-123"})
    async def test_non_200_raises(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal error"
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(RuntimeError, match="500"):
                await _scrape_firecrawl("https://example.com")

    @patch.dict("os.environ", {"FIRECRAWL_API_KEY": "key-123"})
    async def test_missing_data_returns_empty(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # no "data" key
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            content, title, author, og_image = await _scrape_firecrawl("https://example.com")
        assert content == ""
        assert title == ""

    @patch.dict("os.environ", {"FIRECRAWL_API_KEY": "key-123"})
    async def test_og_sitename_fallback(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": {
                "markdown": "content",
                "metadata": {"title": "T", "ogSiteName": "Site Name"},
            }
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            _, _, author, _ = await _scrape_firecrawl("https://example.com")
        assert author == "Site Name"


# ---------------------------------------------------------------------------
# _scrape_trafilatura (test via scrape flow since imports are internal)
# ---------------------------------------------------------------------------


class TestScrapeTrafilatura:
    async def test_success_via_scrape(self):
        """Test trafilatura path through the main scrape() function."""
        from core.scraper.cascade import scrape

        content = "x" * 300
        with patch("core.scraper.cascade.normalize_url", side_effect=lambda u: u), \
             patch("core.scraper.cascade.validate_url", return_value=MagicMock(valid=True)), \
             patch("core.scraper.cascade.resolve_redirects", new=AsyncMock(return_value="https://example.com")), \
             patch("core.scraper.cascade.is_likely_paywalled", return_value=False), \
             patch("core.scraper.cascade.is_twitter_url", return_value=False), \
             patch("core.scraper.cascade.head_check", new=AsyncMock(return_value=MagicMock(ok=True))), \
             patch("core.scraper.cascade._scrape_trafilatura", new=AsyncMock(return_value=(content, "Title", "Author", ""))):
            result = await scrape("https://example.com")
        assert result[0] == content
        assert result[1] == "Title"

    async def test_trafilatura_too_short_falls_back_to_firecrawl(self):
        """When trafilatura returns too little, should fall back to firecrawl."""
        from core.scraper.cascade import scrape

        firecrawl_content = "y" * 300
        with patch("core.scraper.cascade.normalize_url", side_effect=lambda u: u), \
             patch("core.scraper.cascade.validate_url", return_value=MagicMock(valid=True)), \
             patch("core.scraper.cascade.resolve_redirects", new=AsyncMock(return_value="https://example.com")), \
             patch("core.scraper.cascade.is_likely_paywalled", return_value=False), \
             patch("core.scraper.cascade.is_twitter_url", return_value=False), \
             patch("core.scraper.cascade.head_check", new=AsyncMock(return_value=MagicMock(ok=True))), \
             patch("core.scraper.cascade._scrape_trafilatura", new=AsyncMock(return_value=("short", "T", "", ""))), \
             patch("core.scraper.cascade._try_firecrawl_or_fail", new=AsyncMock(return_value=(firecrawl_content, "Title2", "Auth2", ""))):
            result = await scrape("https://example.com")
        assert result[0] == firecrawl_content

    async def test_trafilatura_exception_falls_back_to_firecrawl(self):
        """When trafilatura throws, should fall back to firecrawl."""
        from core.scraper.cascade import scrape

        firecrawl_content = "z" * 300
        with patch("core.scraper.cascade.normalize_url", side_effect=lambda u: u), \
             patch("core.scraper.cascade.validate_url", return_value=MagicMock(valid=True)), \
             patch("core.scraper.cascade.resolve_redirects", new=AsyncMock(return_value="https://example.com")), \
             patch("core.scraper.cascade.is_likely_paywalled", return_value=False), \
             patch("core.scraper.cascade.is_twitter_url", return_value=False), \
             patch("core.scraper.cascade.head_check", new=AsyncMock(return_value=MagicMock(ok=True))), \
             patch("core.scraper.cascade._scrape_trafilatura", new=AsyncMock(side_effect=RuntimeError("boom"))), \
             patch("core.scraper.cascade._try_firecrawl_or_fail", new=AsyncMock(return_value=(firecrawl_content, "T", "", ""))):
            result = await scrape("https://example.com")
        assert result[0] == firecrawl_content
