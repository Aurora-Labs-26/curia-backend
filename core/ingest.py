"""
core/ingest.py
Source ingestion pipeline — scrape → store → embed → transform.

Public functions:
    ingest_url(url, user_id)            CLI-friendly: creates row + processes it.
    process_source(source_id)           Worker-friendly: assumes row exists, processes it.
    embed_chunks(source_id, text)       Internal: chunked text embeddings.

Status transitions:
    queued → scraping → transforming → embedding → ready  (happy path)
    any    → failed                                       (with error message)

LLM transformations route through DSPy modules in core/prompts/transformations.py.
"""

import asyncio
import json
import os
import uuid
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from loguru import logger

from . import analytics
from .db.connection import db_execute, db_fetchrow, db_query
from .prompts.transformations import (
    TRANSFORMATION_NAMES,
    transformations as _transformations,
)
from .scraper.prettify import prettify
from .scraper.validator import is_youtube_url

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

ARTICLE_CHAR_CAP = 50_000


# ---------------------------------------------------------------------------
# Atoms
# ---------------------------------------------------------------------------


async def scrape_url(url: str) -> tuple[str, str, str, str, dict]:
    """Validate URL then scrape via cascade (trafilatura → firecrawl → YouTube → fail). Returns (content, title, author, og_image, extra_data)."""
    from core.scraper.cascade import scrape
    return await scrape(url)


def run_transformation(full_text: str, transformation_name: str) -> str:
    return _transformations.run_one(
        article=full_text[:ARTICLE_CHAR_CAP],
        transformation_name=transformation_name,
    )


async def _set_status(source_id: str, status: str, error: Optional[str] = None) -> None:
    await db_execute(
        """
        UPDATE source
        SET status = $status, error = $error, updated_at = now()
        WHERE id = $id::uuid
        """,
        {"id": source_id, "status": status, "error": error},
    )


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


async def embed_chunks(source_id: str, full_text: str) -> None:
    """Chunk text and embed each chunk into source_embedding (HNSW-indexed)."""
    from .embeddings import get_embedding, get_embedding_column

    col = get_embedding_column()
    words = full_text.split()
    chunk_size = 200
    overlap = 20
    chunks = []
    i = 0
    while i < len(words):
        chunks.append(" ".join(words[i:i + chunk_size]))
        i += chunk_size - overlap

    logger.info(f"Embedding {len(chunks)} chunks for source {source_id} (column={col})")
    for idx, chunk in enumerate(chunks):
        vector = await get_embedding(chunk)
        if vector:
            await db_execute(
                f"""
                INSERT INTO source_embedding (source_id, chunk_text, {col}, chunk_index)
                VALUES ($sid::uuid, $chunk, $vec, $idx)
                """,
                {"sid": source_id, "chunk": chunk, "vec": vector, "idx": idx},
            )
        else:
            logger.warning(f"Skipping chunk {idx} — embedding failed")
    logger.info(f"Embedding complete for source {source_id}")


# ---------------------------------------------------------------------------
# Main pipeline (worker entry point)
# ---------------------------------------------------------------------------


async def process_source(source_id: str, is_final_attempt: bool = True) -> None:
    """
    Process a source row that already exists in the DB.
    Reads url + user_id from the row, runs scrape → transform → embed.
    Status moves through 'scraping' → 'transforming' → 'embedding' → 'ready'.

    On exception: the error column is always recorded, but status is only set to
    'failed' on the final retry (is_final_attempt). On intermediate attempts the
    status is left as-is (still 'scraping'/etc.) so the pile shows it as processing
    rather than flashing "Failed" between auto-retries. Always re-raises so the
    worker can requeue.

    Used by the worker handler. Idempotent-ish (insights are appended; safe to re-run if
    insights table is cleared or duplicates are tolerated).
    """
    row = await db_fetchrow(
        """
        SELECT url, user_id, title, full_text, clean_text, source_type, data->>'channel' AS channel
        FROM source WHERE id = $id::uuid
        """,
        {"id": source_id},
    )
    if not row:
        raise ValueError(f"source {source_id} not found")
    url = row["url"]
    user_id = row["user_id"]
    url_domain = urlparse(url).hostname or ""
    source_type = row.get("source_type") or "article"
    title = row.get("title")
    channel = row.get("channel")

    try:
        # 1. Scrape (skip if full_text already populated — supports resumed runs)
        if not row.get("full_text"):
            await _set_status(source_id, "scraping")
            full_text, title, author, og_image, extra_data = await scrape_url(url)
            source_type = "youtube" if is_youtube_url(url) else "article"
            clean_text = prettify(full_text)
            await db_execute(
                """
                UPDATE source
                SET title = $title, full_text = $full_text, clean_text = $clean_text,
                    author = $author, og_image = $og_image,
                    source_type = $source_type, data = data || $extra_data::jsonb, updated_at = now()
                WHERE id = $id::uuid
                """,
                {
                    "id": source_id,
                    "title": title,
                    "full_text": full_text,
                    "clean_text": clean_text,
                    "author": author or None,
                    "og_image": og_image or None,
                    "source_type": source_type,
                    "extra_data": extra_data or {},
                },
            )
            channel = (extra_data or {}).get("channel") or channel
        else:
            full_text = row["full_text"]
            clean_text = row.get("clean_text")
            if not clean_text:
                # Resumed / pre-prettifier row — clean it now (backfill-on-touch).
                clean_text = prettify(full_text)
                await db_execute(
                    "UPDATE source SET clean_text = $ct, updated_at = now() WHERE id = $id::uuid",
                    {"id": source_id, "ct": clean_text},
                )

        # Every LLM/embedding consumer reads the prettified text; raw stays for audit.
        text_for_llm = clean_text or full_text

        # 2. Run transformations in parallel
        await _set_status(source_id, "transforming")
        loop = asyncio.get_running_loop()
        tasks = [
            (name, loop.run_in_executor(None, run_transformation, text_for_llm, name))
            for name in TRANSFORMATION_NAMES
        ]
        insight_contents: dict[str, Optional[str]] = {}
        for insight_type, task in tasks:
            try:
                content = await task
                if content and content.strip().lower() == "null":
                    content = None
                insight_contents[insight_type] = content
                await db_execute(
                    """
                    INSERT INTO source_insight (source_id, insight_type, content)
                    VALUES ($sid::uuid, $itype, $content)
                    """,
                    {"sid": source_id, "itype": insight_type, "content": content},
                )
                logger.info(f"  ✓ {insight_type}")
            except Exception as e:
                logger.warning(f"  ✗ {insight_type}: {e}")

        # 2b. Topics — deterministic pins + LLM judge (see "topics v1.md").
        #     Never fails the ingest; NULL topics = classify again on a future run.
        topics_env = None
        try:
            from core.taxonomy import classify_source

            host = (urlparse(url).hostname or "").lower().removeprefix("www.")
            pin_key = f"yt:{channel}" if (source_type == "youtube" and channel) else host
            pin_row = await db_fetchrow(
                "SELECT tier1, tier2 FROM domain_pins WHERE hostname = $h",
                {"h": pin_key},
            )
            learned_pin = (pin_row["tier1"], pin_row.get("tier2")) if pin_row else None
            text_signal = insight_contents.get("summary") or (text_for_llm or "")[:1500]
            topics_env = await loop.run_in_executor(
                None, classify_source, url, title, text_signal,
                source_type, channel, learned_pin,
            )
            if topics_env is not None:
                await db_execute(
                    "UPDATE source SET topics = $t::jsonb, updated_at = now() WHERE id = $id::uuid",
                    {"id": source_id, "t": topics_env},
                )
                logger.info(f"  ✓ topics ({len(topics_env['tags'])} tag(s))")
            else:
                logger.warning("  ✗ topics: judge failed with no pins — left NULL for retry")
        except Exception as e:
            logger.warning(f"  ✗ topics: {e}")

        # 3. Embed chunks
        await _set_status(source_id, "embedding")
        await embed_chunks(source_id, text_for_llm)

        # 4. Done
        await _set_status(source_id, "ready")
        logger.info(f"Ingestion complete: {source_id}")
        ingested_props = {
            "source_id": source_id,
            "url_domain": url_domain,
            "source_type": source_type,
        }
        if topics_env is not None:
            ingested_props["topics_tier1"] = [t["tier1"] for t in topics_env["tags"]]
            ingested_props["topics_src"] = [t["src"] for t in topics_env["tags"]]
        await analytics.track(user_id, "source_ingested", ingested_props)

    except Exception as e:
        err = str(e)[:1000]
        if is_final_attempt:
            await _set_status(source_id, "failed", error=err)
            await analytics.track(user_id, "source_ingest_failed", {
                "source_id": source_id,
                "url_domain": url_domain,
                "error_type": type(e).__name__,
            })
        else:
            # Retry pending — record the error but keep the in-progress status so
            # the pile doesn't show "Failed" mid-retry.
            await db_execute(
                "UPDATE source SET error = $error, updated_at = now() WHERE id = $id::uuid",
                {"id": source_id, "error": err},
            )
        raise


# ---------------------------------------------------------------------------
# CLI-friendly wrappers (used by scripts/ingest_urls.py)
# ---------------------------------------------------------------------------


async def ingest_url(
    url: str,
    user_id: str = "default",
    pool: str = "user",
) -> str:
    """
    Create a source row (or reuse an existing one for the same user+url) and process it.
    Returns source_id. Idempotent on repeat URLs.
    """
    source_id = await get_or_create_source(url=url, user_id=user_id, pool=pool)
    await process_source(source_id)
    return source_id


def normalise_url(url: str) -> str:
    from urllib.parse import urlparse, urlencode, parse_qs, urlunparse
    url = url.strip()

    # Rewrite open.substack.com → substack.com so trafilatura can scrape it.
    # open.substack.com is Substack's share/preview domain (requires login);
    # the canonical URL on substack.com is publicly readable.
    parsed_pre = urlparse(url)
    if parsed_pre.netloc.lower() == "open.substack.com":
        url = urlunparse(parsed_pre._replace(netloc="substack.com"))

    parsed = urlparse(url)
    STRIP_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "source", "mc_cid", "mc_eid",
    }
    if parsed.netloc.lower().endswith("substack.com"):
        STRIP_PARAMS = {*STRIP_PARAMS, "r", "publication_id", "post_id", "isFreemail"}
    qs = {k: v for k, v in parse_qs(parsed.query, keep_blank_values=True).items() if k not in STRIP_PARAMS}
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((
        parsed.scheme.lower(),
        parsed.netloc.lower(),
        path,
        parsed.params,
        urlencode(qs, doseq=True),
        "",
    ))


async def get_or_create_source(
    url: str,
    user_id: str = "default",
    pool: str = "user",
) -> str:
    """
    Return the source_id for (user_id, url). Inserts a new row if none exists.
    Idempotent — safe for the API's POST /sources to call on every request.
    """
    url = normalise_url(url)
    existing = await db_fetchrow(
        "SELECT id, status FROM source WHERE user_id = $user_id AND url = $url",
        {"user_id": user_id, "url": url},
    )
    if existing:
        logger.info(f"[ingest] reusing existing source {existing['id']} (status={existing['status']}) for url={url}")
        return str(existing["id"])

    source_id = str(uuid.uuid4())
    await db_execute(
        """
        INSERT INTO source (id, title, url, pool, user_id, status)
        VALUES ($id::uuid, $title, $url, $pool, $user_id, 'queued')
        """,
        {
            "id": source_id,
            "title": "Processing...",
            "url": url,
            "pool": pool,
            "user_id": user_id,
        },
    )
    logger.info(f"Created source record: {source_id}")
    return source_id


async def ingest_urls(
    urls: list[str],
    user_id: str = "default",
    pool: str = "user",
) -> list[str]:
    """Ingest multiple URLs serially. (Concurrent ingest is fine in the worker via the queue.)"""
    source_ids = []
    for url in urls:
        try:
            sid = await ingest_url(url, user_id=user_id, pool=pool)
            source_ids.append(sid)
        except Exception as e:
            logger.error(f"Failed to ingest {url}: {e}")
    return source_ids
