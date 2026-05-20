"""
core/scraper/images.py
Extract image URLs from scraped markdown content, download, and upload to blob storage.
"""

from __future__ import annotations

import hashlib
import mimetypes
import re
from urllib.parse import urlparse

import httpx
from loguru import logger

from core.storage import upload_blob

_IMG_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")

_SKIP_EXTENSIONS = {".svg", ".ico", ".gif"}
_SKIP_DOMAINS = {"gravatar.com", "platform.twitter.com", "abs.twimg.com"}
MIN_IMAGE_BYTES = 5_000


def extract_image_urls(markdown: str) -> list[dict]:
    matches = _IMG_PATTERN.findall(markdown)
    seen = set()
    results = []
    for alt, url in matches:
        url = url.strip()
        if url in seen:
            continue
        seen.add(url)

        parsed = urlparse(url)
        ext = (parsed.path.rsplit(".", 1)[-1] if "." in parsed.path else "").lower()
        if f".{ext}" in _SKIP_EXTENSIONS:
            continue
        hostname = (parsed.hostname or "").lower()
        if any(d in hostname for d in _SKIP_DOMAINS):
            continue
        if not url.startswith("http"):
            continue

        results.append({"alt": alt, "url": url})
    return results


async def download_and_upload_images(
    images: list[dict],
    source_id: str,
    max_images: int = 10,
) -> list[dict]:
    uploaded = []
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        for img in images[:max_images]:
            try:
                resp = await client.get(img["url"])
                if resp.status_code != 200:
                    continue
                data = resp.content
                if len(data) < MIN_IMAGE_BYTES:
                    continue

                content_type = resp.headers.get("content-type", "image/jpeg").split(";")[0]
                ext = mimetypes.guess_extension(content_type) or ".jpg"
                url_hash = hashlib.sha256(img["url"].encode()).hexdigest()[:12]
                key = f"images/{source_id}/{url_hash}{ext}"

                blob_url = await upload_blob(data=data, key=key, content_type=content_type)
                uploaded.append({
                    "original_url": img["url"],
                    "blob_url": blob_url,
                    "alt": img["alt"],
                    "size_bytes": len(data),
                    "content_type": content_type,
                })
                logger.debug(f"[images] uploaded {img['url'][:60]} → {key}")
            except Exception as e:
                logger.debug(f"[images] skip {img['url'][:60]}: {e}")
                continue

    logger.info(f"[images] {len(uploaded)}/{len(images)} images uploaded for source {source_id}")
    return uploaded
