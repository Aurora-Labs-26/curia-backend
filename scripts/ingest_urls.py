"""
scripts/ingest_urls.py
Ingest a list of URLs into the DB.
Usage: python scripts/ingest_urls.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.ingest import ingest_urls

URLS = [
    "https://freddiedeboer.substack.com/p/to-learn-to-live-in-a-mundane-universe",
    "https://miniphilosophy.substack.com/p/finding-sparks-of-joy",
    "https://miniphilosophy.substack.com/p/how-to-make-the-unseen-seen",
    "https://ayushithakkar.substack.com/p/how-to-get-into-philosophy-part-2",
    "https://litverse.substack.com/p/drugs-alcohol-and-existentialism",
    "https://litverse.substack.com/p/portrait-of-an-aggrieved-stem-major",
    "https://litverse.substack.com/p/the-economy-is-made-up",
    "https://rubysstudio.substack.com/p/on-the-5-best-substack-essays-of",
    "https://www.construction-physics.com/p/do-commodities-get-cheaper-over-time",
    "https://www.construction-physics.com/p/the-surprisingly-long-life-of-the",
    "https://www.noahpinion.blog/p/the-moderately-easy-problem-of-consciousness",
    "https://www.noahpinion.blog/p/why-shoplifting-is-bad",
    "https://www.henrikkarlsson.xyz/p/looking-for-alice",
]

async def main():
    print(f"Ingesting {len(URLS)} URLs...\n")
    source_ids = await ingest_urls(URLS, user_id="default", pool="user")
    print(f"\nDone. Ingested {len(source_ids)} sources:")
    for sid in source_ids:
        print(f"  {sid}")

if __name__ == "__main__":
    asyncio.run(main())
