"""
scripts/backfill_three_versions.py
One-time script to log the three already-generated versions into generation_run.
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.prompts import BRIEFING_PROMPTS, OUTLINE_PROMPTS, TRANSCRIPT_PROMPTS
from studio.generator import generate_outline, generate_transcript
from intelligence.selector import get_source_insights, format_source_insights
from core.db.connection import db_query

SHOW_NAME = "last_correspondence"
IDEA_ID = "show_idea:13z2hmofkirj0i9sjb10"
EDITORIAL_DIRECTION = "Our choices are hijacked by how questions are worded, trust networks are broken by industrialized deception, and the next wave of innovation requires obsessive builders nobody can replicate."


async def log_run(label, briefing, outline, transcript, sources, editorial_direction, idea):
    title = outline.get("title", idea.get("angle", "")[:60])
    outline_prompt = OUTLINE_PROMPTS.get(SHOW_NAME, "")
    transcript_prompt = TRANSCRIPT_PROMPTS.get(SHOW_NAME, "")
    transcript_human = f"BRIEFING:\n{briefing}\n\nEPISODE OUTLINE:\n{json.dumps(outline, indent=2)}"

    await db_query(
        """CREATE generation_run SET
            show_name = $show_name,
            idea_id = $idea_id,
            idea_title = $idea_title,
            idea_angle = $idea_angle,
            editorial_direction = $editorial_direction,
            source_titles = $source_titles,
            source_insights = $source_insights,
            briefing = $briefing,
            outline_prompt = $outline_prompt,
            outline_human = $outline_human,
            outline = $outline,
            transcript_prompt = $transcript_prompt,
            transcript_human = $transcript_human,
            transcript = $transcript,
            episode_title = $episode_title,
            run_label = $run_label,
            created_at = time::now()""",
        {
            "show_name": SHOW_NAME,
            "idea_id": str(idea.get("id", IDEA_ID)),
            "idea_title": idea.get("angle", "")[:60],
            "idea_angle": idea.get("angle", ""),
            "editorial_direction": editorial_direction,
            "source_titles": [s.get("title", "") for s in sources],
            "source_insights": briefing,
            "briefing": briefing,
            "outline_prompt": outline_prompt,
            "outline_human": briefing,
            "outline": json.dumps(outline),
            "transcript_prompt": transcript_prompt,
            "transcript_human": transcript_human,
            "transcript": json.dumps(transcript),
            "episode_title": title,
            "run_label": label,
        }
    )
    print(f"Logged: {label} → '{title}'")


async def main():
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

    briefing_1 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction="No specific direction — follow the most interesting thread in the material.",
        source_insights=empty_insights,
    )
    briefing_2 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction="No specific direction — follow the most interesting thread in the material.",
        source_insights=source_insights_str,
    )
    briefing_3 = BRIEFING_PROMPTS[SHOW_NAME].format(
        editorial_direction=EDITORIAL_DIRECTION,
        source_insights=source_insights_str,
    )

    for label, briefing, ed in [
        ("v1 — no direction, no insights", briefing_1, ""),
        ("v2 — source insights only", briefing_2, ""),
        ("v3 — editorial + insights", briefing_3, EDITORIAL_DIRECTION),
    ]:
        print(f"Generating {label}...")
        outline = generate_outline(briefing, SHOW_NAME)
        transcript = generate_transcript(briefing, outline, SHOW_NAME)
        await log_run(label, briefing, outline, transcript, sources, ed, idea)

    print("\nDone. Open http://localhost:7700")


if __name__ == "__main__":
    asyncio.run(main())
