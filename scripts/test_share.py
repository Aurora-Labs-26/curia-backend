"""
scripts/test_share.py
Simulate the full share-sheet flow from the terminal.

1. Ingest a URL (scrape → transform → embed)
2. Trigger generate_from_source (standalone or cluster path)
3. Print the source_id and episode_id when done

Usage:
  python3 scripts/test_share.py <url>
  python3 scripts/test_share.py <url> --standalone
  python3 scripts/test_share.py <url> --format sharp-take --speaker kenji --length 5
  python3 scripts/test_share.py <url> --angle "Focus on the economic implications"
  python3 scripts/test_share.py <url> --user <firebase_uid>

Run python3 scripts/monitor.py in a second terminal to watch the pipeline in real time.
"""

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from core.logging import setup_logging
setup_logging("test_share")

from loguru import logger
from core.db.connection import db_fetchrow
from core.ingest import ingest_url
from core.queue import enqueue
from studio.formats import FORMATS

FORMAT_ALIASES = {
    "slow-burn":    "narrative_drift",
    "sharp-take":   "clarity_engine",
    "live-wire":    "momentum_loop",
    "open-verdict": "exploration_engine",
    **{k: k for k in FORMATS},
}


async def get_default_user() -> str:
    """Return the first user in the DB — useful for local dev with a single account."""
    from core.db.connection import db_fetchrow
    row = await db_fetchrow("SELECT firebase_uid FROM users LIMIT 1", {})
    if not row:
        raise RuntimeError("No users found in DB. Create a user first via the app or scripts/create_user.py.")
    return row["firebase_uid"]


async def wait_for_source_ready(source_id: str, timeout: int = 120) -> bool:
    """Poll until source status = ready (or failed), return True if ready."""
    import asyncio
    from core.db.connection import db_fetchrow
    for _ in range(timeout):
        row = await db_fetchrow("SELECT status FROM source WHERE id = $id::uuid", {"id": source_id})
        if not row:
            await asyncio.sleep(1)
            continue
        status = row["status"]
        if status == "ready":
            return True
        if status == "failed":
            logger.error(f"[test_share] Source {source_id} failed ingestion")
            return False
        await asyncio.sleep(1)
    logger.error(f"[test_share] Timed out waiting for source {source_id}")
    return False


async def main():
    parser = argparse.ArgumentParser(description="Simulate share-sheet flow from terminal.")
    parser.add_argument("url",                          help="URL to ingest and generate from")
    parser.add_argument("--user",      default=None,    help="Firebase UID (default: first user in DB)")
    parser.add_argument("--standalone", action="store_true", default=True,
                        help="Standalone path (default). Use --no-standalone for cluster path.")
    parser.add_argument("--no-standalone", dest="standalone", action="store_false")
    parser.add_argument("--format",    default=None,    help="Format: sharp-take, slow-burn, live-wire, open-verdict")
    parser.add_argument("--speaker",   default=None,    help="Speaker: kenji, arjun, emeka")
    parser.add_argument("--length",    type=int, default=None, help="Length in minutes (3-30)")
    parser.add_argument("--angle",     default=None,    help="Editorial angle / override")
    args = parser.parse_args()

    user_id = args.user or await get_default_user()
    fmt = FORMAT_ALIASES.get(args.format, args.format) if args.format else None

    logger.info(f"[test_share] ── Share flow test ──")
    logger.info(f"[test_share] URL:        {args.url}")
    logger.info(f"[test_share] User:       {user_id}")
    logger.info(f"[test_share] Standalone: {args.standalone}")
    if fmt:     logger.info(f"[test_share] Format:     {fmt}")
    if args.speaker: logger.info(f"[test_share] Speaker:    {args.speaker}")
    if args.length:  logger.info(f"[test_share] Length:     {args.length}m")
    if args.angle:   logger.info(f"[test_share] Angle:      {args.angle}")

    # Step 1 — ingest
    logger.info(f"[test_share] Step 1/2: Ingesting URL...")
    source_id = await ingest_url(args.url, user_id=user_id, pool="user")
    logger.info(f"[test_share] Source created: {source_id}")

    logger.info(f"[test_share] Waiting for ingestion to complete (scrape → transform → embed)...")
    ready = await wait_for_source_ready(str(source_id), timeout=180)
    if not ready:
        logger.error("[test_share] Ingestion did not complete — check worker logs")
        sys.exit(1)
    logger.info(f"[test_share] Source ready ✓")

    # Step 2 — enqueue generate_from_source
    logger.info(f"[test_share] Step 2/2: Enqueueing generate_from_source...")
    job_id = await enqueue(
        type="generate_from_source",
        payload={
            "user_id":        user_id,
            "source_id":      str(source_id),
            "standalone":     args.standalone,
            "show_name":      fmt,
            "speaker":        args.speaker,
            "length_minutes": args.length,
            "angle_override": args.angle,
        },
        user_id=user_id,
    )
    logger.info(f"[test_share] Job enqueued: {job_id}")
    logger.info(f"[test_share] ── Done. Watch progress with: python3 scripts/monitor.py ──")


if __name__ == "__main__":
    asyncio.run(main())
