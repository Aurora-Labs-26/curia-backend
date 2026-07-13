"""
studio/run_episode_gen.py
Full clarity_engine episode generation — both Hume and Smallest.ai.

Runs the complete pipeline:
  1. Build a minimal briefing from a hardcoded source
  2. Generate outline (LLM)
  3. Generate transcript (two-host LLM pipeline: host_a → host_b → merge)
  4. Synthesize + stitch with Hume (default bindings)
  5. Re-synthesize + stitch the same transcript with Smallest.ai (patched bindings)

Outputs two MP3s into data/audio/. Prints timing tables for both.

Usage:
  cd /Users/bhabanimohapatra/Documents/Projects/curia-v2
  python3 -m studio.run_episode_gen
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(dotenv_path=ROOT / ".env")

# ---------------------------------------------------------------------------
# Minimal source for the briefing — no DB needed
# ---------------------------------------------------------------------------

SOURCE_TEXT = """
Title: The Unreasonable Effectiveness of Scaffolding

Modern AI systems are routinely described as reasoning, but they struggle to
recover from early errors in multi-step problems. Researchers have found that
wrapping the same model in explicit scaffolding — structured prompts, intermediate
checkpoints, and error-correction loops — improves performance on hard reasoning
benchmarks by 30-60%, often without any model changes.

This raises a structural question: if the model's raw capability is fixed, but
scaffolding multiplies what it can accomplish, where does the leverage actually
come from? Three competing theories:

1. The error surface theory: Scaffolding shrinks the number of steps where a
   wrong answer can compound. The model isn't better; it just has fewer chances
   to go wrong.

2. The context compression theory: Scaffolding forces the problem into chunks
   that fit within the model's effective attention window. Large problems become
   sequences of small ones.

3. The legibility theory: Structured prompts make the implicit reasoning explicit.
   The model isn't doing more reasoning — the scaffold is doing part of the
   reasoning and handing it off.

These theories have different implications. If theory 1 is right, the right
strategy is more checkpoints. If theory 2, better context management. If theory 3,
scaffolding itself is a form of intelligence — and attributing performance gains
to the model is an error of accounting.
"""

SHOW_NAME = "clarity_engine"
EDITORIAL_DIRECTION = "Why scaffolding might matter more than the model itself"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _print_header(title: str) -> None:
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)


def _print_timing_table(label: str, tts_timings: list[dict]) -> None:
    print(f"\n  [{label}] Timing table ({len(tts_timings)} entries):")
    for t in tts_timings:
        print(
            f"    line {t['line_index']:2d} | {t['speaker']:8s} | "
            f"{t['start_ms']:6d}ms → {t['end_ms']:6d}ms | "
            f"{t['text'][:55]!r}"
        )


def _validate(label: str, output_path: str, tts_timings: list[dict], transcript: list[dict]) -> None:
    p = Path(output_path)
    assert p.exists(), f"[{label}] Output file not found"
    size_kb = p.stat().st_size / 1024
    assert size_kb > 10, f"[{label}] File suspiciously small: {size_kb:.1f} KB"
    assert tts_timings, f"[{label}] tts_timings is empty"
    assert len(tts_timings) == len(transcript), (
        f"[{label}] Expected {len(transcript)} timing entries, got {len(tts_timings)}"
    )
    indices = [t["line_index"] for t in tts_timings]
    assert indices == sorted(indices), f"[{label}] Timings not sorted"
    for e in tts_timings:
        assert e["end_ms"] >= e["start_ms"], f"[{label}] end < start: {e}"
    print(f"  [{label}] ✓  {size_kb:.0f} KB  |  {len(tts_timings)} lines  |  {p.name}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    from studio.generator import (
        generate_outline,
        generate_transcript_two_host,
        synthesize_and_stitch_v2,
    )
    from briefing_builder import build_briefing_packet, briefing_packet_to_str
    from core.llm_config.schema import BindingValue
    from core.llm_config import resolver as _resolver

    output_dir = ROOT / "data" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Briefing ──────────────────────────────────────────────────────────
    _print_header("Step 1: Building briefing")
    sources = [{"id": "src-001", "title": "The Unreasonable Effectiveness of Scaffolding"}]
    # insights: dict[source_id, dict_of_primitive_fields]
    insights = {
        "src-001": {
            "key_insights": SOURCE_TEXT.strip(),
            "human_stakes": "Whether AI gains come from model capability or system design changes what gets funded and built.",
            "core_tensions": "Model-centric vs. scaffold-centric explanations of AI performance gains.",
            "counterpoints": "Some gains may be scaffold-specific and not transfer to new problem types.",
            "examples": "30-60% benchmark improvements from scaffolding without any model changes.",
        }
    }
    packet = build_briefing_packet(
        format_name=SHOW_NAME,
        sources=sources,
        insights=insights,
        editorial_direction=EDITORIAL_DIRECTION,
    )
    briefing = briefing_packet_to_str(packet)
    print(f"  Briefing length: {len(briefing)} chars")

    # ── 2. Outline ───────────────────────────────────────────────────────────
    _print_header("Step 2: Generating outline")
    t0 = time.time()
    outline = generate_outline(briefing, SHOW_NAME)
    print(f"  Done in {time.time()-t0:.1f}s  |  title: {outline.get('title', '?')!r}")
    print(f"  Segments: {[s.get('title','?') for s in outline.get('segments', [])]}")

    # ── 3. Transcript (two-host) ─────────────────────────────────────────────
    _print_header("Step 3: Generating two-host transcript")
    t0 = time.time()
    transcript = generate_transcript_two_host(briefing, outline, SHOW_NAME, user_kb=None)
    elapsed = time.time() - t0
    print(f"  Done in {elapsed:.1f}s  |  {len(transcript)} lines")
    speakers = {}
    for line in transcript:
        sp = line.get("speaker", "?")
        speakers[sp] = speakers.get(sp, 0) + 1
    for sp, n in speakers.items():
        print(f"    {sp}: {n} lines")
    print(f"\n  First 3 lines:")
    for line in transcript[:3]:
        print(f"    [{line['speaker']}] {line['text'][:80]}")

    # Save transcript for reference
    transcript_path = output_dir / "clarity_engine_transcript.json"
    transcript_path.write_text(json.dumps(transcript, indent=2, ensure_ascii=False))
    print(f"\n  Transcript saved: {transcript_path}")

    # ── 4. Synthesize — Smallest.ai (default config) ─────────────────────────
    _print_header("Step 4: Synthesizing with Smallest.ai (kenji=william, arjun=zorin, emeka=julia)")
    smallest_path = str(output_dir / "clarity_engine_smallest.mp3")
    smallest_timings: list[dict] = []
    smallest_elapsed = 0.0
    try:
        t0 = time.time()
        _, smallest_timings, _ = synthesize_and_stitch_v2(
            transcript=transcript,
            show_name=SHOW_NAME,
            output_path=smallest_path,
        )
        smallest_elapsed = time.time() - t0
        print(f"  Done in {smallest_elapsed:.1f}s")
        _validate("Smallest", smallest_path, smallest_timings, transcript)
        _print_timing_table("Smallest", smallest_timings)
    except Exception as e:
        smallest_elapsed = time.time() - t0
        print(f"  SKIPPED — {e}")

    # ── 5. Synthesize — Hume (patch bindings) ────────────────────────────────
    _print_header("Step 5: Synthesizing with Hume (kenji=Ito, arjun=Arjun, emeka=Vince Douglas)")

    HUME_VOICES = {"kenji": "Ito", "arjun": "Arjun", "emeka": "Vince Douglas"}

    cfg = _resolver.get_config()
    orig = {}
    for sp, voice in HUME_VOICES.items():
        if sp in cfg.bindings.speaker:
            orig[sp] = cfg.bindings.speaker[sp]
        cfg.bindings.speaker[sp] = BindingValue(model="hume-octave", voice_id=voice)

    hume_timings: list[dict] = []
    hume_elapsed = 0.0
    try:
        hume_path = str(output_dir / "clarity_engine_hume.mp3")
        t0 = time.time()
        _, hume_timings, _ = synthesize_and_stitch_v2(
            transcript=transcript,
            show_name=SHOW_NAME,
            output_path=hume_path,
        )
        hume_elapsed = time.time() - t0
        print(f"  Done in {hume_elapsed:.1f}s")
        _validate("Hume", hume_path, hume_timings, transcript)
        _print_timing_table("Hume", hume_timings)
    except Exception as e:
        hume_elapsed = time.time() - t0
        print(f"  SKIPPED — {e}")
    finally:
        for sp, binding in orig.items():
            cfg.bindings.speaker[sp] = binding

    # ── Summary ──────────────────────────────────────────────────────────────
    _print_header("Summary")
    print(f"  Transcript:    {len(transcript)} lines")
    print(f"  Outline title: {outline.get('title', '?')!r}")
    print()
    print(f"  Smallest output: {smallest_path}")
    print(f"  Smallest timings:{len(smallest_timings)} entries  ({smallest_elapsed:.1f}s)")
    print()
    print(f"  Hume output:     {hume_path if hume_timings else '(skipped)'}")
    print(f"  Hume timings:    {len(hume_timings)} entries  ({hume_elapsed:.1f}s)")
    print()
    smallest_word_timed = sum(1 for t in smallest_timings if t["end_ms"] > t["start_ms"])
    print(f"  Smallest proportional lines: {smallest_word_timed}/{len(smallest_timings)}")
    hume_word_timed = sum(1 for t in hume_timings if t["end_ms"] > t["start_ms"])
    print(f"  Hume word-timed lines:       {hume_word_timed}/{len(hume_timings)}")
    print()
    print("  ALL DONE ✓")


if __name__ == "__main__":
    main()
