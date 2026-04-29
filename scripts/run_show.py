"""
scripts/run_show.py
Run a full standing show generation end to end.
Usage: python scripts/run_show.py [show_name] [editorial_direction]
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from studio.generator import generate_standing_show

async def main():
    show_name = sys.argv[1] if len(sys.argv) > 1 else "last_correspondence"
    editorial_direction = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""

    print(f"Show: {show_name}")
    print(f"Editorial direction: {editorial_direction or '(none)'}\n")

    result = await generate_standing_show(
        show_name=show_name,
        user_id="default",
        editorial_direction=editorial_direction
    )

    print(f"\n--- Episode complete ---")
    print(f"Title:      {result['title']}")
    print(f"Episode ID: {result['episode_id']}")
    print(f"Audio:      {result['audio_path']}")
    print(f"Sources:    {result['source_count']}")
    print(f"\nPlay: open {result['audio_path']}")

if __name__ == "__main__":
    asyncio.run(main())
