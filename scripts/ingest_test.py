"""
scripts/ingest_test.py
Test ingesting a few URLs and verify source + insights land in DB.
Usage: python scripts/ingest_test.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ingest import ingest_urls
from core.db.connection import db_query

TEST_URLS = [
    "https://paulgraham.com/words.html",
    "https://paulgraham.com/think.html",
    "https://paulgraham.com/writing44.html",
]

async def main():
    print(f"Ingesting {len(TEST_URLS)} URLs...\n")

    source_ids = await ingest_urls(TEST_URLS, user_id="default", pool="user")
    print(f"\nIngested {len(source_ids)} sources: {source_ids}")

    # Verify insights landed
    print("\nChecking source_insight records...")
    for sid in source_ids:
        result = await db_query(
            "SELECT insight_type, content FROM source_insight WHERE source_id = $sid",
            {"sid": sid}
        )
        print(f"\nSource {sid}:")
        for row in (result or []):
            print(f"  [{row['insight_type']}] {row['content'][:100]}...")

if __name__ == "__main__":
    asyncio.run(main())
