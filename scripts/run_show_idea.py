"""
scripts/run_show_idea.py
Generate a script (no TTS) from a show idea.
Usage: python scripts/run_show_idea.py <show_idea_id> <show_name> [editorial_direction]
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.profiles import SHOW_PROFILES
from shows.prompts import BRIEFING_PROMPTS, OUTLINE_PROMPTS, TRANSCRIPT_PROMPTS
from studio.generator import generate_outline, generate_transcript
from intelligence.selector import get_source_insights, format_source_insights
from core.db.connection import db_query


async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/run_show_idea.py <show_idea_id> <show_name> [editorial_direction]")
        sys.exit(1)

    idea_id = sys.argv[1]
    show_name = sys.argv[2]
    editorial_direction = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    # Load idea
    result = await db_query(f"SELECT * FROM {idea_id}", {})
    if not result:
        print(f"Idea not found: {idea_id}")
        sys.exit(1)
    idea = result[0]

    print(f"Idea: {idea['title']}")
    print(f"Angle: {idea['angle']}")
    print(f"Show: {show_name}")
    print(f"Editorial direction: {editorial_direction or '(none)'}\n")

    source_ids = idea["source_ids"]

    # Resolve sources
    sources = []
    for sid in source_ids:
        r = await db_query(f"SELECT id, title FROM {sid}", {})
        if r:
            sources.append(r[0])

    print(f"Sources ({len(sources)}):")
    for s in sources:
        print(f"  - {s['title']}")
    print()

    # Fetch insights
    bare_ids = [str(s["id"]).replace("source:", "") for s in sources]
    insights = await get_source_insights(bare_ids)
    source_insights_str = format_source_insights(sources, insights)

    if not source_insights_str.strip():
        source_insights_str = "No sources available. Generate based on the editorial direction."

    # Render briefing
    briefing = BRIEFING_PROMPTS[show_name].format(
        editorial_direction=editorial_direction or idea["angle"],
        source_insights=source_insights_str,
    )

    # Generate outline
    outline = generate_outline(briefing, show_name)
    title = outline.get("title", idea["title"])

    print(f"=== {title} ===")
    thread = outline.get("thread", outline.get("central_tension", ""))
    if thread:
        print(f"Thread: {thread}\n")

    # Generate transcript
    transcript = generate_transcript(briefing, outline, show_name)

    print("--- SCRIPT ---\n")
    for line in transcript:
        print(f"{line['speaker'].upper()}: {line['text']}\n")
    print("--- END SCRIPT ---")


if __name__ == "__main__":
    asyncio.run(main())
