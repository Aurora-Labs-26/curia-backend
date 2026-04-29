"""
core/ingest.py
Source ingestion pipeline — scrape → store → embed → transform.
Replaces open-notebook's source_graph with a direct Claude + content-core implementation.
"""

import asyncio
import os
import uuid
from typing import Optional

import anthropic
from content_core import extract_content
from dotenv import load_dotenv
from loguru import logger

from .db.connection import db_create, db_query, db_update

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# Transformations to run on every source at ingest time
# Tier 1 — always extract (every source has these)
# Tier 2 — extract if present, return null if not
TRANSFORMATIONS = {

    # TIER 1

    "key_insights": """You are extracting material for a single-host audio podcast.

From the article below, extract the 3 to 4 most important insights, ideas, or claims. These could be surprising, counterintuitive, or simply the sharpest things the piece says.

Requirements:
- 3 to 4 insights, no more
- Each one specific and concrete — include actual numbers, names, claims, or mechanisms if present
- Plain numbered list, one insight per line
- No markdown, no headers, no bold, no hedging language
- Never refuse — if the piece is a personal essay or reflection, extract the central ideas it is built around""",

    # TIER 2

    "human_stakes": """You are extracting material for a single-host audio podcast.

From the article below, extract what is actually at stake for real people — the concrete human consequence of the idea being true or false.

Requirements:
- One to two plain sentences
- Name the actual people or group affected, not "society" or "everyone"
- State what specifically changes or is lost — not "this matters" but what happens
- Plain text only, no markdown, no headers, no bold
- If the piece is a personal essay or reflection with no real-world stakes, return the single word: null""",

    "core_tensions": """You are extracting material for a single-host audio podcast.

From the article below, extract the central tension or contradiction AND the most important question it leaves unresolved. These two things together create the structural turn and the closing of an episode.

Requirements:
- One tension: "[Force A] vs [Force B]" followed by one sentence explaining the conflict
- One unresolved question: a direct question the article raises but does not answer
- Plain text only, no markdown, no headers, no bold
- If neither a real tension nor an unresolved question exists, return the single word: null""",

    "counterpoints": """You are extracting material for a single-host audio podcast.

From the article below, extract the strongest counterpoint to the article's main claim — the best argument against what the article is saying, whether the article raises it or not.

Requirements:
- One counterpoint only, steelmanned as strongly as possible
- One to two plain sentences
- No markdown, no headers, no bold
- If no meaningful counterpoint can be honestly constructed, return the single word: null""",

    "examples": """You are extracting material for a single-host audio podcast.

From the article below, extract either the single most concrete specific example OR the most useful mental model or framework the piece introduces — whichever is more present and more useful for a listener.

Requirements:
- One item only — example or mental model, whichever is stronger
- If an example: a named person, place, number, event, or mechanism in one speakable sentence
- If a mental model: name it in a short phrase, then one sentence explaining how it works
- Plain text only, no markdown, no headers, no bold
- If neither exists in the article, return the single word: null""",

}


async def scrape_url(url: str) -> tuple[str, str]:
    """Extract full text and title from a URL using content-core."""
    logger.info(f"Scraping: {url}")
    result = await extract_content(url=url)
    if not result.content or not result.content.strip():
        raise ValueError(f"Could not extract content from {url}")
    title = result.title or url
    logger.info(f"Scraped: {title} ({len(result.content)} chars)")
    return result.content, title


def run_transformation(full_text: str, prompt: str) -> str:
    """Run a single LLM transformation on full_text. Returns insight string."""
    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=prompt,
        messages=[{"role": "user", "content": full_text[:50000]}]  # cap at 50k chars
    )
    return message.content[0].text.strip()


async def embed_chunks(source_id: str, full_text: str):
    """Split text into chunks and store embeddings via Ollama nomic-embed-text."""
    from .embeddings import get_embedding

    words = full_text.split()
    chunk_size = 200
    overlap = 20
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        i += chunk_size - overlap

    logger.info(f"Embedding {len(chunks)} chunks for source {source_id}")

    for idx, chunk in enumerate(chunks):
        vector = await get_embedding(chunk)
        if vector:
            await db_query(
                "CREATE source_embedding SET source_id = $sid, chunk_text = $chunk, embedding = $vec, chunk_index = $idx",
                {"sid": source_id, "chunk": chunk, "vec": vector, "idx": idx}
            )
        else:
            logger.warning(f"Skipping chunk {idx} — embedding failed")

    logger.info(f"Embedding complete for source {source_id}")


async def ingest_url(
    url: str,
    user_id: str = "default",
    pool: str = "user",
    run_embed: bool = True
) -> str:
    """
    Full ingestion pipeline for a single URL.
    Returns source_id.
    """
    source_id = str(uuid.uuid4()).replace("-", "")

    # 1. Create placeholder record using raw SurrealQL so time::now() is evaluated server-side
    await db_query(
        f"CREATE source:{source_id} SET title = $title, url = $url, full_text = NONE, pool = '{pool}', user_id = '{user_id}', created_at = time::now()",
        {"title": "Processing...", "url": url}
    )
    logger.info(f"Created source record: {source_id}")

    # 2. Scrape content
    try:
        full_text, title = await scrape_url(url)
    except Exception as e:
        logger.error(f"Scraping failed for {url}: {e}")
        await db_update(f"source:{source_id}", {"title": f"Failed: {str(e)[:100]}"})
        raise

    # 3. Update source with full text
    await db_update(f"source:{source_id}", {
        "title": title,
        "full_text": full_text,
    })
    logger.info(f"Saved full text for: {title}")

    # 4. Run transformations (parallel)
    logger.info("Running transformations...")
    loop = asyncio.get_event_loop()
    tasks = []
    for insight_type, prompt in TRANSFORMATIONS.items():
        tasks.append((insight_type, loop.run_in_executor(
            None, run_transformation, full_text, prompt
        )))

    for insight_type, task in tasks:
        try:
            insight_content = await task
            await db_query(
                "CREATE source_insight SET source_id = $sid, insight_type = $itype, content = $content, created_at = time::now()",
                {"sid": source_id, "itype": insight_type, "content": insight_content}
            )
            logger.info(f"  ✓ {insight_type}")
        except Exception as e:
            logger.warning(f"  ✗ {insight_type}: {e}")

    # 5. Embed chunks (async, non-blocking)
    if run_embed:
        asyncio.create_task(embed_chunks(source_id, full_text))
        logger.info("Embedding started in background")

    logger.info(f"Ingestion complete: {source_id}")
    return source_id


async def ingest_urls(
    urls: list[str],
    user_id: str = "default",
    pool: str = "user",
) -> list[str]:
    """Ingest multiple URLs concurrently."""
    tasks = [ingest_url(url, user_id=user_id, pool=pool) for url in urls]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    source_ids = []
    for url, result in zip(urls, results):
        if isinstance(result, Exception):
            logger.error(f"Failed to ingest {url}: {result}")
        else:
            source_ids.append(result)
    return source_ids
