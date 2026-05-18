"""
core/ingest.py
Source ingestion pipeline — scrape → store → embed → transform.

Public functions:
    ingest_url(url, user_id)            CLI-friendly: creates row + processes it.
    process_source(source_id)           Worker-friendly: assumes row exists, processes it.
    embed_chunks(source_id, text)       Internal: chunked text embeddings.
    embed_primitive(source_id)          Internal: single primitive embedding for clustering.

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

from dotenv import load_dotenv
from loguru import logger

from .db.connection import db_execute, db_fetchrow, db_query
from .prompts.transformations import (
    TRANSFORMATION_NAMES,
    transformations as _transformations,
)

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

ARTICLE_CHAR_CAP = 50_000


# ---------------------------------------------------------------------------
# Atoms
# ---------------------------------------------------------------------------


async def scrape_url(url: str) -> tuple[str, str]:
    """Validate URL then scrape via cascade (trafilatura → firecrawl → fail)."""
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


async def embed_primitive(source_id: str) -> None:
    """
    Embed core_tensions || counterpoints as a single primitive vector.
    This is what idea_generator clusters on; doing it during ingest fixes the
    long-standing race where you had to run scripts/embed_primitives.py manually.
    """
    from .embeddings import get_embedding, get_embedding_column

    col = get_embedding_column()
    rows = await db_query(
        """
        SELECT insight_type, content
        FROM source_insight
        WHERE source_id = $sid::uuid
          AND insight_type IN ('core_tensions', 'counterpoints')
        """,
        {"sid": source_id},
    )
    parts = []
    for row in rows:
        c = (row.get("content") or "").strip()
        if c and c.lower() != "null":
            parts.append(c)
    if not parts:
        logger.info(f"  primitive_embed skipped (no core_tensions/counterpoints) for {source_id}")
        return
    combined = "\n".join(parts)
    vector = await get_embedding(combined)
    if not vector:
        logger.warning(f"  primitive_embed: embedding returned None for {source_id}")
        return
    await db_execute(
        f"""
        INSERT INTO source_primitive_embedding (source_id, {col})
        VALUES ($sid::uuid, $vec)
        ON CONFLICT (source_id) DO UPDATE SET {col} = EXCLUDED.{col}
        """,
        {"sid": source_id, "vec": vector},
    )
    logger.info(f"  primitive_embed: stored for {source_id} (column={col})")


# ---------------------------------------------------------------------------
# Main pipeline (worker entry point)
# ---------------------------------------------------------------------------


async def process_source(source_id: str) -> None:
    """
    Process a source row that already exists in the DB.
    Reads url + user_id from the row, runs scrape → transform → embed → primitive.
    Status moves through 'scraping' → 'transforming' → 'embedding' → 'ready'.
    On any exception: status='failed' + error column set, then re-raises.

    Used by the worker handler. Idempotent-ish (insights are appended; safe to re-run if
    insights table is cleared or duplicates are tolerated).
    """
    row = await db_fetchrow(
        "SELECT url, user_id, full_text FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    if not row:
        raise ValueError(f"source {source_id} not found")
    url = row["url"]

    try:
        # 1. Scrape (skip if full_text already populated — supports resumed runs)
        if not row.get("full_text"):
            await _set_status(source_id, "scraping")
            full_text, title = await scrape_url(url)
            await db_execute(
                """
                UPDATE source SET title = $title, full_text = $full_text, updated_at = now()
                WHERE id = $id::uuid
                """,
                {"id": source_id, "title": title, "full_text": full_text},
            )
        else:
            full_text = row["full_text"]

        # 2. Run transformations in parallel
        await _set_status(source_id, "transforming")
        loop = asyncio.get_event_loop()
        tasks = [
            (name, loop.run_in_executor(None, run_transformation, full_text, name))
            for name in TRANSFORMATION_NAMES
        ]
        for insight_type, task in tasks:
            try:
                content = await task
                if content and content.strip().lower() == "null":
                    content = None
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

        # 3. Embed chunks + primitive
        await _set_status(source_id, "embedding")
        await embed_chunks(source_id, full_text)
        await embed_primitive(source_id)

        # 4. Done
        await _set_status(source_id, "ready")
        logger.info(f"Ingestion complete: {source_id}")

    except Exception as e:
        await _set_status(source_id, "failed", error=str(e)[:1000])
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


async def get_or_create_source(
    url: str,
    user_id: str = "default",
    pool: str = "user",
) -> str:
    """
    Return the source_id for (user_id, url). Inserts a new row if none exists.
    Idempotent — safe for the API's POST /sources to call on every request.
    """
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
