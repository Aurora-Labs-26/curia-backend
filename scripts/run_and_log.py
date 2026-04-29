"""
scripts/run_and_log.py
Run the idea-to-script pipeline and log every stage to generation_run table.
Usage: python scripts/run_and_log.py <show_idea_id> <profile_name> [editorial_direction]

profile_name: narrative_drift | clarity_engine | momentum_loop | exploration_engine

Example:
  python scripts/run_and_log.py show_idea:13z2hmofkirj0i9sjb10 narrative_drift
  python scripts/run_and_log.py show_idea:13z2hmofkirj0i9sjb10 exploration_engine "Focus on framing"
"""

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.profiles import SHOW_PROFILES
from shows.prompts import OUTLINE_PROMPT, TRANSCRIPT_PROMPT
from briefing_builder import build_briefing_packet, briefing_packet_to_str
from studio.generator import generate_outline, generate_transcript
from intelligence.selector import get_source_insights
from core.db.connection import db_query


async def run_and_log(idea_id: str, profile_name: str, editorial_direction: str = ""):
    # 1. Load idea
    result = await db_query(f"SELECT * FROM {idea_id}", {})
    if not result:
        print(f"Idea not found: {idea_id}")
        sys.exit(1)
    idea = result[0]

    profile = SHOW_PROFILES[profile_name]
    print(f"Running: {idea.get('angle', '')[:80]} / {profile_name}")

    # 2. Resolve sources
    sources = []
    for sid in idea.get("source_ids", []):
        r = await db_query(f"SELECT id, title FROM {sid}", {})
        if r:
            sources.append(r[0])

    bare_ids = [str(s["id"]).replace("source:", "") for s in sources]
    insights = await get_source_insights(bare_ids)

    effective_direction = editorial_direction or idea.get("angle", "")

    # 3. Build briefing packet
    packet = build_briefing_packet(
        format_name=profile.format_name,
        sources=sources,
        insights=insights,
        editorial_direction=effective_direction,
    )
    briefing = briefing_packet_to_str(packet)

    # 4. Generate outline
    outline = generate_outline(briefing, profile_name)
    title = outline.get("title", effective_direction[:60])

    # 5. Generate transcript
    transcript = generate_transcript(briefing, outline, profile_name)

    # 6. Build human message for transcript (for logging)
    speaker = profile.speaker_config.speakers[0]
    transcript_human = f"SPEAKER:\nName: {speaker.name}\nBackstory: {speaker.backstory}\nSpeech patterns: {speaker.speech_patterns}\n\nBRIEFING:\n{briefing}\n\nEPISODE OUTLINE:\n{json.dumps(outline, indent=2)}"

    # 7. Save to generation_run
    await db_query(
        """CREATE generation_run SET
            show_name = $show_name,
            idea_id = $idea_id,
            idea_angle = $idea_angle,
            editorial_direction = $editorial_direction,
            source_titles = $source_titles,
            briefing = $briefing,
            outline_prompt = $outline_prompt,
            outline_human = $outline_human,
            outline = $outline,
            transcript_prompt = $transcript_prompt,
            transcript_human = $transcript_human,
            transcript = $transcript,
            episode_title = $episode_title,
            created_at = time::now()""",
        {
            "show_name": profile_name,
            "idea_id": str(idea.get("id", idea_id)),
            "idea_angle": idea.get("angle", ""),
            "editorial_direction": effective_direction,
            "source_titles": [s.get("title", "") for s in sources],
            "briefing": briefing,
            "outline_prompt": OUTLINE_PROMPT,
            "outline_human": briefing,
            "outline": json.dumps(outline),
            "transcript_prompt": TRANSCRIPT_PROMPT,
            "transcript_human": transcript_human,
            "transcript": json.dumps(transcript),
            "episode_title": title,
        }
    )

    print(f"Logged: '{title}' ({len(transcript)} lines)")

    # 8. Print script
    print(f"\n=== {title} ===")
    thread = outline.get("thread", "")
    if thread:
        print(f"Thread: {thread}\n")
    print("--- SCRIPT ---\n")
    for line in transcript:
        print(f"{line['text']}\n")
    print("--- END ---")


async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/run_and_log.py <show_idea_id> <profile_name> [editorial_direction]")
        print("Profiles: narrative_drift | clarity_engine | momentum_loop | exploration_engine")
        sys.exit(1)

    idea_id = sys.argv[1]
    profile_name = sys.argv[2]
    editorial_direction = " ".join(sys.argv[3:]) if len(sys.argv) > 3 else ""

    await run_and_log(idea_id, profile_name, editorial_direction)


if __name__ == "__main__":
    asyncio.run(main())
