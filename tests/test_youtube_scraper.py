"""
tests/test_youtube_scraper.py
Unit tests for core/scraper/youtube.py (v2.9 YouTube source support).

All mock-only: the network-bound helpers `_fetch_transcript_sync` (youtube-transcript-api)
and `_fetch_metadata_sync` (yt-dlp) are patched, so neither library needs to be installed.
"""

from unittest.mock import MagicMock, patch

import pytest

from core.errors import PermanentError
from core.scraper import youtube
from core.scraper.youtube import extract_video_id, scrape_youtube

# A valid 11-char YouTube video id.
VID = "dQw4w9WgXcQ"


# ---------------------------------------------------------------------------
# extract_video_id (pure)
# ---------------------------------------------------------------------------


class TestExtractVideoId:
    def test_watch_url(self):
        assert extract_video_id(f"https://www.youtube.com/watch?v={VID}") == VID

    def test_short_youtu_be(self):
        assert extract_video_id(f"https://youtu.be/{VID}") == VID

    def test_embed_url(self):
        assert extract_video_id(f"https://www.youtube.com/embed/{VID}") == VID

    def test_shorts_url(self):
        assert extract_video_id(f"https://www.youtube.com/shorts/{VID}") == VID

    def test_extra_query_params(self):
        assert extract_video_id(f"https://www.youtube.com/watch?v={VID}&t=42s") == VID

    def test_non_youtube_returns_none(self):
        assert extract_video_id("https://example.com/article/123") is None

    def test_no_id_returns_none(self):
        assert extract_video_id("https://www.youtube.com/") is None


# ---------------------------------------------------------------------------
# scrape_youtube (async, 5-tuple contract)
# ---------------------------------------------------------------------------


def _meta(**over):
    base = {
        "title": "Great Talk",
        "author": "Some Channel",
        "thumbnail": "https://img.youtube.com/vi/x/hq.jpg",
        "duration_seconds": 615,
        "upload_date": "20240115",
        "view_count": 12345,
        "channel": "Some Channel",
    }
    base.update(over)
    return base


def _transcript(text="This is the transcript body with plenty of words."):
    return text, {"video_id": VID, "language": "en", "is_generated": True}


class TestScrapeYoutube:
    async def test_happy_path_returns_5_tuple(self):
        with patch.object(youtube, "_fetch_transcript_sync", MagicMock(return_value=_transcript())), \
             patch.object(youtube, "_fetch_metadata_sync", MagicMock(return_value=_meta())):
            result = await scrape_youtube(f"https://youtu.be/{VID}")

        assert isinstance(result, tuple) and len(result) == 5
        content, title, author, og_image, extra = result
        assert content.startswith("This is the transcript")
        assert title == "Great Talk"
        assert author == "Some Channel"
        assert og_image == "https://img.youtube.com/vi/x/hq.jpg"
        assert isinstance(extra, dict)

    async def test_extra_data_merges_transcript_and_metadata(self):
        with patch.object(youtube, "_fetch_transcript_sync", MagicMock(return_value=_transcript())), \
             patch.object(youtube, "_fetch_metadata_sync", MagicMock(return_value=_meta())):
            _, _, _, _, extra = await scrape_youtube(f"https://youtu.be/{VID}")

        # transcript-side fields
        assert extra["video_id"] == VID
        assert extra["language"] == "en"
        assert extra["is_generated"] is True
        # metadata-side fields (title/author/thumbnail are promoted out, not in extra)
        assert extra["duration_seconds"] == 615
        assert extra["upload_date"] == "20240115"
        assert extra["view_count"] == 12345
        assert "title" not in extra
        assert "author" not in extra
        assert "thumbnail" not in extra

    async def test_title_falls_back_to_video_id_when_metadata_empty(self):
        # yt-dlp failed → returns {} → title=video_id, author="", og_image=""
        with patch.object(youtube, "_fetch_transcript_sync", MagicMock(return_value=_transcript())), \
             patch.object(youtube, "_fetch_metadata_sync", MagicMock(return_value={})):
            _, title, author, og_image, _ = await scrape_youtube(f"https://youtu.be/{VID}")

        assert title == VID
        assert author == ""
        assert og_image == ""

    async def test_empty_transcript_raises_permanent(self):
        with patch.object(youtube, "_fetch_transcript_sync", MagicMock(return_value=("   ", {}))), \
             patch.object(youtube, "_fetch_metadata_sync", MagicMock(return_value=_meta())):
            with pytest.raises(PermanentError, match="empty"):
                await scrape_youtube(f"https://youtu.be/{VID}")

    async def test_unparseable_url_raises_permanent(self):
        with pytest.raises(PermanentError, match="video ID"):
            await scrape_youtube("https://example.com/not-a-video")

    def test_ip_block_raises_permanent_not_transient(self):
        # YouTube blocks datacenter IPs; retries share one NAT IP so this must be
        # permanent (transient handling left sources stuck on "scraping" forever).
        from youtube_transcript_api import RequestBlocked

        api = MagicMock()
        api.return_value.fetch.side_effect = RequestBlocked(VID)
        with patch("youtube_transcript_api.YouTubeTranscriptApi", api):
            with pytest.raises(PermanentError, match="blocked"):
                youtube._fetch_transcript_sync(VID)
