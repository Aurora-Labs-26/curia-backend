"""
scripts/embed_primitives.py
Backfill primitive embeddings for all sources.
Embeds core_tensions + counterpoints per source into source_primitive_embedding table.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from core.db.connection import db_query
from core.embeddings import get_embedding


async def embed_primitives():
    sources = await db_query("SELECT id, title FROM source", {})
    if not sources:
        print("No sources found.")
        return

    print(f"Embedding primitives for {len(sources)} sources...\n")

    for s in sources:
        sid = str(s["id"])
        bare_sid = sid.replace("source:", "")
        title = s.get("title", "Untitled")

        # Fetch core_tensions and counterpoints
        insights = await db_query(
            "SELECT insight_type, content FROM source_insight WHERE source_id = $sid AND insight_type IN ['core_tensions', 'counterpoints']",
            {"sid": bare_sid}
        )

        if not insights:
            print(f"  SKIP {title[:50]} — no insights found")
            continue

        parts = []
        for row in insights:
            content = row.get("content", "").strip()
            if content and content.lower() != "null":
                parts.append(content)

        if not parts:
            print(f"  SKIP {title[:50]} — core_tensions and counterpoints are null")
            continue

        combined = "\n".join(parts)

        # Check if already embedded
        existing = await db_query(
            "SELECT id FROM source_primitive_embedding WHERE source_id = $sid",
            {"sid": bare_sid}
        )
        if existing:
            print(f"  SKIP {title[:50]} — already embedded")
            continue

        vector = await get_embedding(combined)
        if not vector:
            print(f"  FAIL {title[:50]} — embedding returned None")
            continue

        await db_query(
            "CREATE source_primitive_embedding SET source_id = $sid, text = $text, embedding = $vec",
            {"sid": bare_sid, "text": combined, "vec": vector}
        )
        print(f"  OK   {title[:50]}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(embed_primitives())
