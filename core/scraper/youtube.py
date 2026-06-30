"""
core/scraper/youtube.py
YouTube transcript + metadata extraction. No API key required.

Transcript: youtube-transcript-api (reads YouTube's caption tracks directly).
Metadata (title/author/thumbnail/duration/upload_date): yt-dlp, metadata-only (skip_download).
"""

from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from loguru import logger

from core.errors import PermanentError

_VIDEO_ID_PATTERNS = (
    re.compile(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})"),
)


def extract_video_id(url: str) -> Optional[str]:
    for pattern in _VIDEO_ID_PATTERNS:
        m = pattern.search(url)
        if m:
            return m.group(1)
    return None


def _build_proxy_config():
    if os.getenv("YOUTUBE_USE_TOR", "false").lower() not in ("1", "true", "yes"):
        return None
    from youtube_transcript_api.proxies import GenericProxyConfig
    return GenericProxyConfig(
        http_url="socks5://127.0.0.1:9050",
        https_url="socks5://127.0.0.1:9050",
    )


def _fetch_transcript_sync(video_id: str) -> tuple[str, dict]:
    from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound, VideoUnavailable

    ytt = YouTubeTranscriptApi(proxy_config=_build_proxy_config())
    try:
        transcript = ytt.fetch(video_id)
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        raise PermanentError(f"No transcript available for this YouTube video: {e}")

    content = " ".join(s.text for s in transcript)
    extra = {
        "video_id": video_id,
        "language": transcript.language_code,
        "is_generated": transcript.is_generated,
    }
    return content, extra


def _fetch_metadata_sync(url: str) -> dict:
    import yt_dlp

    opts = {"quiet": True, "skip_download": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logger.warning(f"[youtube] yt-dlp metadata fetch failed: {e}")
        return {}

    return {
        "title": info.get("title") or "",
        "author": info.get("uploader") or "",
        "thumbnail": info.get("thumbnail") or "",
        "duration_seconds": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "channel": info.get("uploader"),
    }


async def scrape_youtube(url: str) -> tuple[str, str, str, str, dict]:
    """
    Returns (content, title, author, og_image, extra_data).
    extra_data is merged into source.data JSONB by the caller.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise PermanentError(f"Could not extract a video ID from URL: {url}")

    content, transcript_extra = await asyncio.to_thread(_fetch_transcript_sync, video_id)
    if not content.strip():
        raise PermanentError("YouTube transcript was empty")

    metadata = await asyncio.to_thread(_fetch_metadata_sync, url)

    title = metadata.get("title") or video_id
    author = metadata.get("author") or ""
    og_image = metadata.get("thumbnail") or ""

    extra_data = {**transcript_extra, **{k: v for k, v in metadata.items() if k not in ("title", "author", "thumbnail")}}

    logger.info(f"[youtube] scraped {video_id}: {len(content)} chars, title={title!r}")
    return content, title, author, og_image, extra_data
