"""
scripts/rerun_transformations.py
Run the new 6 transformations on all existing sources and store results.
Skips any insight_type that already exists for a source.
Usage: python scripts/rerun_transformations.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from loguru import logger
from core.db.connection import db_execute, db_query
from core.ingest import run_transformation
from core.prompts.transformations import TRANSFORMATION_NAMES


async def main():
    # Fetch all sources with full text
    sources = await db_query("SELECT id, title, full_text FROM source", {})
    sources = [s for s in (sources or []) if s.get("full_text")]
    logger.info(f"Found {len(sources)} sources with full text")

    # Delete all existing insights and start fresh
    await db_execute("DELETE FROM source_insight", {})
    logger.info("Cleared all existing source_insight records")
    done = set()

    logger.info(f"Transformation types: {sorted(TRANSFORMATION_NAMES)}")

    for source in sources:
        sid = str(source["id"]).replace("source:", "")
        title = source.get("title", "")[:50]
        full_text = source.get("full_text", "")
        if not full_text:
            continue

        logger.info(f"Processing: {title}")

        for insight_type in TRANSFORMATION_NAMES:
            if (sid, insight_type) in done:
                logger.info(f"  skip {insight_type} (exists)")
                continue

            try:
                loop = asyncio.get_event_loop()
                content = await loop.run_in_executor(
                    None, run_transformation, full_text, insight_type
                )

                # Store null as None if model returned "null"
                if content and content.strip().lower() == "null":
                    content = None

                await db_execute(
                    """
                    INSERT INTO source_insight (source_id, insight_type, content)
                    VALUES ($sid::uuid, $itype, $content)
                    """,
                    {"sid": sid, "itype": insight_type, "content": content},
                )
                logger.info(f"  {insight_type}: {(content or 'null')[:80]}")
                done.add((sid, insight_type))

            except Exception as e:
                logger.warning(f"  failed {insight_type}: {e}")

    logger.info("Done.")


if __name__ == "__main__":
    asyncio.run(main())
