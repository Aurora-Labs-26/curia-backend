"""
tests/test_url_validator.py
TDD: URL validation + cascading scraper.
"""

import pytest


class TestURLValidator:

    def test_rejects_video_sites(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://vimeo.com/123456",
            "https://www.tiktok.com/@user/video/123",
            "https://www.twitch.tv/channel",
            "https://www.dailymotion.com/video/x7tgad",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject video: {url}"
            assert "video" in result.reason.lower()

    def test_allows_youtube(self):
        # YouTube is allowed — transcript is extracted via youtube-transcript-api
        from core.scraper.validator import validate_url
        for url in ["https://www.youtube.com/watch?v=abc123", "https://youtu.be/abc123"]:
            result = validate_url(url)
            assert result.valid, f"Should allow YouTube: {url}"

    def test_rejects_social_media_except_twitter(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://www.instagram.com/p/abc123",
            "https://www.facebook.com/post/123",
            "https://www.snapchat.com/add/user",
            "https://www.threads.net/@user/post/123",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject social: {url}"
            assert "social" in result.reason.lower()

    def test_allows_twitter(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://twitter.com/user/status/123",
            "https://x.com/user/status/123",
            "https://twitter.com/elonmusk/status/999",
        ]:
            result = validate_url(url)
            assert result.valid, f"Should allow twitter: {url}"

    def test_rejects_shopping(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://www.amazon.com/dp/B09V3KXJPB",
            "https://www.amazon.in/product/123",
            "https://www.ebay.com/itm/123456",
            "https://www.walmart.com/ip/123",
            "https://www.flipkart.com/product/p/itm123",
            "https://www.alibaba.com/product/123",
            "https://www.etsy.com/listing/123",
            "https://www.shopify.com/store",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject shopping: {url}"
            assert "shopping" in result.reason.lower()

    def test_rejects_porn(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://www.pornhub.com/view_video?v=123",
            "https://xvideos.com/video123",
            "https://www.xnxx.com/video-123",
            "https://onlyfans.com/user",
            "https://www.redtube.com/123",
            "https://chaturbate.com/user",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject adult: {url}"
            assert "adult" in result.reason.lower()

    def test_rejects_file_downloads(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://example.com/file.pdf",
            "https://example.com/doc.zip",
            "https://example.com/image.jpg",
            "https://example.com/video.mp4",
            "https://example.com/data.csv",
            "https://example.com/archive.tar.gz",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject file: {url}"
            assert "file" in result.reason.lower()

    def test_rejects_localhost_and_private(self):
        from core.scraper.validator import validate_url
        for url in [
            "http://localhost:3000/page",
            "http://127.0.0.1/page",
            "http://192.168.1.1/admin",
            "http://10.0.0.1/internal",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject private: {url}"

    def test_rejects_non_http(self):
        from core.scraper.validator import validate_url
        for url in [
            "ftp://files.example.com/doc",
            "mailto:user@example.com",
            "javascript:alert(1)",
            "data:text/html,<h1>hi</h1>",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject non-http: {url}"

    def test_rejects_search_engines(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://www.google.com/search?q=test",
            "https://www.bing.com/search?q=test",
            "https://duckduckgo.com/?q=test",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject search: {url}"
            assert "search" in result.reason.lower()

    def test_rejects_messaging_apps(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://wa.me/1234567890",
            "https://t.me/channel",
            "https://discord.gg/invite",
            "https://discord.com/channels/123",
        ]:
            result = validate_url(url)
            assert not result.valid, f"Should reject messaging: {url}"

    def test_accepts_valid_articles(self):
        from core.scraper.validator import validate_url
        for url in [
            "https://paulgraham.com/greatwork.html",
            "https://blog.samaltman.com/how-to-be-successful",
            "https://www.wired.com/story/fast-fashion",
            "https://aeon.co/essays/why-is-it-so-hard",
            "https://stratechery.com/2024/ai-integration",
            "https://chamath.substack.com/p/learn-with-me",
            "https://pmarchive.com/guide_to_startups_part1.html",
            "https://en.wikipedia.org/wiki/Memory",
            "https://www.nytimes.com/2024/article-slug",
            "https://medium.com/@user/article-title-abc123",
            "https://arxiv.org/abs/2301.00001",
            "https://twitter.com/sama/status/123456",
            "https://x.com/elonmusk/status/789",
        ]:
            result = validate_url(url)
            assert result.valid, f"Should accept: {url} — rejected: {result.reason}"

    def test_empty_url(self):
        from core.scraper.validator import validate_url
        result = validate_url("")
        assert not result.valid

    def test_garbage_url(self):
        from core.scraper.validator import validate_url
        result = validate_url("not a url at all")
        assert not result.valid


class TestHeadCheck:

    @pytest.mark.asyncio
    async def test_detects_404(self):
        from core.scraper.cascade import head_check
        from unittest.mock import AsyncMock, patch

        mock_resp = AsyncMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient.head", return_value=mock_resp):
            result = await head_check("https://example.com/missing")
            assert not result.ok
            assert "404" in result.reason

    @pytest.mark.asyncio
    async def test_detects_403(self):
        from core.scraper.cascade import head_check
        from unittest.mock import AsyncMock, patch

        mock_resp = AsyncMock()
        mock_resp.status_code = 403

        with patch("httpx.AsyncClient.head", return_value=mock_resp):
            result = await head_check("https://example.com/blocked")
            assert not result.ok
            assert "403" in result.reason

    @pytest.mark.asyncio
    async def test_detects_5xx(self):
        from core.scraper.cascade import head_check
        from unittest.mock import AsyncMock, patch

        mock_resp = AsyncMock()
        mock_resp.status_code = 500

        with patch("httpx.AsyncClient.head", return_value=mock_resp):
            result = await head_check("https://example.com/broken")
            assert not result.ok
            assert "500" in result.reason

    @pytest.mark.asyncio
    async def test_passes_200(self):
        from core.scraper.cascade import head_check
        from unittest.mock import AsyncMock, patch

        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/html"}

        with patch("httpx.AsyncClient.head", return_value=mock_resp):
            result = await head_check("https://example.com/article")
            assert result.ok

    @pytest.mark.asyncio
    async def test_passes_on_timeout(self):
        """HEAD timeout should not block — pass through to scraper."""
        from core.scraper.cascade import head_check
        from unittest.mock import patch
        import httpx

        with patch("httpx.AsyncClient.head", side_effect=httpx.TimeoutException("timeout")):
            result = await head_check("https://example.com/slow")
            assert result.ok  # don't block on timeout, let scraper try


class TestPaywallDetection:

    def test_known_paywall_domains(self):
        from core.scraper.validator import is_likely_paywalled
        for domain in [
            "www.wsj.com", "www.ft.com", "www.economist.com",
            "www.nytimes.com", "www.washingtonpost.com",
            "www.bloomberg.com", "theathletic.com",
        ]:
            assert is_likely_paywalled(domain), f"Should flag as paywalled: {domain}"

    def test_non_paywalled_domains(self):
        from core.scraper.validator import is_likely_paywalled
        for domain in [
            "paulgraham.com", "blog.samaltman.com", "aeon.co",
            "en.wikipedia.org", "arxiv.org",
        ]:
            assert not is_likely_paywalled(domain), f"Should not flag: {domain}"


class TestTwitterDetection:

    def test_twitter_url_detected(self):
        from core.scraper.validator import is_twitter_url
        assert is_twitter_url("https://twitter.com/sama/status/123")
        assert is_twitter_url("https://x.com/elonmusk/status/456")

    def test_non_twitter_not_detected(self):
        from core.scraper.validator import is_twitter_url
        assert not is_twitter_url("https://paulgraham.com/greatwork.html")


class TestCascadingScraper:

    def test_scraper_exists(self):
        from core.scraper.cascade import scrape
        assert callable(scrape)

    @pytest.mark.asyncio
    async def test_rejects_invalid_url(self):
        from core.scraper.cascade import scrape
        with pytest.raises(ValueError, match="(?i)video"):
            await scrape("https://vimeo.com/123")

    @pytest.mark.asyncio
    async def test_trafilatura_first(self):
        """Should try trafilatura before firecrawl."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        traf_called = False
        fire_called = False

        async def mock_traf(url):
            nonlocal traf_called
            traf_called = True
            return ("Good content " * 100, "Title", None, "")

        async def mock_fire(url):
            nonlocal fire_called
            fire_called = True
            return ("Fallback", "Title", None, "")

        with patch("core.scraper.cascade.head_check", return_value=_ok_head()):
            with patch("core.scraper.cascade._scrape_trafilatura", side_effect=mock_traf):
                with patch("core.scraper.cascade._scrape_firecrawl", side_effect=mock_fire):
                    content, title, author, og_image, extra_data = await scrape("https://example.com/article")

        assert traf_called
        assert not fire_called

    @pytest.mark.asyncio
    async def test_falls_back_to_firecrawl(self):
        """If trafilatura fails, should try firecrawl."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        async def mock_traf(url):
            return ("", "", None, "")

        async def mock_fire(url):
            return ("Firecrawl got the content " * 50, "FC Title", None, "")

        with patch("core.scraper.cascade.head_check", return_value=_ok_head()):
            with patch("core.scraper.cascade._scrape_trafilatura", side_effect=mock_traf):
                with patch("core.scraper.cascade._scrape_firecrawl", side_effect=mock_fire):
                    content, title, author, og_image, extra_data = await scrape("https://example.com/js-heavy-page")

        assert "Firecrawl" in content
        assert title == "FC Title"

    @pytest.mark.asyncio
    async def test_fails_when_both_fail(self):
        """If both trafilatura and firecrawl fail, raise error."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        async def mock_traf(url):
            return ("", "")

        async def mock_fire(url):
            return ("", "")

        with patch("core.scraper.cascade.head_check", return_value=_ok_head()):
            with patch("core.scraper.cascade._scrape_trafilatura", side_effect=mock_traf):
                with patch("core.scraper.cascade._scrape_firecrawl", side_effect=mock_fire):
                    with pytest.raises(ValueError, match="Could not extract"):
                        await scrape("https://example.com/empty-page")

    @pytest.mark.asyncio
    async def test_firecrawl_exception_handled(self):
        """If firecrawl throws, should still give a clear error."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        async def mock_traf(url):
            return ("", "")

        async def mock_fire(url):
            raise RuntimeError("Firecrawl API error")

        with patch("core.scraper.cascade.head_check", return_value=_ok_head()):
            with patch("core.scraper.cascade._scrape_trafilatura", side_effect=mock_traf):
                with patch("core.scraper.cascade._scrape_firecrawl", side_effect=mock_fire):
                    with pytest.raises(ValueError, match="Could not extract"):
                        await scrape("https://example.com/broken")

    @pytest.mark.asyncio
    async def test_404_caught_early(self):
        """HEAD check should catch 404 before wasting scrape calls."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        mock_resp = AsyncMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient.head", return_value=mock_resp):
            with pytest.raises(ValueError, match="404"):
                await scrape("https://example.com/gone")

    @pytest.mark.asyncio
    async def test_paywall_warning_in_error(self):
        """If a known paywall domain returns thin content, mention paywall."""
        from core.scraper.cascade import scrape
        from unittest.mock import AsyncMock, patch

        async def mock_traf(url):
            return ("Subscribe to read", "")

        async def mock_fire(url):
            return ("Subscribe to continue reading", "")

        with patch("core.scraper.cascade._scrape_trafilatura", side_effect=mock_traf):
            with patch("core.scraper.cascade._scrape_firecrawl", side_effect=mock_fire):
                with patch("core.scraper.cascade.head_check", return_value=_ok_head()):
                    with pytest.raises(ValueError, match="(?i)paywall"):
                        await scrape("https://www.wsj.com/articles/some-article")


def _ok_head():
    """Helper: mock HeadCheckResult that passes."""
    from core.scraper.cascade import HeadCheckResult
    return HeadCheckResult(ok=True)
