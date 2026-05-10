"""
scripts/run_idea_generator.py
Manually trigger the show idea generator workflow.
Usage: python scripts/run_idea_generator.py [user_id]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from intelligence.idea_generator import run_idea_generator
from core.db.connection import db_query


async def main():
    user_id = sys.argv[1] if len(sys.argv) > 1 else "default"
    print(f"Running idea generator for user: {user_id}\n")

    result = await run_idea_generator(user_id)

    print(f"\n--- Ideas generated: {result['saved_count']} ---\n")

    # Print what was saved
    ideas = await db_query(
        "SELECT angle, idea_type, format, source_ids FROM show_idea WHERE user_id = $uid AND generated = false",
        {"uid": user_id}
    )
    for i, idea in enumerate(ideas or [], 1):
        print(f"{i}. [{idea['idea_type'].upper()}] [{idea.get('format', '?')}]")
        print(f"   Angle: {idea['angle']}")
        print(f"   Sources: {len(idea['source_ids'])} article(s)")
        print()


if __name__ == "__main__":
    asyncio.run(main())
