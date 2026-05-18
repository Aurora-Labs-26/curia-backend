"""
core/scraper/cascade.py
Cascading scraper: validate → trafilatura (free) → firecrawl (credits) → fail.
"""

from __future__ import annotations

import os

import httpx
from loguru import logger

from .validator import validate_url

MIN_CONTENT_LENGTH = 200


async def scrape(url: str) -> tuple[str, str]:
    """
    Scrape a URL using cascading fallbacks. Returns (content, title).
    Raises ValueError with a clear reason on failure.
    """
    # Step 0: Validate
    result = validate_url(url)
    if not result.valid:
        raise ValueError(result.reason)

    # Step 1: Trafilatura (free, fast, no JS)
    try:
        content, title = await _scrape_trafilatura(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] trafilatura success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] trafilatura returned too little ({len(content.strip()) if content else 0} chars), trying firecrawl...")
    except Exception as e:
        logger.info(f"[scraper] trafilatura failed ({e}), trying firecrawl...")

    # Step 2: Firecrawl (uses credits)
    try:
        content, title = await _scrape_firecrawl(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] firecrawl success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] firecrawl returned too little ({len(content.strip()) if content else 0} chars)")
    except Exception as e:
        logger.warning(f"[scraper] firecrawl failed: {e}")

    # Step 3: Give up
    raise ValueError(f"Could not extract content from {url} — tried trafilatura and firecrawl")


async def _scrape_trafilatura(url: str) -> tuple[str, str]:
    """Free, fast, no JS rendering. Works for static blogs and articles."""
    import asyncio
    import trafilatura

    def _fetch_and_extract():
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return "", ""
        content = trafilatura.extract(
            downloaded, include_comments=False, include_tables=False
        ) or ""
        title = ""
        try:
            from trafilatura.metadata import extract_metadata
            meta = extract_metadata(downloaded)
            if meta and meta.title:
                title = meta.title
        except Exception:
            pass
        return content, title

    return await asyncio.to_thread(_fetch_and_extract)


async def _scrape_firecrawl(url: str) -> tuple[str, str]:
    """Firecrawl API — full browser rendering, handles JS + paywalls."""
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY not set — cannot use firecrawl fallback")

    base_url = os.getenv("FIRECRAWL_BASE_URL", "https://api.firecrawl.dev/v1")

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            f"{base_url}/scrape",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": url,
                "formats": ["markdown"],
            },
        )

    if resp.status_code != 200:
        raise RuntimeError(f"Firecrawl error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    result = data.get("data", {})
    content = result.get("markdown", "") or ""
    title = result.get("metadata", {}).get("title", "") or ""

    return content, title
