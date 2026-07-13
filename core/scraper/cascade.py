"""
core/scraper/cascade.py
Cascading scraper: validate → HEAD check → trafilatura (free) → firecrawl (credits) → fail.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from loguru import logger

from core.errors import PermanentError
from .validator import validate_url, is_likely_paywalled, is_twitter_url, is_youtube_url


def normalize_url(url: str) -> str:
    """
    Rewrite known broken URL patterns to their canonical form before scraping.

    - substack.com/pub/<author>/p/<slug> → <author>.substack.com/p/<slug>
    """
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
    """Follow redirects and return the final URL. Used to unwrap share/redirect URLs like open.substack.com."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.head(url)
            final = str(resp.url)
            if final != url:
                logger.info(f"[scraper] redirect {url} → {final}")
            return final
    except Exception:
        return url


async def scrape(url: str) -> tuple[str, str, str, str, dict]:
    # Normalize known broken URL patterns before anything else
    url = normalize_url(url)

    result = validate_url(url)
    if not result.valid:
        raise PermanentError(result.reason)

    if is_youtube_url(url):
        logger.info(f"[scraper] YouTube URL detected — fetching transcript")
        from .youtube import scrape_youtube
        return await scrape_youtube(url)

    # Resolve any redirects (e.g. open.substack.com share links → real article URL)
    url = await resolve_redirects(url)

    hostname = (urlparse(url).hostname or "").lower()
    is_paywall_domain = is_likely_paywalled(hostname)
    is_twitter = is_twitter_url(url)

    if is_twitter:
        logger.info(f"[scraper] Twitter/X URL detected — going straight to firecrawl")
        return await _try_firecrawl_or_fail(url, is_paywall_domain)

    head = await head_check(url)
    if not head.ok:
        raise PermanentError(head.reason)

    try:
        content, title, author, og_image = await _scrape_trafilatura(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] trafilatura success: {len(content)} chars")
            return content.strip(), title or url, author, og_image, {}
        logger.info(f"[scraper] trafilatura returned too little ({len(content.strip()) if content else 0} chars), trying next tier...")
    except Exception as e:
        logger.info(f"[scraper] trafilatura failed ({e}), trying next tier...")

    # Tier 2: Jina Reader (free/cheap hosted extraction) — only when a key is
    # configured; without JINA_API_KEY the cascade behaves exactly as before.
    if os.getenv("JINA_API_KEY"):
        try:
            content, title, author, og_image = await _scrape_jina(url)
            if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
                logger.info(f"[scraper] jina reader success: {len(content)} chars")
                return content.strip(), title or url, author, og_image, {}
            logger.info(f"[scraper] jina reader returned too little ({len(content.strip()) if content else 0} chars), trying firecrawl...")
        except Exception as e:
            logger.info(f"[scraper] jina reader failed ({e}), trying firecrawl...")

    return await _try_firecrawl_or_fail(url, is_paywall_domain)


async def _scrape_jina(url: str) -> tuple[str, str, str, str]:
    """
    Jina Reader (r.jina.ai) — hosted URL→LLM-ready-markdown extraction.
    Non-generative pipeline; returns (content, title, author, og_image).
    Author/og_image are not provided by Reader → empty strings.
    """
    api_key = os.getenv("JINA_API_KEY")
    base = os.getenv("JINA_READER_URL", "https://r.jina.ai").rstrip("/")
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.get(
            f"{base}/{url}",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"jina reader error {resp.status_code}: {resp.text[:200]}")
    data = resp.json().get("data") or {}
    return (data.get("content") or "", data.get("title") or "", "", "")


async def _try_firecrawl_or_fail(url: str, is_paywall_domain: bool) -> tuple[str, str, str, str, dict]:
    try:
        content, title, author, og_image = await _scrape_firecrawl(url)
        if content and len(content.strip()) >= MIN_CONTENT_LENGTH:
            logger.info(f"[scraper] firecrawl success: {len(content)} chars")
            return content.strip(), title or url, author, og_image, {}
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

    raise PermanentError(f"Could not extract content from {url} — tried trafilatura and firecrawl")


async def _scrape_trafilatura(url: str) -> tuple[str, str, str, str]:
    import asyncio
    import httpx
    import trafilatura

    async def _fetch_html() -> str:
        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
        ) as client:
            resp = await client.get(url)
            return resp.text

    html = await _fetch_html()

    def _extract(html: str):
        content = trafilatura.extract(html, include_comments=False, include_tables=False) or ""
        title = ""
        author = ""
        og_image = ""
        try:
            from trafilatura.metadata import extract_metadata
            meta = extract_metadata(html)
            if meta:
                if meta.title:
                    title = meta.title
                if meta.author:
                    author = meta.author
                elif meta.sitename:
                    author = meta.sitename
                if meta.image:
                    og_image = meta.image
        except Exception:
            pass
        return content, title, author, og_image

    return await asyncio.to_thread(_extract, html)


async def _scrape_firecrawl(url: str) -> tuple[str, str, str, str]:
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
    metadata = result.get("metadata", {})
    title = metadata.get("title", "") or ""
    author = metadata.get("author", "") or metadata.get("ogSiteName", "") or ""
    og_image = metadata.get("ogImage", "") or metadata.get("og:image", "") or ""

    return content, title, author, og_image
