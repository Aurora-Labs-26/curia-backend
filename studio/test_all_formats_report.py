"""
studio/test_all_formats_report.py
Runs all four formats (transcript only, no TTS) using the stored article/insights
from the China-India episode. Generates a markdown report with segments, word counts,
line counts, target words, and approximate input tokens per call.

Run from curia-v2 root:
    python3 studio/test_all_formats_report.py
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
from core.prompts.transcript import generate_transcript as _transcript_module
from core.prompts.transcript_two_host import (
    generate_host_a as _host_a_module,
    generate_host_b as _host_b_module,
    merge_dialogue as _merge_module,
)
from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
from studio.shows.profiles import SHOW_PROFILES

# ---------------------------------------------------------------------------
# Episode data — same article/angle used in prior test runs
# ---------------------------------------------------------------------------

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

ALL_FORMATS = ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]

# Approximate sizes of shared inputs in characters (÷4 = tokens)
BRIEFING_CHARS   = 3200   # ~800 tokens
OUTLINE_CHARS    = 1600   # ~400 tokens
SPEAKER_CHARS    = 400    # ~100 tokens
HOST_A_TX_CHARS  = 2400   # ~600 tokens (used by Host B and Merger)
HOST_B_TX_CHARS  = 1600   # ~400 tokens (used by Merger)


def chars_to_tokens(n: int) -> int:
    return round(n / 4)


def prompt_tokens(path: str) -> int:
    p = Path(path)
    return chars_to_tokens(len(p.read_text())) if p.exists() else 0


def parse_json(raw: str, label: str):
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip()
    s, e = raw.find("{"), raw.rfind("}")
    if label == "outline" and s != -1 and e != -1:
        return json.loads(raw[s:e+1])
    s, e = raw.find("["), raw.rfind("]")
    if s != -1 and e != -1:
        return json.loads(raw[s:e+1])
    return json.loads(raw)


def run_format(fmt: str) -> dict:
    sources = [{"id": SOURCE_ID, "title": TITLE, "url": None}]
    packet = build_briefing_packet(
        format_name=fmt,
        sources=sources,
        insights=INSIGHTS,
        editorial_direction=ANGLE,
    )
    briefing = briefing_packet_to_str(packet)
    target_words = packet["episode_constraints"]["target_words"]
    target_minutes = packet["episode_constraints"]["target_length_minutes"]

    # --- Outline ---
    t0 = time.time()
    with dspy.context(lm=resolve.llm("outline", show=fmt)):
        pred = _outline_module(briefing=briefing)
    outline_time = time.time() - t0
    outline = parse_json(pred.outline_json, "outline")
    segments = outline.get("segments", [])

    profile = SHOW_PROFILES.get(fmt)
    is_two_host = profile and len(profile.speaker_config.speakers) >= 2

    briefing_tagged  = f"<briefing>\n{briefing}\n</briefing>"
    outline_tagged   = f"<outline>\n{json.dumps(outline, indent=2)}\n</outline>"

    if not is_two_host:
        # ── Single host ──────────────────────────────────────────────────
        speaker = profile.speaker_config.speakers[0]
        speaker_def = (
            f"Name: {speaker.name}\n"
            f"Backstory: {speaker.backstory}\n"
            f"Speech patterns: {speaker.speech_patterns}"
        )
        t1 = time.time()
        with dspy.context(lm=resolve.llm("transcript", show=fmt)):
            pred_tx = _transcript_module(
                briefing=briefing_tagged,
                speaker=f"<speaker>\n{speaker_def}\n</speaker>",
                outline=outline_tagged,
            )
        tx_time = time.time() - t1
        transcript = parse_json(pred_tx.transcript_json, "transcript")

        # Token estimate
        sys_prompt_chars = len(Path("prompts/transcript.txt").read_text())
        input_tokens = chars_to_tokens(
            sys_prompt_chars + BRIEFING_CHARS + OUTLINE_CHARS + SPEAKER_CHARS
        )
        calls = [{
            "name": "transcript",
            "time": tx_time,
            "input_tokens": input_tokens,
        }]
        return {
            "format": fmt,
            "is_two_host": False,
            "outline": outline,
            "transcript": transcript,
            "target_words": target_words,
            "target_minutes": target_minutes,
            "outline_time": outline_time,
            "calls": calls,
        }

    else:
        # ── Two host ─────────────────────────────────────────────────────
        speaker_a = profile.speaker_config.speakers[0]
        speaker_b = profile.speaker_config.speakers[1]
        speaker_a_def = (
            f"Name: {speaker_a.name}\n"
            f"Backstory: {speaker_a.backstory}\n"
            f"Speech patterns: {speaker_a.speech_patterns}"
        )
        speaker_b_def = (
            f"Name: {speaker_b.name}\n"
            f"Backstory: {speaker_b.backstory}\n"
            f"Speech patterns: {speaker_b.speech_patterns}"
        )

        sys_a = len(Path("prompts/transcript_host_a.txt").read_text())
        sys_b = len(Path("prompts/transcript_host_b.txt").read_text())
        sys_m = len(Path("prompts/transcript_merge.txt").read_text())

        # Call 1: Host A
        t1 = time.time()
        with dspy.context(lm=resolve.llm("transcript", show=fmt)):
            pred_a = _host_a_module(
                briefing=briefing_tagged,
                speaker=f"<speaker>\n{speaker_a_def}\n</speaker>",
                outline=outline_tagged,
            )
        t1e = time.time() - t1
        host_a = parse_json(pred_a.host_a_json, "host_a")
        for line in host_a:
            line["speaker"] = speaker_a.name.lower()
        host_a_str = json.dumps(host_a, indent=2)

        # Call 2: Host B
        t2 = time.time()
        with dspy.context(lm=resolve.llm("transcript", show=fmt)):
            pred_b = _host_b_module(
                briefing=briefing_tagged,
                speaker=f"<speaker>\n{speaker_b_def}\n</speaker>",
                outline=outline_tagged,
                host_a_transcript=f"<host_a_transcript>\n{host_a_str}\n</host_a_transcript>",
            )
        t2e = time.time() - t2
        host_b = parse_json(pred_b.host_b_json, "host_b")
        for line in host_b:
            line["speaker"] = speaker_b.name.lower()
        host_b_str = json.dumps(host_b, indent=2)

        # Call 3: Merge
        t3 = time.time()
        with dspy.context(lm=resolve.llm("transcript", show=fmt)):
            pred_m = _merge_module(
                briefing=briefing_tagged,
                outline=outline_tagged,
                host_a_transcript=f"<host_a_transcript>\n{host_a_str}\n</host_a_transcript>",
                host_b_transcript=f"<host_b_transcript>\n{host_b_str}\n</host_b_transcript>",
                speaker_a_name=speaker_a.name.lower(),
                speaker_b_name=speaker_b.name.lower(),
            )
        t3e = time.time() - t3
        transcript = parse_json(pred_m.merged_json, "merger")
        name_a, name_b = speaker_a.name.lower(), speaker_b.name.lower()
        for line in transcript:
            raw = (line.get("speaker") or "").strip().lower()
            line["speaker"] = name_a if name_a in raw else (name_b if name_b in raw else name_a)

        # Actual host A/B transcript sizes for token calc
        actual_a_chars = len(host_a_str)
        actual_b_chars = len(host_b_str)

        calls = [
            {
                "name": f"host_a ({speaker_a.name})",
                "time": t1e,
                "input_tokens": chars_to_tokens(sys_a + BRIEFING_CHARS + OUTLINE_CHARS + SPEAKER_CHARS),
            },
            {
                "name": f"host_b ({speaker_b.name})",
                "time": t2e,
                "input_tokens": chars_to_tokens(sys_b + BRIEFING_CHARS + OUTLINE_CHARS + SPEAKER_CHARS + actual_a_chars),
            },
            {
                "name": "merger",
                "time": t3e,
                "input_tokens": chars_to_tokens(sys_m + BRIEFING_CHARS + OUTLINE_CHARS + actual_a_chars + actual_b_chars),
            },
        ]
        return {
            "format": fmt,
            "is_two_host": True,
            "speakers": [speaker_a.name, speaker_b.name],
            "outline": outline,
            "host_a": host_a,
            "host_b": host_b,
            "transcript": transcript,
            "target_words": target_words,
            "target_minutes": target_minutes,
            "outline_time": outline_time,
            "calls": calls,
        }


def word_count(lines: list) -> int:
    return sum(len(l.get("text", "").split()) for l in lines)


def build_report(results: list) -> str:
    lines = []
    lines.append("# Transcript Run Report")
    lines.append(f"\n**Article:** {TITLE}")
    lines.append(f"**Angle:** {ANGLE[:120]}...")
    lines.append("\n---\n")

    for r in results:
        fmt = r["format"]
        target_w = r["target_words"]
        target_m = r["target_minutes"]
        transcript = r["transcript"]
        actual_w = word_count(transcript)
        actual_lines = len(transcript)
        pct = round(actual_w / target_w * 100)
        flag = " ⚑" if pct < 90 or pct > 110 else ""
        host_label = f"two-host ({', '.join(r['speakers'])})" if r["is_two_host"] else "single-host"
        total_call_time = r["outline_time"] + sum(c["time"] for c in r["calls"])
        total_input_tokens = sum(c["input_tokens"] for c in r["calls"])

        lines.append(f"## {fmt.upper().replace('_', ' ')}")
        lines.append(f"**Format:** {fmt} | **Mode:** {host_label} | **Target:** {target_m} min / {target_w} words")
        lines.append(f"**Output:** {actual_w} words ({pct}%{flag}) | {actual_lines} lines | {total_call_time:.1f}s total")
        lines.append(f"**Input tokens (approx):** {total_input_tokens:,} across {len(r['calls'])} transcript call(s)")
        lines.append("")

        # Calls breakdown
        lines.append("### Calls")
        lines.append("| Call | Input Tokens | Time |")
        lines.append("|------|-------------|------|")
        for c in r["calls"]:
            lines.append(f"| {c['name']} | {c['input_tokens']:,} | {c['time']:.1f}s |")
        lines.append("")

        # Outline segments
        outline = r["outline"]
        lines.append(f"### Outline: *{outline.get('title', '')}*")
        lines.append(f"**Thread:** {outline.get('thread', '')}")
        lines.append("")
        lines.append("| # | Title | Purpose | Primitives |")
        lines.append("|---|-------|---------|------------|")
        for seg in outline.get("segments", []):
            prims = ", ".join(seg.get("primitives_used", []))[:80]
            lines.append(f"| {seg['segment']} | {seg.get('title','')} | {seg.get('purpose','')[:60]} | {prims} |")
        lines.append("")

        # Transcript
        lines.append("### Transcript")
        lines.append("")
        if r["is_two_host"]:
            lines.append(f"**Host A ({r['speakers'][0]}):** {word_count(r['host_a'])} words, {len(r['host_a'])} lines")
            lines.append(f"**Host B ({r['speakers'][1]}):** {word_count(r['host_b'])} words, {len(r['host_b'])} lines")
            lines.append(f"**Merged:** {actual_w} words, {actual_lines} lines")
            lines.append("")

        for line in transcript:
            speaker = line.get("speaker", "?").upper()
            text = line.get("text", "")
            lines.append(f"**[{speaker}]** {text}")
            lines.append("")

        lines.append("\n---\n")

    return "\n".join(lines)


def main():
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    total_start = time.time()
    results = []

    for fmt in ALL_FORMATS:
        print(f"\n{'='*60}")
        print(f"Running: {fmt}")
        try:
            r = run_format(fmt)
            results.append(r)
            actual_w = word_count(r["transcript"])
            pct = round(actual_w / r["target_words"] * 100)
            total_tokens = sum(c["input_tokens"] for c in r["calls"])
            print(f"  Done — {actual_w}/{r['target_words']} words ({pct}%), {len(r['transcript'])} lines, ~{total_tokens:,} input tokens")
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback; traceback.print_exc()

    report = build_report(results)
    out = Path(__file__).parent / "transcript_run_report.md"
    out.write_text(report)
    print(f"\n{'='*60}")
    print(f"Report saved: {out}")
    print(f"Total time: {time.time()-total_start:.1f}s")


if __name__ == "__main__":
    main()
