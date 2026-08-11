"""
Article enrichment — optional accuracy refinement on top of scoring_service's
no-LLM ranking step.

scoring_service.rank_articles() clusters and scores articles using only their
title + the weak Google-News-RSS description (usually just the title again).
This module tries to replace that with real article-body text scraped from
the publisher, then re-scores topic similarity using the richer text. Verified
against real articles from this project's test conversations: it correctly
pushed known duplicate stories (different outlets, same event) above the
clustering similarity threshold that title-only text was missing, without
pulling genuinely different stories closer together.

It is a pure best-effort refinement, not a required step — rank_articles()
works completely fine without it (that's what "no enrichment" already looked
like, and it's been validated on its own). Disable it any time with the single
flag below; nothing else needs to change.
"""
import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# THE ON/OFF SWITCH. Set to False (or env ENABLE_ARTICLE_ENRICHMENT=false) to
# fully disable this feature — rank_articles() will skip straight to its
# existing title+RSS-description scoring with no other code changes needed.
# ---------------------------------------------------------------------------
ENRICHMENT_ENABLED = os.getenv("ENABLE_ARTICLE_ENRICHMENT", "true").lower() in ("true", "1", "yes")

# How many articles to decode+fetch concurrently. googlenewsdecoder hits an
# undocumented Google endpoint and self-throttles (see DECODE_INTERVAL_SECONDS
# below) — keep this moderate rather than maximizing throughput, to avoid
# getting rate-limited. Also directly bounds peak memory (each concurrent
# fetch holds a full page response + extraction buffer in memory at once) —
# kept lower than throughput alone would justify since memory-constrained
# hosted deployments (e.g. Railway's default plan) have OOM-killed the
# process under previous higher values (12, then 6) — 6 was still enough to
# OOM-kill user_brief_runner.generate_brief_for_user, which chains rank/
# enrich, a second content-fetch round, and multiple segment-LLM calls in one
# request instead of Pre-Opt's one-topic-per-request granularity, so its peak
# concurrent memory is higher than what 6 was tuned against.
MAX_CONCURRENT_FETCHES = 3

# Hard per-article budget (decode + fetch + extract combined). Measured
# real-world cost was ~2.2-2.8s decode + up to ~2s fetch; this leaves margin.
PER_ARTICLE_TIMEOUT_SECONDS = 6.0

# Courtesy delay googlenewsdecoder sleeps internally after each decode, purely
# to avoid hammering Google's redirect-resolution endpoint.
DECODE_INTERVAL_SECONDS = 1

# Minimum extracted body length to trust — very short "extractions" are
# usually boilerplate/nav text that slipped through, not real article content.
MIN_USABLE_TEXT_CHARS = 200

# How many words of the extracted body to use for re-embedding. Enough to
# capture the lede's concrete facts without exceeding MiniLM's input window.
LEAD_WORD_COUNT = 120

# Word cap for the full body text handed to Outline/Transcript (see
# fetch_full_texts below) — generous, a normal article body is well under
# this; only a safety net against pathological pages (live blogs, long-form
# features) blowing up prompt size for the handful of selected articles.
FULL_TEXT_WORD_CAP = 1500

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def _warm_html_parser() -> None:
    """One-time libxml2 global-state init, forced onto a single thread before
    any concurrent enrichment fetch can touch it. libxml2 (which trafilatura's
    HTML parsing sits on top of) lazily initializes shared global state on its
    first use, and that lazy init is itself not thread-safe — if two
    _decode_and_fetch_text worker threads both call trafilatura.extract() for
    the first time in this process concurrently, the race corrupts that
    global state and segfaults the whole server (observed: consistently on
    the very first generate-brief call after every restart, at the same point
    in the enrichment fetch pass — exactly the "first concurrent use" shape
    of this bug). Mirrors scoring_service.warm_model()'s same
    pay-the-one-time-cost-at-boot pattern.
    """
    import trafilatura
    trafilatura.extract("<html><body><p>" + "warm " * 50 + "</p></body></html>")


async def warm_html_parser() -> None:
    await asyncio.to_thread(_warm_html_parser)


def _decode_and_fetch_text(google_url: str) -> "Tuple[Optional[str], Optional[str]]":
    """Synchronous, blocking: decode a Google News redirect URL to the real
    publisher URL, fetch it, and extract the article body text. Runs inside a
    worker thread (see _try_one) — never called directly from async code.
    Returns None on ANY failure: decode failure, HTTP block/error, or empty
    extraction. Every failure mode is treated identically on purpose — the
    caller doesn't need to distinguish why enrichment didn't work, only that
    it didn't, so it can fall back to the existing weak-description text.
    """
    try:
        import requests
        import trafilatura
        from googlenewsdecoder import gnewsdecoder

        decoded = gnewsdecoder(google_url, interval=DECODE_INTERVAL_SECONDS)
        if not decoded.get("status") or not decoded.get("decoded_url"):
            logger.info(f"[enrich] decode_failed url={google_url[:60]}")
            return None, None
        real_url = decoded.get("decoded_url")

        resp = requests.get(real_url, headers=_HEADERS, timeout=PER_ARTICLE_TIMEOUT_SECONDS)
        if resp.status_code != 200:
            logger.info(f"[enrich] fetch_blocked status={resp.status_code} url={real_url[:60]}")
            return real_url, None

        extracted = trafilatura.extract(
            resp.text, include_comments=False, include_tables=False, favor_precision=True,
        )
        if not extracted or len(extracted) < MIN_USABLE_TEXT_CHARS:
            logger.info(f"[enrich] extract_empty url={real_url[:60]}")
            return real_url, None
        return real_url, extracted
    except Exception as e:
        logger.info(f"[enrich] decode_or_fetch_error {type(e).__name__} url={google_url[:60]}")
        return None, None


async def _try_one(url: str, semaphore: asyncio.Semaphore) -> Optional[Tuple[str, Optional[str]]]:
    """Returns (text, resolved_publisher_url) on success — resolved url is
    None when text came from rendering the Google redirect directly — or
    None when every path failed."""
    async with semaphore:
        real_url = None
        try:
            real_url, text = await asyncio.wait_for(
                asyncio.to_thread(_decode_and_fetch_text, url),
                timeout=PER_ARTICLE_TIMEOUT_SECONDS + 2.0,
            )
            if text:
                return text, real_url
        except Exception:
            pass
        # Rescue via the host scraper cascade (trafilatura → jina → firecrawl)
        # when the fast requests path failed but the decode gave us the
        # publisher URL.
        if real_url:
            try:
                from core.scraper.cascade import scrape
                content, *_ = await asyncio.wait_for(scrape(real_url), timeout=20.0)
                if content and len(content) >= MIN_USABLE_TEXT_CHARS:
                    logger.info(f"[enrich] rescued_via_cascade url={real_url[:60]}")
                    return content, real_url
            except Exception:
                pass
        # Last resort when even the DECODE failed: point the cascade at the
        # Google News URL itself — Jina/Firecrawl render JS and ride the
        # redirect to the publisher, which the fast requests path cannot.
        else:
            try:
                from core.scraper.cascade import scrape
                content, *_ = await asyncio.wait_for(scrape(url), timeout=25.0)
                if content and len(content) >= MIN_USABLE_TEXT_CHARS:
                    logger.info(f"[enrich] rescued_google_url url={url[:60]}")
                    return content, None
            except Exception:
                pass
        return None


async def _try_one_tagged(url: str, semaphore: asyncio.Semaphore) -> Tuple[str, Optional[Tuple[str, Optional[str]]]]:
    hit = await _try_one(url, semaphore)
    return url, hit


async def _race_urls(urls: List[str], semaphore: asyncio.Semaphore) -> Optional[Tuple[str, str]]:
    """Try every url concurrently, return (text, url) for whichever succeeds
    first; cancels the rest once one succeeds. None if every url fails.
    Same race pattern as the cluster-borrowing used for Step 2b re-scoring
    (see _enrich_cluster below, which is just this with the url discarded),
    but also reports which url actually won so a caller that cares about
    attribution (Step 3b's same-story fallback) can show it correctly."""
    if not urls:
        return None
    tasks = [asyncio.create_task(_try_one_tagged(u, semaphore)) for u in urls]
    try:
        for coro in asyncio.as_completed(tasks):
            url, hit = await coro
            if hit:
                return hit[0], url
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
    return None


async def _enrich_cluster(urls: List[str], semaphore: asyncio.Semaphore) -> Optional[str]:
    """Cluster-borrowing: a cluster only needs ONE of its member outlets to be
    scrapeable, not all of them — a story covered by 3 outlets has 3 chances
    to succeed, not 1. Tries every member concurrently, returns whichever
    succeeds first, cancels the rest. Returns None if every member fails."""
    result = await _race_urls(urls, semaphore)
    return result[0] if result else None


async def enrich_and_rescore(
    ranked: List[Dict[str, Any]],
    cluster_member_urls: Dict[int, List[str]],
    interests: List[str],
    topic_embeddings: Dict[str, Any],
) -> None:
    """Mutates `ranked` in place. For each non-local entry with known cluster
    member URLs, attempts to fetch real article text and — only if that
    succeeds — recomputes its topic-similarity score and composite using the
    richer text, and updates its description. Local entries have no topic
    anchors to refine against (see scoring_service module docstring) and are
    skipped entirely. Entries where enrichment fails (blocked, timed out,
    nothing to try) are left completely untouched, so a partial-failure run
    still returns a fully valid ranking."""
    # Deferred import: scoring_service imports this module at load time, so a
    # top-level import here would be circular. Safe by the time this function
    # actually runs (called from within scoring_service.rank_articles).
    from brief.scoring import TS_WEIGHT, RS_WEIGHT, _cos, _embed_texts

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    candidate_indices = [
        i for i, e in enumerate(ranked)
        if not e.get("is_local") and cluster_member_urls.get(i)
    ]
    if not candidate_indices:
        return

    enriched_texts = await asyncio.gather(
        *[_enrich_cluster(cluster_member_urls[i], semaphore) for i in candidate_indices]
    )

    succeeded = 0
    scoring_candidates: List[Dict[str, Any]] = []
    for i, body_text in zip(candidate_indices, enriched_texts):
        if not body_text:
            continue
        entry = ranked[i]
        lead = " ".join(body_text.split()[:LEAD_WORD_COUNT])
        combined = f"{entry['title']} {lead}".strip()
        scoring_candidates.append({"entry": entry, "lead": lead, "combined": combined})

    if scoring_candidates:
        # Core embeddings API (the fix for the dangling `model` reference the
        # torch removal left here — this call site silently NameError'd from
        # 8075332 until 2026-08-10, so enrichment never actually ran). One
        # batched async call, same as the rest of brief/scoring.py.
        embeddings = await _embed_texts([c["combined"] for c in scoring_candidates])

        for c, emb in zip(scoring_candidates, embeddings):
            entry = c["entry"]
            topic_scores = [
                float(_cos([emb], anchor_emb)[0].max())
                for topic_name, anchor_emb in topic_embeddings.items()
                if topic_name in interests
            ]
            if not topic_scores:
                continue
            new_ts = max(topic_scores)

            # Re-derive RS from the already-stored significance score rather
            # than recomputing cluster/heuristic significance from scratch —
            # enrichment only ever refines the topic-similarity half of the
            # composite.
            rs_fraction = entry["scores"]["significance"] / 10.0
            boost = entry["scores"].get("multiTopicBoost", 0.0)
            new_composite = round((new_ts * TS_WEIGHT + rs_fraction * RS_WEIGHT) * 10 + boost, 2)

            entry["scores"]["topicSimilarity"] = round(new_ts * 10, 1)
            entry["composite"] = new_composite
            entry["description"] = c["lead"]
            entry["enriched"] = True
            succeeded += 1

    # loguru doesn't interpolate stdlib %-style args — the counts were
    # silently dropped from the log line (printed literally as %d/%d).
    logger.info(
        f"enrich_and_rescore: {succeeded}/{len(candidate_indices)} candidate clusters enriched successfully"
    )


def _cap_words(text: str) -> str:
    return " ".join(text.split()[:FULL_TEXT_WORD_CAP])


async def fetch_full_texts(selections: List[Dict[str, Any]]) -> List[Optional[Dict[str, str]]]:
    """Fetch real article body text for a small, known set of selections —
    e.g. the 5 articles Step 3 picked for the daily brief — so Outline/
    Transcript can be written from real reporting instead of a title + one-
    sentence reason.

    Each selection needs a "url" and, optionally, a "cluster_alternates" list
    of {"url", "source"} dicts for other outlets that covered the exact same
    event (see scoring_service._embed_cluster_score, which computes this at
    clustering time — every member of an event cluster other than the one
    kept as representative). The selection's own url is tried first (that's
    the outlet actually picked); only if that fails does this race the
    cluster alternates concurrently (_race_urls) and take whichever succeeds
    first, so one blocked/paywalled publisher doesn't leave a selection with
    nothing when a same-story alternate would have worked.

    Reuses the same decode-redirect + scrape + extract pipeline as
    enrich_and_rescore (_try_one/_decode_and_fetch_text), unmodified, just
    keeping the full body (capped) instead of trimming to a 120-word lead —
    there's no re-embedding step here, so no need to keep it short.

    Returns, per selection (same order/length as `selections`), either
    {"text", "url", "source"} — url/source reflect whichever outlet actually
    supplied the text, which may not be the selection's original pick — or
    `None` if the primary url and every alternate failed. Never raises.
    """
    if not selections:
        return []

    semaphore = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def _one(sel: Dict[str, Any]) -> Optional[Dict[str, str]]:
        primary_url = sel.get("url") or ""
        if primary_url:
            hit = await _try_one(primary_url, semaphore)
            if hit:
                text, resolved = hit
                return {"text": _cap_words(text), "url": primary_url,
                        "source": sel.get("source", ""), "resolved_url": resolved}

        alternates = sel.get("cluster_alternates") or []
        alt_urls = [a.get("url") for a in alternates if a.get("url")]
        if not alt_urls:
            return None

        raced = await _race_urls(alt_urls, semaphore)
        if not raced:
            return None
        text, used_url = raced
        used_source = next(
            (a.get("source", "") for a in alternates if a.get("url") == used_url), ""
        )
        return {"text": _cap_words(text), "url": used_url, "source": used_source,
                "resolved_url": None}

    return await asyncio.gather(*[_one(sel) for sel in selections])
