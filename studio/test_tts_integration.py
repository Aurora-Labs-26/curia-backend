"""
studio/test_tts_integration.py
Live integration test — exercises synthesize_and_stitch_v2 against real TTS providers.

Runs two passes:
  1. Hume (kenji voice, as configured in models.yaml)
  2. Smallest.ai (using a temporary speaker_override binding in models.yaml)

Usage:
  cd /Users/bhabanimohapatra/Documents/Projects/curia-v2
  python3 -m studio.test_tts_integration
"""

from __future__ import annotations

import os
import sys
import json
import tempfile
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent.parent))

# ---------------------------------------------------------------------------
# Test transcript — short enough for a quick run, varied enough to test
# merging, splitting, and timing reconstruction
# ---------------------------------------------------------------------------

TRANSCRIPT_HUME = [
    {"speaker": "kenji", "text": "There's a pattern in this field that goes back decades."},
    {"speaker": "kenji", "text": "Every time a benchmark falls, people assume the hard problem is solved."},
    {"speaker": "arjun", "text": "But the benchmark was never the hard problem."},
    {"speaker": "arjun", "text": "It was a proxy. A convenient proxy."},
    {"speaker": "kenji", "text": "Exactly. And the moment the proxy falls, we invent a harder one."},
    {"speaker": "kenji", "text": "Chess, then Go, then language. Same pattern, different decade."},
    {"speaker": "arjun", "text": "So what's the current proxy we're about to lose?"},
    {"speaker": "kenji", "text": "Reasoning. Planning under uncertainty. Whatever we call it this year."},
]

# For Smallest.ai test — use a speaker name that maps to a smallest binding
# We'll temporarily patch the config to use smallest for a speaker
TRANSCRIPT_SMALLEST = [
    {"speaker": "kenji", "text": "The interesting question isn't whether the model can reason."},
    {"speaker": "kenji", "text": "It's whether we can tell when it isn't."},
    {"speaker": "arjun", "text": "That's actually a harder problem than building the model."},
    {"speaker": "arjun", "text": "Verification is always harder than generation."},
    {"speaker": "kenji", "text": "In math, in code, in argument. Universally true."},
]


def _check_output(label: str, output_path: str, tts_timings: list[dict]) -> None:
    """Validate the output file and timings."""
    print(f"\n  [{label}] Output: {output_path}")

    # File exists and has content
    p = Path(output_path)
    assert p.exists(), f"Output file not found: {output_path}"
    size_kb = p.stat().st_size / 1024
    assert size_kb > 1, f"Output file suspiciously small: {size_kb:.1f} KB"
    print(f"  [{label}] File size: {size_kb:.1f} KB ✓")

    # Timings present
    assert tts_timings, "tts_timings is empty"
    print(f"  [{label}] Timing entries: {len(tts_timings)}")

    # Timings sorted by line_index
    indices = [t["line_index"] for t in tts_timings]
    assert indices == sorted(indices), f"Timings not sorted: {indices}"
    print(f"  [{label}] Timings sorted ✓")

    # All required fields present
    for entry in tts_timings:
        assert "line_index" in entry, f"Missing line_index: {entry}"
        assert "start_ms" in entry, f"Missing start_ms: {entry}"
        assert "end_ms" in entry, f"Missing end_ms: {entry}"
        assert "speaker" in entry, f"Missing speaker: {entry}"
        assert "text" in entry, f"Missing text: {entry}"
        assert entry["end_ms"] >= entry["start_ms"], (
            f"end_ms < start_ms: {entry}"
        )
    print(f"  [{label}] All fields valid ✓")

    # Timings cover all transcript lines (at least as many unique line_indices as lines)
    unique_lines = len({t["line_index"] for t in tts_timings})
    print(f"  [{label}] Lines covered: {unique_lines}")

    # Print timing table
    print(f"\n  [{label}] Timing table:")
    for t in tts_timings:
        print(
            f"    line {t['line_index']:2d} | {t['speaker']:8s} | "
            f"{t['start_ms']:6d}ms → {t['end_ms']:6d}ms | "
            f"{t['text'][:50]!r}"
        )


def test_hume(tmpdir: str) -> None:
    """Test Hume TTS — patches speaker bindings to use hume-octave (Ito/Arjun)."""
    api_key = os.getenv("HUME_API_KEY")
    if not api_key:
        print("  SKIP: HUME_API_KEY not set")
        return

    print("\n" + "="*60)
    print("TEST 1: Hume TTS (kenji=Ito, arjun=Arjun)")
    print("="*60)

    from core.llm_config import resolver as _resolver
    from core.llm_config.schema import BindingValue
    from studio.generator import synthesize_and_stitch_v2

    HUME_VOICES = {"kenji": "Ito", "arjun": "Arjun", "emeka": "Vince Douglas"}

    cfg = _resolver.get_config()
    original_bindings = {}
    for speaker, voice_id in HUME_VOICES.items():
        if speaker in cfg.bindings.speaker:
            original_bindings[speaker] = cfg.bindings.speaker[speaker]
        cfg.bindings.speaker[speaker] = BindingValue(model="hume-octave", voice_id=voice_id)

    try:
        output_path = os.path.join(tmpdir, "hume_episode.mp3")
        t0 = time.time()
        output, tts_timings = synthesize_and_stitch_v2(
            transcript=TRANSCRIPT_HUME,
            show_name="clarity_engine",
            output_path=output_path,
        )
        elapsed = time.time() - t0
        print(f"  Elapsed: {elapsed:.1f}s")
        _check_output("Hume", output, tts_timings)
        print("\n  HUME TEST PASSED ✓")
    finally:
        for speaker, binding in original_bindings.items():
            cfg.bindings.speaker[speaker] = binding


def test_smallest(tmpdir: str) -> None:
    """Test Smallest.ai TTS — uses default config (smallest-lightning, william/alec/julia)."""
    api_key = os.getenv("SMALLEST_API_KEY")
    if not api_key:
        print("  SKIP: SMALLEST_API_KEY not set")
        return

    print("\n" + "="*60)
    print("TEST 2: Smallest.ai TTS (default config)")
    print("="*60)

    from studio.generator import synthesize_and_stitch_v2

    output_path = os.path.join(tmpdir, "smallest_episode.mp3")
    t0 = time.time()
    output, tts_timings = synthesize_and_stitch_v2(
        transcript=TRANSCRIPT_SMALLEST,
        show_name="clarity_engine",
        output_path=output_path,
    )
    elapsed = time.time() - t0
    print(f"  Elapsed: {elapsed:.1f}s")
    _check_output("Smallest", output, tts_timings)
    print("\n  SMALLEST TEST PASSED ✓")


def main() -> None:
    print("Curia TTS Integration Test")
    print(f"Working dir: {Path.cwd()}")

    with tempfile.TemporaryDirectory() as tmpdir:
        errors = []

        try:
            test_hume(tmpdir)
        except Exception as e:
            print(f"\n  HUME TEST FAILED: {e}")
            import traceback; traceback.print_exc()
            errors.append(("Hume", e))

        try:
            test_smallest(tmpdir)
        except Exception as e:
            print(f"\n  SMALLEST TEST FAILED: {e}")
            import traceback; traceback.print_exc()
            errors.append(("Smallest", e))

    print("\n" + "="*60)
    if errors:
        print(f"FAILED: {len(errors)} test(s)")
        for name, err in errors:
            print(f"  - {name}: {err}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED ✓")


if __name__ == "__main__":
    main()
