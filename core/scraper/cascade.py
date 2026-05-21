"""
core/scraper/cascade.py
Cascading scraper: validate → HEAD check → trafilatura (free) → Jina (free) → firecrawl (credits) → fail.
All URLs (including Twitter/X) go through the full pipeline.
Each tier catches its own errors and falls through to the next.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from loguru import logger

from core.errors import PermanentError
from .validator import validate_url, is_likely_paywalled, is_twitter_url


def normalize_url(url: str) -> str:
    m = re.match(r"https?://(?:www\.)?substack\.com/pub/([^/]+)/p/(.+)", url)
    if m:
        author, slug = m.group(1), m.group(2)
        canonical = f"https://{author}.substack.com/p/{slug}"
        logger.info(f"[scraper] normalized substack URL: {url} → {canonical}")
        return canonical
    return url


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


async def resolve_redirects(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.head(url)
            final = str(resp.url)
            if final != url:
                logger.info(f"[scraper] redirect {url} → {final}")
            return final
    except Exception:
        return url


async def scrape(url: str) -> tuple[str, str]:
    url = normalize_url(url)

    result = validate_url(url)
    if not result.valid:
        raise PermanentError(result.reason)

    url = await resolve_redirects(url)

    head = await head_check(url)
    if not head.ok:
        raise PermanentError(head.reason)

    hostname = (urlparse(url).hostname or "").lower()
    is_paywall_domain = is_likely_paywalled(hostname)

    # Tier 1: trafilatura (free, local, no network dependency)
    try:
        content, title = await _scrape_trafilatura(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] trafilatura success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] trafilatura returned too little ({len(content.strip()) if content else 0} chars), trying jina...")
    except Exception as e:
        logger.info(f"[scraper] trafilatura failed ({e}), trying jina...")

    # Tier 2: Jina Reader (free, handles JS-rendered pages)
    try:
        content, title = await _scrape_jina(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] jina success: {len(content)} chars")
            return content.strip(), title or url
        logger.info(f"[scraper] jina returned too little ({len(content.strip()) if content else 0} chars), trying firecrawl...")
    except Exception as e:
        logger.info(f"[scraper] jina failed ({e}), trying firecrawl...")

    # Tier 3: Firecrawl (paid credits, headless browser)
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
        raise PermanentError(
            "Could not extract content — this article is likely behind a paywall. "
            "Try a non-paywalled source or check if the article has a free version."
        )

    if is_twitter_url(url):
        raise PermanentError(
            "Could not extract this Twitter/X thread. "
            "The thread may be deleted, private, or too short to extract."
        )

    raise PermanentError(f"Could not extract content from {url} — tried trafilatura, jina, and firecrawl")


# ---------------------------------------------------------------------------
# Tier 1: trafilatura (free, local)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Tier 2: Jina Reader (free, JS-capable)
# ---------------------------------------------------------------------------


async def _scrape_jina(url: str) -> tuple[str, str]:
    jina_url = f"https://r.jina.ai/{url}"
    headers = {"Accept": "application/json"}

    api_key = os.getenv("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(jina_url, headers=headers)

    if resp.status_code != 200:
        raise RuntimeError(f"Jina Reader error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    inner = data.get("data", data)
    content = inner.get("content", "") or ""
    title = inner.get("title", "") or ""

    return content, title


# ---------------------------------------------------------------------------
# Tier 3: Firecrawl (paid credits, headless browser)
# ---------------------------------------------------------------------------


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
