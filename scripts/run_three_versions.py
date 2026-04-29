"""
scripts/run_three_versions.py
Generate three versions of a script for the same show idea:
1. No editorial direction, no source insights
2. Source insights only, no editorial direction
3. Editorial direction + source insights
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.prompts import BRIEFING_PROMPTS
from studio.generator import generate_outline, generate_transcript
from intelligence.selector import get_source_insights, format_source_insights
from core.db.connection import db_query

SHOW_NAME = "last_correspondence"
IDEA_ID = "show_idea:13z2hmofkirj0i9sjb10"
EDITORIAL_DIRECTION = "Our choices are hijacked by how questions are worded, trust networks are broken by industrialized deception, and the next wave of innovation requires obsessive builders nobody can replicate."


def print_script(version: str, briefing: str):
    outline = generate_outline(briefing, SHOW_NAME)
    transcript = generate_transcript(briefing, outline, SHOW_NAME)
    print(f"\n{'='*60}")
    print(f"VERSION: {version}")
    print(f"Title: {outline.get('title', '?')}")
    print(f"Thread: {outline.get('thread', '')}")
    print(f"{'='*60}\n")
    for line in transcript:
        print(f"{line['text']}\n")


async def main():
    # Load idea + sources
    result = await db_query(f"SELECT * FROM {IDEA_ID}", {})
    idea = result[0]
    source_ids = idea["source_ids"]

    sources = []
    for sid in source_ids:
        r = await db_query(f"SELECT id, title FROM {sid}", {})
        if r:
            sources.append(r[0])

    bare_ids = [str(s["id"]).replace("source:", "") for s in sources]
    insights = await get_source_insights(bare_ids)
    source_insights_str = format_source_insights(sources, insights)
    empty_insights = "No source material provided."

    # Version 1: no editorial direction, no source insights
    briefing_1 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction="No specific direction — follow the most interesting thread in the material.",
        source_insights=empty_insights,
    )

    # Version 2: source insights only, no editorial direction
    briefing_2 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction="No specific direction — follow the most interesting thread in the material.",
        source_insights=source_insights_str,
    )

    # Version 3: editorial direction + source insights
    briefing_3 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction=EDITORIAL_DIRECTION,
        source_insights=source_insights_str,
    )

    print_script("1 — No editorial direction, no source insights", briefing_1)
    print_script("2 — Source insights only", briefing_2)
    print_script("3 — Editorial direction + source insights", briefing_3)


if __name__ == "__main__":
    asyncio.run(main())
