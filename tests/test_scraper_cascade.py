"""
tests/test_scraper_cascade.py
Tests for the cascading scraper and image extraction.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from core.scraper.cascade import (
    scrape,
    _scrape_jina,
    _scrape_trafilatura,
    normalize_url,
    head_check,
)
from core.scraper.images import extract_image_urls, download_and_upload_images
from core.scraper.validator import validate_url, is_twitter_url
from core.storage.blob import get_blob_url, get_storage_backend


# ---------------------------------------------------------------------------
# Cascade tests
# ---------------------------------------------------------------------------


class TestCascadeOrder:
    """Verify the cascade falls through tiers correctly."""

    @pytest.mark.asyncio
    async def test_trafilatura_success_skips_jina_and_firecrawl(self):
        with (
            patch("core.scraper.cascade._scrape_trafilatura", new_callable=AsyncMock) as mock_traf,
            patch("core.scraper.cascade._scrape_jina", new_callable=AsyncMock) as mock_jina,
            patch("core.scraper.cascade._scrape_firecrawl", new_callable=AsyncMock) as mock_fc,
            patch("core.scraper.cascade.head_check", new_callable=AsyncMock) as mock_head,
            patch("core.scraper.cascade.resolve_redirects", new_callable=AsyncMock) as mock_redir,
        ):
            mock_redir.return_value = "https://example.com/article"
            mock_head.return_value = MagicMock(ok=True)
            mock_traf.return_value = ("A" * 300, "Good Article")

            content, title = await scrape("https://example.com/article")
            assert len(content) >= 200
            mock_traf.assert_called_once()
            mock_jina.assert_not_called()
            mock_fc.assert_not_called()

    @pytest.mark.asyncio
    async def test_trafilatura_fail_falls_to_jina(self):
        with (
            patch("core.scraper.cascade._scrape_trafilatura", new_callable=AsyncMock) as mock_traf,
            patch("core.scraper.cascade._scrape_jina", new_callable=AsyncMock) as mock_jina,
            patch("core.scraper.cascade._scrape_firecrawl", new_callable=AsyncMock) as mock_fc,
            patch("core.scraper.cascade.head_check", new_callable=AsyncMock) as mock_head,
            patch("core.scraper.cascade.resolve_redirects", new_callable=AsyncMock) as mock_redir,
        ):
            mock_redir.return_value = "https://example.com/article"
            mock_head.return_value = MagicMock(ok=True)
            mock_traf.side_effect = RuntimeError("trafilatura broken")
            mock_jina.return_value = ("B" * 300, "Jina Article")

            content, title = await scrape("https://example.com/article")
            assert len(content) >= 200
            mock_traf.assert_called_once()
            mock_jina.assert_called_once()
            mock_fc.assert_not_called()

    @pytest.mark.asyncio
    async def test_trafilatura_short_falls_to_jina(self):
        with (
            patch("core.scraper.cascade._scrape_trafilatura", new_callable=AsyncMock) as mock_traf,
            patch("core.scraper.cascade._scrape_jina", new_callable=AsyncMock) as mock_jina,
            patch("core.scraper.cascade._scrape_firecrawl", new_callable=AsyncMock) as mock_fc,
            patch("core.scraper.cascade.head_check", new_callable=AsyncMock) as mock_head,
            patch("core.scraper.cascade.resolve_redirects", new_callable=AsyncMock) as mock_redir,
        ):
            mock_redir.return_value = "https://example.com/article"
            mock_head.return_value = MagicMock(ok=True)
            mock_traf.return_value = ("short", "")
            mock_jina.return_value = ("C" * 300, "Jina Got It")

            content, title = await scrape("https://example.com/article")
            mock_jina.assert_called_once()
            mock_fc.assert_not_called()

    @pytest.mark.asyncio
    async def test_jina_fail_falls_to_firecrawl(self):
        with (
            patch("core.scraper.cascade._scrape_trafilatura", new_callable=AsyncMock) as mock_traf,
            patch("core.scraper.cascade._scrape_jina", new_callable=AsyncMock) as mock_jina,
            patch("core.scraper.cascade._scrape_firecrawl", new_callable=AsyncMock) as mock_fc,
            patch("core.scraper.cascade.head_check", new_callable=AsyncMock) as mock_head,
            patch("core.scraper.cascade.resolve_redirects", new_callable=AsyncMock) as mock_redir,
        ):
            mock_redir.return_value = "https://example.com/article"
            mock_head.return_value = MagicMock(ok=True)
            mock_traf.return_value = ("short", "")
            mock_jina.side_effect = RuntimeError("jina down")
            mock_fc.return_value = ("D" * 300, "Firecrawl Got It")

            content, title = await scrape("https://example.com/article")
            mock_fc.assert_called_once()

    @pytest.mark.asyncio
    async def test_twitter_goes_straight_to_firecrawl(self):
        with (
            patch("core.scraper.cascade._scrape_trafilatura", new_callable=AsyncMock) as mock_traf,
            patch("core.scraper.cascade._scrape_jina", new_callable=AsyncMock) as mock_jina,
            patch("core.scraper.cascade._scrape_firecrawl", new_callable=AsyncMock) as mock_fc,
            patch("core.scraper.cascade.head_check", new_callable=AsyncMock) as mock_head,
            patch("core.scraper.cascade.resolve_redirects", new_callable=AsyncMock) as mock_redir,
        ):
            mock_redir.return_value = "https://x.com/paulg/status/123"
            mock_head.return_value = MagicMock(ok=True)
            mock_fc.return_value = ("E" * 300, "PG Tweet")

            content, title = await scrape("https://x.com/paulg/status/123")
            mock_traf.assert_not_called()
            mock_jina.assert_not_called()
            mock_fc.assert_called_once()


class TestNormalizeUrl:
    def test_substack_pub_rewrite(self):
        url = "https://substack.com/pub/paulgraham/p/founder-mode"
        assert normalize_url(url) == "https://paulgraham.substack.com/p/founder-mode"

    def test_normal_url_unchanged(self):
        url = "https://example.com/article"
        assert normalize_url(url) == url


# ---------------------------------------------------------------------------
# Image extraction tests
# ---------------------------------------------------------------------------


class TestImageExtraction:
    def test_extract_basic_images(self):
        md = """
Some text here.
![Article hero](https://example.com/hero.jpg)
More text.
![Diagram](https://example.com/diagram.png)
"""
        images = extract_image_urls(md)
        assert len(images) == 2
        assert images[0]["url"] == "https://example.com/hero.jpg"
        assert images[1]["alt"] == "Diagram"

    def test_skips_svg_and_ico(self):
        md = """
![Logo](https://example.com/logo.svg)
![Favicon](https://example.com/favicon.ico)
![Real](https://example.com/photo.jpg)
"""
        images = extract_image_urls(md)
        assert len(images) == 1
        assert "photo.jpg" in images[0]["url"]

    def test_skips_gravatar(self):
        md = "![Avatar](https://gravatar.com/avatar/abc123)"
        images = extract_image_urls(md)
        assert len(images) == 0

    def test_deduplicates(self):
        md = """
![A](https://example.com/same.jpg)
![B](https://example.com/same.jpg)
"""
        images = extract_image_urls(md)
        assert len(images) == 1

    def test_skips_non_http(self):
        md = "![Local](data:image/png;base64,abc)"
        images = extract_image_urls(md)
        assert len(images) == 0


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------


class TestStorage:
    def test_default_backend_is_local(self):
        assert get_storage_backend() == "local"

    def test_local_blob_url(self):
        url = get_blob_url("images/abc/123.jpg")
        assert "images/abc/123.jpg" in url

    def test_s3_blob_url(self):
        import os
        os.environ["CURIA_STORAGE_BACKEND"] = "s3"
        os.environ["CURIA_S3_BUCKET"] = "test-bucket"
        os.environ["CURIA_S3_REGION"] = "us-west-2"
        try:
            url = get_blob_url("images/abc/123.jpg")
            assert "test-bucket" in url
            assert "images/abc/123.jpg" in url
        finally:
            os.environ.pop("CURIA_STORAGE_BACKEND", None)
            os.environ.pop("CURIA_S3_BUCKET", None)
            os.environ.pop("CURIA_S3_REGION", None)

    def test_s3_custom_endpoint(self):
        import os
        os.environ["CURIA_STORAGE_BACKEND"] = "s3"
        os.environ["CURIA_S3_BUCKET"] = "my-bucket"
        os.environ["CURIA_S3_ENDPOINT"] = "https://r2.cloudflarestorage.com"
        try:
            url = get_blob_url("images/test.jpg")
            assert url.startswith("https://r2.cloudflarestorage.com")
            assert "my-bucket" in url
        finally:
            os.environ.pop("CURIA_STORAGE_BACKEND", None)
            os.environ.pop("CURIA_S3_BUCKET", None)
            os.environ.pop("CURIA_S3_ENDPOINT", None)


class TestLocalUpload:
    @pytest.mark.asyncio
    async def test_upload_local_creates_file(self, tmp_path):
        import os
        os.environ["CURIA_STORAGE_BACKEND"] = "local"
        os.environ["CURIA_STORAGE_LOCAL_DIR"] = str(tmp_path)
        try:
            from core.storage.blob import upload_blob
            url = await upload_blob(
                data=b"\x89PNG fake image data" * 100,
                key="images/test-source/abc.png",
                content_type="image/png",
            )
            assert (tmp_path / "images" / "test-source" / "abc.png").exists()
            assert "abc.png" in url
        finally:
            os.environ.pop("CURIA_STORAGE_BACKEND", None)
            os.environ.pop("CURIA_STORAGE_LOCAL_DIR", None)

    @pytest.mark.asyncio
    async def test_upload_file_local(self, tmp_path):
        import os
        os.environ["CURIA_STORAGE_BACKEND"] = "local"
        os.environ["CURIA_STORAGE_LOCAL_DIR"] = str(tmp_path / "blobs")
        try:
            src = tmp_path / "episode.mp3"
            src.write_bytes(b"\xff\xfb\x90\x00" * 100)
            from core.storage.blob import upload_file
            url = await upload_file(
                file_path=str(src),
                key="audio/test-ep.mp3",
                content_type="audio/mpeg",
            )
            assert (tmp_path / "blobs" / "audio" / "test-ep.mp3").exists()
            assert "test-ep.mp3" in url
        finally:
            os.environ.pop("CURIA_STORAGE_BACKEND", None)
            os.environ.pop("CURIA_STORAGE_LOCAL_DIR", None)


class TestPublicUrl:
    def test_s3_public_url_override(self):
        import os
        os.environ["CURIA_STORAGE_BACKEND"] = "s3"
        os.environ["CURIA_S3_PUBLIC_URL"] = "https://pub-abc123.r2.dev"
        try:
            url = get_blob_url("audio/episode-1.mp3")
            assert url == "https://pub-abc123.r2.dev/audio/episode-1.mp3"
        finally:
            os.environ.pop("CURIA_STORAGE_BACKEND", None)
            os.environ.pop("CURIA_S3_PUBLIC_URL", None)
