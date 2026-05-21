"""
scripts/test_remix.py
Simulate a remix request from the terminal — given a source URL or source_id,
trigger generate_from_source with custom format/speaker/length/angle overrides.

Usage:
  # Remix by source URL (ingests if not already present)
  python3 scripts/test_remix.py --url https://ft.com/...

  # Remix by existing source_id (skips ingestion)
  python3 scripts/test_remix.py --source-id <uuid>

  # With overrides
  python3 scripts/test_remix.py --url https://ft.com/... \\
      --format slow-burn --speaker arjun --length 10 \\
      --angle "Focus on the geopolitical angle"

  # List recent sources to find a source_id
  python3 scripts/test_remix.py --list

Run python3 scripts/monitor.py in a second terminal to watch the pipeline.
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
setup_logging("test_remix")

from loguru import logger
from core.db.connection import db_fetchrow, db_query
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
    row = await db_fetchrow("SELECT firebase_uid FROM users LIMIT 1", {})
    if not row:
        raise RuntimeError("No users found in DB.")
    return row["firebase_uid"]


async def list_sources(user_id: str):
    rows = await db_query(
        """
        SELECT id, url, title, status, created_at
        FROM source
        WHERE user_id = $user_id
        ORDER BY created_at DESC
        LIMIT 20
        """,
        {"user_id": user_id},
    )
    if not rows:
        print("No sources found.")
        return
    print(f"\n{'ID':<38}  {'STATUS':<12}  {'TITLE / URL'}")
    print("─" * 90)
    for r in rows:
        title = (r["title"] or r["url"] or "")[:50]
        print(f"{str(r['id']):<38}  {r['status']:<12}  {title}")
    print()


async def find_or_ingest(url: str, user_id: str) -> str:
    """Return source_id — reuse existing if URL already ingested."""
    from core.ingest import ingest_url
    source_id = await ingest_url(url, user_id=user_id, pool="user")
    logger.info(f"[test_remix] Source: {source_id}")

    # Wait for ready
    for _ in range(180):
        row = await db_fetchrow("SELECT status FROM source WHERE id = $id::uuid", {"id": str(source_id)})
        if not row:
            await asyncio.sleep(1)
            continue
        if row["status"] == "ready":
            logger.info("[test_remix] Source ready ✓")
            return str(source_id)
        if row["status"] == "failed":
            raise RuntimeError(f"Source ingestion failed")
        await asyncio.sleep(1)
    raise RuntimeError("Timed out waiting for source")


async def main():
    parser = argparse.ArgumentParser(description="Trigger a remix from the terminal.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--url",       default=None, help="URL to remix (ingests if needed)")
    group.add_argument("--source-id", default=None, help="Existing source UUID")
    group.add_argument("--list",      action="store_true", help="List recent sources and exit")
    parser.add_argument("--user",     default=None,  help="Firebase UID (default: first user in DB)")
    parser.add_argument("--format",   default=None,  help="Format: sharp-take, slow-burn, live-wire, open-verdict")
    parser.add_argument("--speaker",  default=None,  help="Speaker: kenji, arjun, emeka")
    parser.add_argument("--length",   type=int, default=None, help="Length in minutes")
    parser.add_argument("--angle",    default=None,  help="Editorial angle override")
    parser.add_argument("--standalone", action="store_true", default=True)
    parser.add_argument("--no-standalone", dest="standalone", action="store_false")
    args = parser.parse_args()

    user_id = args.user or await get_default_user()

    if args.list:
        await list_sources(user_id)
        return

    if not args.url and not args.source_id:
        parser.error("Provide --url or --source-id (or --list to see existing sources)")

    fmt = FORMAT_ALIASES.get(args.format, args.format) if args.format else None

    logger.info(f"[test_remix] ── Remix test ──")
    logger.info(f"[test_remix] User:       {user_id}")
    logger.info(f"[test_remix] Standalone: {args.standalone}")
    if fmt:          logger.info(f"[test_remix] Format:     {fmt}")
    if args.speaker: logger.info(f"[test_remix] Speaker:    {args.speaker}")
    if args.length:  logger.info(f"[test_remix] Length:     {args.length}m")
    if args.angle:   logger.info(f"[test_remix] Angle:      {args.angle}")

    if args.url:
        logger.info(f"[test_remix] URL: {args.url}")
        source_id = await find_or_ingest(args.url, user_id)
    else:
        source_id = args.source_id
        row = await db_fetchrow("SELECT title, status FROM source WHERE id = $id::uuid", {"id": source_id})
        if not row:
            logger.error(f"[test_remix] Source {source_id} not found")
            sys.exit(1)
        logger.info(f"[test_remix] Source: {source_id}  status={row['status']}  title={row['title']}")
        if row["status"] != "ready":
            logger.warning(f"[test_remix] Source is not ready (status={row['status']}) — proceeding anyway")

    job_id = await enqueue(
        type="generate_from_source",
        payload={
            "user_id":        user_id,
            "source_id":      source_id,
            "standalone":     args.standalone,
            "show_name":      fmt,
            "speaker":        args.speaker,
            "length_minutes": args.length,
            "angle_override": args.angle,
        },
        user_id=user_id,
    )
    logger.info(f"[test_remix] Job enqueued: {job_id}")
    logger.info(f"[test_remix] ── Done. Watch progress with: python3 scripts/monitor.py ──")


if __name__ == "__main__":
    asyncio.run(main())
