"""
core/scraper/cascade.py
Cascading scraper: validate → HEAD check → trafilatura (free) → firecrawl (credits) → fail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from loguru import logger

from .validator import validate_url, is_likely_paywalled, is_twitter_url

MIN_CONTENT_LENGTH = 200


@dataclass
class HeadCheckResult:
    ok: bool
    status_code: int = 0
    reason: str = ""


async def head_check(url: str) -> HeadCheckResult:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.head(url)

        if resp.status_code == 404:
            return HeadCheckResult(ok=False, status_code=404, reason="Page not found (404) — this URL doesn't exist")
        if resp.status_code == 403:
            return HeadCheckResult(ok=False, status_code=403, reason="Access forbidden (403) — this page blocks automated access")
        if resp.status_code >= 500:
            return HeadCheckResult(ok=False, status_code=resp.status_code, reason=f"Server error ({resp.status_code}) — the site is down or broken")

        return HeadCheckResult(ok=True, status_code=resp.status_code)

    except httpx.TimeoutException:
        return HeadCheckResult(ok=True, status_code=0, reason="HEAD timeout — proceeding to scrape")
    except Exception as e:
        return HeadCheckResult(ok=True, status_code=0, reason=f"HEAD check failed: {e}")


async def scrape(url: str) -> tuple[str, str]:
    result = validate_url(url)
    if not result.valid:
        raise ValueError(result.reason)

    head = await head_check(url)
    if not head.ok:
        raise ValueError(head.reason)

    hostname = (urlparse(url).hostname or "").lower()
    is_paywall_domain = is_likely_paywalled(hostname)
    is_twitter = is_twitter_url(url)

    if is_twitter:
        logger.info(f"[scraper] Twitter/X URL detected — going straight to firecrawl")
        return await _try_firecrawl_or_fail(url, is_paywall_domain)

    try:
        content, title = await _scrape_trafilatura(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] trafilatura success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] trafilatura returned too little ({len(content.strip()) if content else 0} chars), trying firecrawl...")
    except Exception as e:
        logger.info(f"[scraper] trafilatura failed ({e}), trying firecrawl...")

    return await _try_firecrawl_or_fail(url, is_paywall_domain)


async def _try_firecrawl_or_fail(url: str, is_paywall_domain: bool) -> tuple[str, str]:
    try:
        content, title = await _scrape_firecrawl(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] firecrawl success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] firecrawl returned too little ({len(content.strip()) if content else 0} chars)")
    except Exception as e:
        logger.warning(f"[scraper] firecrawl failed: {e}")

    if is_paywall_domain:
        raise ValueError(
            "Could not extract content — this article is likely behind a paywall. "
            "Try a non-paywalled source or check if the article has a free version."
        )

    if is_twitter_url(url):
        raise ValueError(
            "Could not extract this Twitter/X thread. "
            "The thread may be deleted, private, or too short to extract."
        )

    raise ValueError(f"Could not extract content from {url} — tried trafilatura and firecrawl")


async def _scrape_trafilatura(url: str) -> tuple[str, str]:
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
