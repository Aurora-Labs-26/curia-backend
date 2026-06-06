"""
studio/test_music_bed.py
Test the Option-B music bed (fade envelope) on a 3-minute episode.

No DB, no selector — hardcoded sources.
Runs: briefing → outline (Haiku) → transcript (Sonnet) → stitch + music.

Output: studio/test_music_bed.mp3

Run from curia-v2 root:
    python studio/test_music_bed.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import dspy
from pydub import AudioSegment

from core.llm_config import resolve
from core.prompts.outline import generate_outline as _outline_module
from core.prompts.transcript import generate_transcript as _transcript_module, TRANSCRIPT_EXAMPLES
from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
from studio.generator import (
    SHOW_PROFILES,
    SPEAKER_PROFILES,
    synthesize_line_by_speaker,
    _load_optional_segment,
    _overlay_music,
    INTRO_FULL_MS, INTRO_FADE_MS, INTRO_GAIN_DB,
    OUTRO_FADE_IN_MS, OUTRO_FULL_MS, OUTRO_FADE_OUT_MS, OUTRO_GAIN_DB,
)

SHOW_NAME    = "clarity_engine"
OUTPUT_PATH  = str(Path(__file__).parent / "test_music_bed.mp3")
CURIA_ROOT   = Path(__file__).parent.parent

SAMPLE_SOURCES = [
    {
        "id": "00000000-0000-0000-0000-000000000001",
        "title": "The Attention Economy and Its Discontents",
        "url": "https://example.com/attention-economy",
    },
]
SAMPLE_INSIGHTS = {
    "00000000-0000-0000-0000-000000000001": {
        "key_insights": (
            "1. Human attention is finite and increasingly commodified by platforms.\n"
            "2. The average person switches tasks every 47 seconds when at a computer."
        ),
        "core_tensions": (
            "Platforms are designed to capture attention — but the same attention "
            "is what enables deep thought."
        ),
        "counterpoints": (
            "Some argue distraction is not new — Romans complained about it too. "
            "The question is whether the scale has changed the nature."
        ),
        "human_stakes": (
            "A generation is growing up without sustained attention practice. "
            "What cognitive capacities are being lost?"
        ),
        "examples": (
            "TikTok's 15-second format vs. the 3-hour podcast — both successful, "
            "radically different attention contracts."
        ),
    },
}


def generate_outline(briefing: str) -> dict:
    print("Generating outline (Haiku)...")
    with dspy.context(lm=resolve.llm("outline", show=SHOW_NAME)):
        prediction = _outline_module(briefing=briefing)
    raw = prediction.outline_json.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start:end+1]
    outline = json.loads(raw)
    print(f"  Title: '{outline.get('title', 'untitled')}' — {len(outline.get('segments', []))} segments")
    return outline


def generate_transcript(briefing: str, outline: dict) -> list[dict]:
    print("Generating transcript (Sonnet)...")
    profile = SHOW_PROFILES[SHOW_NAME]
    speaker = profile.speaker_config.speakers[0]
    speaker_definition = (
        f"Speaker name: {speaker.name}\n"
        f"Backstory: {speaker.backstory}\n"
        f"Speech patterns: {speaker.speech_patterns}"
    )
    with dspy.context(lm=resolve.llm("transcript", show=SHOW_NAME)):
        prediction = _transcript_module(
            briefing=briefing,
            outline=json.dumps(outline, ensure_ascii=False),
            speaker_definition=speaker_definition,
            examples=TRANSCRIPT_EXAMPLES,
        )
    raw = prediction.transcript_json.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    start, end = raw.find("["), raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start:end+1]
    transcript = json.loads(raw)
    print(f"  {len(transcript)} lines")
    return transcript


def stitch_with_music(transcript: list[dict]) -> str:
    profile = SHOW_PROFILES[SHOW_NAME]
    allowed_speakers = {s.name.lower() for s in profile.speaker_config.speakers}

    bitrate = os.getenv("CURIA_AUDIO_BITRATE", "128k")
    gap_ms  = int(os.getenv("CURIA_STITCH_GAP_MS", "400"))

    print(f"Synthesizing {len(transcript)} lines...")
    clips = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, line in enumerate(transcript):
            raw_speaker = (line.get("speaker") or "").strip().lower()
            if raw_speaker not in allowed_speakers:
                raw_speaker = next(iter(allowed_speakers))
            clip_path = os.path.join(tmpdir, f"line_{i:04d}.wav")
            print(f"  [{i+1}/{len(transcript)}] {raw_speaker}: {line['text'][:60]}...")
            fmt = synthesize_line_by_speaker(line["text"], raw_speaker, clip_path)
            if fmt == "mp3":
                clips.append(AudioSegment.from_mp3(clip_path))
            else:
                clips.append(AudioSegment.from_wav(clip_path))

        print("Stitching speech...")
        gap  = AudioSegment.silent(duration=gap_ms)
        body = AudioSegment.empty()
        for clip in clips:
            body += clip + gap

        # Resolve audio asset paths
        intro_path = (CURIA_ROOT / "assets/audio/intro.mp3").resolve()
        outro_path = (CURIA_ROOT / "assets/audio/outro.mp3").resolve()
        # Use intro.mp3 looped as the music bed (only asset available)
        music_path = intro_path

        intro = _load_optional_segment(str(intro_path), "intro") if intro_path.exists() else None
        outro = _load_optional_segment(str(outro_path), "outro") if outro_path.exists() else None
        music = _load_optional_segment(str(music_path), "music") if music_path.exists() else None

        intro_offset_ms     = 0
        music_intro_end_ms  = 0
        music_outro_start_ms = None

        if intro is not None:
            print(f"  Crossfading intro ({len(intro)/1000:.1f}s)...")
            episode_dbfs = body.dBFS
            intro_gain = (episode_dbfs - intro.dBFS + INTRO_GAIN_DB) if intro.dBFS != float("-inf") else 0
            intro = intro.apply_gain(intro_gain)
            intro_full_clip = intro[:INTRO_FULL_MS]
            intro_fade_clip = intro[INTRO_FULL_MS: INTRO_FULL_MS + INTRO_FADE_MS].fade_out(INTRO_FADE_MS)
            body = intro_full_clip + body
            body = body.overlay(intro_fade_clip, position=INTRO_FULL_MS)
            intro_offset_ms     = INTRO_FULL_MS
            music_intro_end_ms  = INTRO_FULL_MS + INTRO_FADE_MS

        if outro is not None:
            print(f"  Crossfading outro ({len(outro)/1000:.1f}s)...")
            episode_dbfs = body.dBFS
            outro_gain = (episode_dbfs - outro.dBFS + OUTRO_GAIN_DB) if outro.dBFS != float("-inf") else 0
            outro = outro.apply_gain(outro_gain)
            outro_fade_in   = outro[:OUTRO_FADE_IN_MS].fade_in(OUTRO_FADE_IN_MS)
            outro_full_clip = outro[OUTRO_FADE_IN_MS: OUTRO_FADE_IN_MS + OUTRO_FULL_MS]
            outro_fade_out  = outro[OUTRO_FADE_IN_MS + OUTRO_FULL_MS: OUTRO_FADE_IN_MS + OUTRO_FULL_MS + OUTRO_FADE_OUT_MS].fade_out(OUTRO_FADE_OUT_MS)
            outro_ready = outro_fade_in + outro_full_clip + outro_fade_out
            tts_end_pos     = len(body)
            outro_start_pos = tts_end_pos - OUTRO_FADE_IN_MS
            tail_ms         = OUTRO_FULL_MS + OUTRO_FADE_OUT_MS
            body = body + AudioSegment.silent(duration=tail_ms)
            body = body.overlay(outro_ready, position=max(0, outro_start_pos))
            music_outro_start_ms = max(0, outro_start_pos)

        if music is not None:
            print(
                f"  Overlaying music bed (Option B envelope: "
                f"intro_end={music_intro_end_ms/1000:.1f}s, "
                f"outro_start={music_outro_start_ms/1000:.1f}s if set)..."
            )
            body = _overlay_music(
                body, music, gain_db=-16.0,
                intro_end_ms=music_intro_end_ms,
                outro_start_ms=music_outro_start_ms,
            )
        else:
            print("  No music asset found — skipping music bed.")

        body.export(OUTPUT_PATH, format="mp3", bitrate=bitrate)
        print(f"\nDone. Episode: {OUTPUT_PATH} ({len(body)/1000:.1f}s)")

    return OUTPUT_PATH


def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    print(f"Show: {SHOW_NAME} | Length: 3 min | Music: Option B envelope\n")

    # 3-minute episode
    packet = build_briefing_packet(
        format_name="clarity_engine",
        sources=SAMPLE_SOURCES,
        insights=SAMPLE_INSIGHTS,
        editorial_direction="attention, distraction, and the 47-second task-switch",
        length_override=3,
    )
    briefing = briefing_packet_to_str(packet)

    outline    = generate_outline(briefing)
    transcript = generate_transcript(briefing, outline)

    # Save transcript for inspection
    transcript_out = Path(__file__).parent / "test_music_bed_transcript.json"
    with open(transcript_out, "w") as f:
        json.dump(transcript, f, indent=2, ensure_ascii=False)
    print(f"Transcript saved: {transcript_out}\n")

    stitch_with_music(transcript)


if __name__ == "__main__":
    main()
