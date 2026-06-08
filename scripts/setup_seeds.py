"""
scripts/setup_seeds.py
One-time seed setup script. Run this locally after adding new seed URLs.

What it does for each seed URL:
  1. Ingests the URL as a shared seed source (user_id='seed')
  2. Generates one pre-baked episode in the configured format
  3. Marks both source and episode as is_seed=true
  4. Registers the URL in the seed_url table

Usage:
    python3 -m scripts.setup_seeds [--url <url>]   # single seed
    python3 -m scripts.setup_seeds                 # all seeds
"""

import asyncio
import sys
import uuid
from loguru import logger

from core.db.connection import db_execute, db_fetchrow, db_query
from core.ingest import get_or_create_source, process_source
from core.seeds import SEED_ENTRIES, find_seed


SEED_USER_ID = "seed"


async def setup_seed(url: str) -> None:
    entry = find_seed(url)
    if not entry:
        logger.error(f"[setup_seeds] {url!r} is not in SEED_ENTRIES — add it to core/seeds.py first")
        return

    logger.info(f"[setup_seeds] processing seed: {url}")

    # ── 1. Create / reuse source row under seed user ──────────────────────────
    source_id = await get_or_create_source(url=url, user_id=SEED_USER_ID)
    logger.info(f"[setup_seeds] source_id={source_id}")

    source_row = await db_fetchrow(
        "SELECT status, is_seed FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    status = source_row["status"] if source_row else "queued"

    if status not in ("ready",):
        logger.info(f"[setup_seeds] running ingest pipeline for source_id={source_id} (status={status})")
        await process_source(source_id=source_id, is_final_attempt=True)
    else:
        logger.info(f"[setup_seeds] source already ingested (status=ready), skipping ingest")

    # Mark source as seed
    await db_execute(
        "UPDATE source SET is_seed = true WHERE id = $id::uuid",
        {"id": source_id},
    )

    # ── 2. Check if seed episode already exists ───────────────────────────────
    existing_episode = await db_fetchrow(
        """
        SELECT id, status FROM episode
        WHERE user_id = $user_id
          AND source_ids @> ARRAY[$source_id::uuid]
          AND is_seed = true
          AND status = 'ready'
        LIMIT 1
        """,
        {"user_id": SEED_USER_ID, "source_id": source_id},
    )

    if existing_episode:
        episode_id = str(existing_episode["id"])
        logger.info(f"[setup_seeds] seed episode already exists: {episode_id}")
    else:
        # ── 3. Generate episode ───────────────────────────────────────────────
        logger.info(f"[setup_seeds] generating seed episode for format={entry.format_name}")
        from worker.handlers.generate_from_source import _run_standalone
        from worker.handlers.generate_episode import handle_generate_episode

        # _run_standalone creates a queued episode row + enqueues a generate_episode job.
        # We run both inline here so we don't need the worker running.
        await _run_standalone(
            user_id=SEED_USER_ID,
            source_id=source_id,
            show_name=entry.format_name,
            speaker=None,
            length_minutes=None,
            angle_override=None,
        )

        # Find the queued episode _run_standalone just created
        ep_row = await db_fetchrow(
            """
            SELECT id FROM episode
            WHERE user_id = $user_id
              AND source_ids @> ARRAY[$source_id::uuid]
              AND status = 'queued'
            ORDER BY created_at DESC LIMIT 1
            """,
            {"user_id": SEED_USER_ID, "source_id": source_id},
        )
        if not ep_row:
            logger.error(f"[setup_seeds] _run_standalone did not create an episode for source_id={source_id}")
            return

        episode_id = str(ep_row["id"])
        logger.info(f"[setup_seeds] episode row created: {episode_id}, running generation inline")

        # Drain the enqueued job and run episode generation inline
        await db_execute(
            "UPDATE jobs SET status = 'running' WHERE payload->>'episode_id' = $episode_id AND status = 'queued'",
            {"episode_id": episode_id},
        )
        await handle_generate_episode({"episode_id": episode_id, "user_id": SEED_USER_ID})

        # Mark as seed
        await db_execute(
            "UPDATE episode SET is_seed = true WHERE id = $id::uuid",
            {"id": episode_id},
        )

        # Confirm it completed
        ep_row = await db_fetchrow(
            "SELECT status, error FROM episode WHERE id = $id::uuid",
            {"id": episode_id},
        )
        if not ep_row or ep_row["status"] != "ready":
            logger.error(f"[setup_seeds] episode generation failed: {ep_row and ep_row['error']}")
            return
        logger.info(f"[setup_seeds] episode ready: {episode_id}")

    # ── 4. Register in seed_url table ─────────────────────────────────────────
    await db_execute(
        """
        INSERT INTO seed_url (url, source_id, episode_id)
        VALUES ($url, $source_id::uuid, $episode_id::uuid)
        ON CONFLICT (url) DO UPDATE
            SET source_id = EXCLUDED.source_id,
                episode_id = EXCLUDED.episode_id
        """,
        {"url": url, "source_id": source_id, "episode_id": episode_id},
    )
    logger.info(f"[setup_seeds] registered in seed_url: {url}")


async def main() -> None:
    from core.db.connection import get_pool
    await get_pool()  # warm up the connection pool

    urls = sys.argv[1:] or [e.url for e in SEED_ENTRIES]
    for url in urls:
        await setup_seed(url)

    logger.info("[setup_seeds] done")


if __name__ == "__main__":
    asyncio.run(main())
