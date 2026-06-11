"""
studio/test_two_host_formats.py
Runs clarity_engine and exploration_engine (two-host) with the same
article + angle from the four-format test. No TTS, no audio.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import dspy
from core.llm_config import resolve
from core.prompts.outline import generate_outline as _outline_module
from studio.generator import generate_transcript_two_host
from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str

ANGLE = (
    "India and China made the same economic choice in 1950 — to modernize their societies — "
    "but only China's stuck because it was willing to break families apart, suggesting that "
    "the real cost of development isn't choosing reform over tradition, but choosing "
    "enforcement over consent."
)

SOURCE_ID = "00000000-0000-0000-0000-000000000001"
TITLE = "Why China got rich and India didn't"

INSIGHTS = {
    SOURCE_ID: {
        "key_insights": (
            "1. China's rapid growth after 1978 was enabled by decades of brutal social "
            "transformation under Mao that destroyed traditional kinship structures.\n"
            "2. The New Marriage Law of 1950 banned arranged marriage and gave women "
            "property/divorce rights — and was enforced.\n"
            "3. India's Hindu Code Bill (same year, same ambition) failed in parliament "
            "and was never enforced at village level.\n"
            "4. Human capital (literacy, health, female labour participation) is the "
            "primary determinant of national economic success — not policy choices."
        ),
        "human_stakes": (
            "Hundreds of millions of Indians remain significantly poorer than Chinese "
            "citizens, earning roughly half the median income. Indian women specifically "
            "bore the cost — forced marriages, dowry abuse, exclusion from economic life "
            "persisted because the state chose not to enforce modernization."
        ),
        "core_tensions": (
            "Brutal authoritarian transformation vs. democratic stability and gradual reform. "
            "China's Communist government ruthlessly dismantled traditional society; India's "
            "democratic government accommodated it. The economic results 70 years later make "
            "the trade-off visible."
        ),
        "counterpoints": (
            "The article's framework may overstate human capital's explanatory power — "
            "China's specific post-1978 policy choices (SEZs, export manufacturing) also "
            "mattered. India's IT sector shows selective human capital investment can produce "
            "explosive growth without comprehensive social transformation."
        ),
        "examples": (
            "The New Marriage Law of 1950 in China banned arranged marriage, concubinage, "
            "and child betrothal, gave women property rights and right to divorce — enforced "
            "by state cadres going into villages. Female labour force participation: China 61%, "
            "India 27%. Median daily income 2022: China $13.36, India $5.54."
        ),
    }
}

SEP = "=" * 70


def run_outline(format_name: str, briefing: str) -> dict:
    print("  Generating outline (Haiku)...")
    with dspy.context(lm=resolve.llm("outline", show=format_name)):
        pred = _outline_module(briefing=briefing)
    raw = pred.outline_json.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    s, e = raw.find("{"), raw.rfind("}")
    outline = json.loads(raw[s:e+1])
    print(f"  Outline: '{outline.get('title')}' — {len(outline.get('segments', []))} segments")
    return outline


def run_two_host(format_name: str, briefing: str, outline: dict) -> list[dict]:
    print("  Generating two-host transcript (3x Sonnet)...")
    t = time.time()
    transcript = generate_transcript_two_host(
        briefing=briefing,
        outline=outline,
        show_name=format_name,
        user_kb=None,
    )
    elapsed = time.time() - t
    words = sum(len(line.get("text", "").split()) for line in transcript)
    speakers = sorted(set(line.get("speaker") for line in transcript))
    print(f"  Done in {elapsed:.1f}s — {len(transcript)} lines, ~{words} words")
    print(f"  Speakers: {speakers}")
    return transcript


def print_transcript(format_name: str, transcript: list[dict]):
    print(f"\n--- TRANSCRIPT: {format_name.upper()} ---")
    for line in transcript:
        print(f"  [{line['speaker']}] {line['text']}")


def main():
    for fmt in ["clarity_engine", "exploration_engine"]:
        print(f"\n{SEP}")
        print(f"FORMAT: {fmt.upper()}")

        sources = [{"id": SOURCE_ID, "title": TITLE, "url": None}]
        packet = build_briefing_packet(
            format_name=fmt,
            sources=sources,
            insights=INSIGHTS,
            editorial_direction=ANGLE,
        )
        briefing = briefing_packet_to_str(packet)

        outline = run_outline(fmt, briefing)
        transcript = run_two_host(fmt, briefing, outline)
        print_transcript(fmt, transcript)

        out = Path(__file__).parent / f"test_two_host_{fmt}.json"
        with open(out, "w") as f:
            json.dump(transcript, f, indent=2, ensure_ascii=False)
        print(f"\n  Saved: {out}")


if __name__ == "__main__":
    main()
