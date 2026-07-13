"""
tests/test_og_image_contract.py
Contract tests for the v2.9 og_image + source-type changes:

- core.scraper.cascade.scrape() now returns a 5-tuple
  (content, title, author, og_image, extra_data)
- og_image is propagated from both the trafilatura and firecrawl paths
- YouTube URLs are routed to scrape_youtube (and no longer rejected by the validator)
- api.schemas.SourceSummary exposes og_image

All network/scraper internals are mocked.
"""

from datetime import datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from core.scraper.cascade import HeadCheckResult, scrape
from core.scraper.validator import ValidationResult, is_youtube_url, validate_url

LONG = "word " * 100  # > MIN_CONTENT_LENGTH (200) after strip


def _article_patches():
    """Common patches to force the non-youtube article path with a passing HEAD check."""
    return [
        patch("core.scraper.cascade.validate_url", return_value=ValidationResult(True)),
        patch("core.scraper.cascade.is_youtube_url", return_value=False),
        patch("core.scraper.cascade.is_twitter_url", return_value=False),
        patch("core.scraper.cascade.resolve_redirects",
              AsyncMock(return_value="https://example.com/a")),
        patch("core.scraper.cascade.head_check",
              AsyncMock(return_value=HeadCheckResult(ok=True, status_code=200))),
    ]


class TestScrapeOgImageContract:
    async def test_trafilatura_path_returns_5_tuple_with_og_image(self):
        patches = _article_patches() + [
            patch("core.scraper.cascade._scrape_trafilatura",
                  AsyncMock(return_value=(LONG, "Title", "Author", "https://img/a.jpg"))),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = await scrape("https://example.com/a")

        assert len(result) == 5
        content, title, author, og_image, extra = result
        assert title == "Title"
        assert author == "Author"
        assert og_image == "https://img/a.jpg"
        assert extra == {}  # non-youtube path carries no extra_data

    async def test_firecrawl_fallback_propagates_og_image(self):
        # trafilatura returns too-little content → falls through to firecrawl
        patches = _article_patches() + [
            patch("core.scraper.cascade._scrape_trafilatura",
                  AsyncMock(return_value=("tiny", "t", "a", "https://traf.jpg"))),
            patch("core.scraper.cascade._scrape_firecrawl",
                  AsyncMock(return_value=(LONG, "FTitle", "FAuthor", "https://fc.jpg"))),
        ]
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            result = await scrape("https://example.com/b")

        assert len(result) == 5
        assert result[3] == "https://fc.jpg"
        assert result[4] == {}

    async def test_youtube_url_routes_to_scrape_youtube(self):
        fake = ("transcript", "Vid Title", "Uploader", "https://yt/thumb.jpg", {"video_id": "abc"})
        with patch("core.scraper.cascade.validate_url", return_value=ValidationResult(True)), \
             patch("core.scraper.cascade.is_youtube_url", return_value=True), \
             patch("core.scraper.youtube.scrape_youtube", AsyncMock(return_value=fake)):
            result = await scrape("https://youtu.be/abc12345678")

        assert result == fake
        assert result[3] == "https://yt/thumb.jpg"       # thumbnail surfaces as og_image
        assert result[4]["video_id"] == "abc"            # transcript extra_data preserved


class TestValidatorYoutube:
    def test_is_youtube_url_true(self):
        assert is_youtube_url("https://www.youtube.com/watch?v=abc")
        assert is_youtube_url("https://youtu.be/abc")

    def test_is_youtube_url_false(self):
        assert not is_youtube_url("https://example.com/article")

    def test_validate_url_allows_youtube(self):
        # v2.9 removed YouTube from the video block-list
        assert validate_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ").valid is True


class TestSourceSummarySchema:
    def test_has_og_image_field_defaulting_none(self):
        from api.schemas import SourceSummary

        assert "og_image" in SourceSummary.model_fields
        s = SourceSummary(id=uuid4(), status="ready", created_at=datetime.now())
        assert s.og_image is None
