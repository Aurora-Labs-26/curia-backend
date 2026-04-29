"""
scripts/run_show_print.py
Generate outline + transcript, print it, then synthesize audio.
Usage: python scripts/run_show_print.py [show_name] [editorial_direction]
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.profiles import SHOW_PROFILES
from shows.prompts import BRIEFING_PROMPTS, OUTLINE_PROMPTS, TRANSCRIPT_PROMPTS
from intelligence.selector import select_episode_sources
from studio.generator import generate_outline, generate_transcript, synthesize_and_stitch, save_episode, log_covered_topics
from core.db.connection import db_query

EPISODES_DIR = Path(__file__).parent.parent / "studio" / "episodes"
EPISODES_DIR.mkdir(exist_ok=True)


async def main():
    show_name = sys.argv[1] if len(sys.argv) > 1 else "last_correspondence"
    editorial_direction = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else ""

    print(f"Show: {show_name}")
    print(f"Editorial direction: {editorial_direction or '(none)'}\n")

    # 1. Select sources
    sources, source_insights_str = await select_episode_sources(
        user_id="default",
        editorial_direction=editorial_direction,
        n=12
    )
    source_ids = [str(s.get("id", "")) for s in sources]

    if not source_insights_str.strip():
        source_insights_str = "No sources available. Generate a general episode based on the editorial direction."

    # 2. Render briefing
    briefing = BRIEFING_PROMPTS[show_name].format(
        editorial_direction=editorial_direction or "No specific direction — follow the most interesting thread in the material.",
        source_insights=source_insights_str,
    )

    # 3. Outline
    outline = generate_outline(briefing, show_name)
    title = outline.get("title", show_name)
    print(f"\n=== {title} ===")
    print(f"Thread: {outline.get('thread', outline.get('central_tension', ''))}\n")

    # 4. Transcript
    transcript = generate_transcript(briefing, outline, show_name)

    print("--- SCRIPT ---\n")
    for line in transcript:
        print(f"{line['speaker'].upper()}: {line['text']}\n")

    print("--- END SCRIPT ---\n")
    print("Synthesizing audio...")

    # 5. Synthesize
    episode_id = str(uuid.uuid4()).replace("-", "")
    audio_path = str(EPISODES_DIR / f"{episode_id}.mp3")
    synthesize_and_stitch(transcript, show_name, audio_path)

    # 6. Save
    saved_id = await save_episode(
        user_id="default",
        show_name=show_name,
        title=title,
        transcript=transcript,
        audio_path=audio_path,
        source_ids=source_ids,
        editorial_direction=editorial_direction
    )
    await log_covered_topics("default", show_name, saved_id, outline, source_ids)

    print(f"\n--- Done ---")
    print(f"Title:      {title}")
    print(f"Episode ID: {saved_id}")
    print(f"Audio:      {audio_path}")
    print(f"\nPlay: open {audio_path}")


if __name__ == "__main__":
    asyncio.run(main())
