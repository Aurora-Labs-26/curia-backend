"""
tests/test_cascade_scraper.py
Unit tests for core/scraper/cascade.py — URL normalization, HEAD checks, scrape orchestration.
All HTTP calls mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.errors import PermanentError
from core.scraper.cascade import (
    HeadCheckResult,
    head_check,
    normalize_url,
    resolve_redirects,
    _try_firecrawl_or_fail,
)


# ---------------------------------------------------------------------------
# normalize_url (pure)
# ---------------------------------------------------------------------------


class TestNormalizeUrl:
    def test_substack_pub_rewrite(self):
        url = "https://substack.com/pub/johnsmith/p/my-great-article"
        assert normalize_url(url) == "https://johnsmith.substack.com/p/my-great-article"

    def test_substack_www_pub_rewrite(self):
        url = "https://www.substack.com/pub/jane/p/post-slug"
        assert normalize_url(url) == "https://jane.substack.com/p/post-slug"

    def test_non_substack_unchanged(self):
        url = "https://example.com/article/123"
        assert normalize_url(url) == url

    def test_already_canonical_substack(self):
        url = "https://johnsmith.substack.com/p/my-article"
        assert normalize_url(url) == url


# ---------------------------------------------------------------------------
# head_check
# ---------------------------------------------------------------------------


class TestHeadCheck:
    async def test_200_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com")
        assert result.ok is True

    async def test_404_not_found(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com/nope")
        assert result.ok is False
        assert result.status_code == 404

    async def test_403_forbidden(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com/blocked")
        assert result.ok is False
        assert result.status_code == 403

    async def test_500_server_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com")
        assert result.ok is False
        assert result.status_code == 502

    async def test_timeout_still_proceeds(self):
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.TimeoutException("slow")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com")
        assert result.ok is True  # timeout → proceed anyway

    async def test_connection_error_still_proceeds(self):
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.ConnectError("dns fail")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await head_check("https://example.com")
        assert result.ok is True


# ---------------------------------------------------------------------------
# resolve_redirects
# ---------------------------------------------------------------------------


class TestResolveRedirects:
    async def test_no_redirect(self):
        mock_resp = MagicMock()
        mock_resp.url = "https://example.com/article"
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_redirects("https://example.com/article")
        assert result == "https://example.com/article"

    async def test_redirect_followed(self):
        mock_resp = MagicMock()
        mock_resp.url = "https://canonical.example.com/real-article"
        mock_client = AsyncMock()
        mock_client.head.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_redirects("https://short.link/abc")
        assert result == "https://canonical.example.com/real-article"

    async def test_exception_returns_original(self):
        mock_client = AsyncMock()
        mock_client.head.side_effect = httpx.ConnectError("down")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("core.scraper.cascade.httpx.AsyncClient", return_value=mock_client):
            result = await resolve_redirects("https://example.com")
        assert result == "https://example.com"


# ---------------------------------------------------------------------------
# _try_firecrawl_or_fail
# ---------------------------------------------------------------------------


class TestTryFirecrawlOrFail:
    async def test_paywall_domain_error(self):
        with patch("core.scraper.cascade._scrape_firecrawl", new=AsyncMock(return_value=("short", "", "", ""))):
            with pytest.raises(PermanentError, match="paywall"):
                await _try_firecrawl_or_fail("https://wsj.com/article", is_paywall_domain=True)

    async def test_firecrawl_exception_raises_permanent(self):
        with patch("core.scraper.cascade._scrape_firecrawl", new=AsyncMock(side_effect=RuntimeError("no key"))):
            with pytest.raises(PermanentError):
                await _try_firecrawl_or_fail("https://example.com/x", is_paywall_domain=False)

    async def test_firecrawl_success(self):
        content = "x" * 300
        with patch("core.scraper.cascade._scrape_firecrawl", new=AsyncMock(return_value=(content, "Title", "Author", ""))):
            result = await _try_firecrawl_or_fail("https://example.com", is_paywall_domain=False)
        assert result[0] == content.strip()
        assert result[1] == "Title"

    async def test_twitter_url_error(self):
        with patch("core.scraper.cascade._scrape_firecrawl", new=AsyncMock(return_value=("short", "", "", ""))), \
             patch("core.scraper.cascade.is_twitter_url", return_value=True):
            with pytest.raises(PermanentError, match="Twitter"):
                await _try_firecrawl_or_fail("https://x.com/user/status/123", is_paywall_domain=False)

    async def test_generic_failure_error(self):
        with patch("core.scraper.cascade._scrape_firecrawl", new=AsyncMock(return_value=("short", "", "", ""))), \
             patch("core.scraper.cascade.is_twitter_url", return_value=False):
            with pytest.raises(PermanentError, match="Could not extract"):
                await _try_firecrawl_or_fail("https://example.com/broken", is_paywall_domain=False)


# ---------------------------------------------------------------------------
# Full scrape flow
# ---------------------------------------------------------------------------


class TestScrapeFlow:
    async def test_invalid_url_raises_permanent(self):
        from core.scraper.cascade import scrape
        with pytest.raises(PermanentError):
            await scrape("")

    async def test_head_check_failure_raises(self):
        from core.scraper.cascade import scrape
        with patch("core.scraper.cascade.normalize_url", side_effect=lambda u: u), \
             patch("core.scraper.cascade.validate_url", return_value=MagicMock(valid=True)), \
             patch("core.scraper.cascade.resolve_redirects", new=AsyncMock(return_value="https://example.com")), \
             patch("core.scraper.cascade.is_likely_paywalled", return_value=False), \
             patch("core.scraper.cascade.is_twitter_url", return_value=False), \
             patch("core.scraper.cascade.head_check", new=AsyncMock(return_value=HeadCheckResult(ok=False, status_code=404, reason="Not found"))):
            with pytest.raises(PermanentError, match="Not found"):
                await scrape("https://example.com/article")

    async def test_twitter_skips_trafilatura(self):
        from core.scraper.cascade import scrape
        content = "x" * 300
        with patch("core.scraper.cascade.normalize_url", side_effect=lambda u: u), \
             patch("core.scraper.cascade.validate_url", return_value=MagicMock(valid=True)), \
             patch("core.scraper.cascade.resolve_redirects", new=AsyncMock(return_value="https://x.com/status/1")), \
             patch("core.scraper.cascade.is_likely_paywalled", return_value=False), \
             patch("core.scraper.cascade.is_twitter_url", return_value=True), \
             patch("core.scraper.cascade._try_firecrawl_or_fail", new=AsyncMock(return_value=(content, "Tweet", "", ""))), \
             patch("core.scraper.cascade.head_check") as mock_head, \
             patch("core.scraper.cascade._scrape_trafilatura") as mock_traf:
            result = await scrape("https://x.com/status/1")
        mock_head.assert_not_awaited()  # skipped for Twitter
        mock_traf.assert_not_awaited()  # skipped for Twitter
        assert result[0] == content
