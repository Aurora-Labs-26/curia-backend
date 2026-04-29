"""
test_pipeline.py
End-to-end pipeline: selector → briefing → Haiku outline → Sonnet transcript → XTTS audio
Show: The Last Correspondence (Kenji, solo)
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import anthropic
from dotenv import load_dotenv
from pydub import AudioSegment

# Add curia root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from shows.profiles import SHOW_PROFILES
from shows.prompts import BRIEFING_PROMPTS, OUTLINE_PROMPTS, TRANSCRIPT_PROMPTS
from intelligence.selector import select_episode_sources

# --- Config ---
SHOW_NAME = "last_correspondence"
USER_ID = "default"
EDITORIAL_DIRECTION = "the gap between thinking and expression"
XTTS_PYTHON = "/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/bin/python"
OUTPUT_PATH = str(Path(__file__).parent / "test_episode.mp3")


def render_briefing(show_name: str, source_insights: str, host_memory: str = "") -> str:
    template = BRIEFING_PROMPTS[show_name]
    return template.format(
        editorial_direction=EDITORIAL_DIRECTION,
        source_insights=source_insights,
        host_memory=host_memory or "No prior episodes — this is the first session."
    )


def generate_outline(briefing: str, show_name: str, client: anthropic.Anthropic) -> dict:
    profile = SHOW_PROFILES[show_name]
    print(f"Generating outline via {profile.outline_llm}...")
    message = client.messages.create(
        model=profile.outline_llm,
        max_tokens=1024,
        system=OUTLINE_PROMPTS[show_name],
        messages=[{"role": "user", "content": briefing}]
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    outline = json.loads(raw)
    print(f"Outline: '{outline.get('title', 'untitled')}' — {len(outline.get('segments', []))} segments")
    return outline


def generate_transcript(briefing: str, outline: dict, show_name: str, client: anthropic.Anthropic) -> list[dict]:
    profile = SHOW_PROFILES[show_name]
    print(f"Generating transcript via {profile.transcript_llm}...")
    human_message = f"BRIEFING:\n{briefing}\n\nEPISODE OUTLINE:\n{json.dumps(outline, indent=2)}"
    message = client.messages.create(
        model=profile.transcript_llm,
        max_tokens=4096,
        system=TRANSCRIPT_PROMPTS[show_name],
        messages=[{"role": "user", "content": human_message}]
    )
    raw = message.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    transcript = json.loads(raw)
    print(f"Transcript: {len(transcript)} lines")
    for i, line in enumerate(transcript):
        print(f"  [{i+1}] {line['text'][:80]}...")
    return transcript


def synthesize_line(text: str, voice_id: str, output_path: str):
    script = f"""
import sys
sys.path.insert(0, '/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/lib/python3.11/site-packages')
import torch
_original_load = torch.load
def _patched_load(*args, **kwargs):
    kwargs.setdefault('weights_only', False)
    return _original_load(*args, **kwargs)
torch.load = _patched_load
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
tts.tts_to_file(
    text={json.dumps(text)},
    speaker_wav={json.dumps(voice_id)},
    language="en",
    file_path={json.dumps(output_path)}
)
"""
    result = subprocess.run([XTTS_PYTHON, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"XTTS failed:\n{result.stderr[-500:]}")


def synthesize_and_stitch(transcript: list[dict], show_name: str, output_path: str) -> str:
    profile = SHOW_PROFILES[show_name]
    voice_map = {s.name: s.voice_id for s in profile.speaker_config.speakers}

    print(f"\nSynthesizing {len(transcript)} lines with XTTS...")
    audio_clips = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, line in enumerate(transcript):
            speaker = line["speaker"]
            voice_id = voice_map.get(speaker)
            if not voice_id:
                raise ValueError(f"No voice_id for speaker '{speaker}'")
            clip_path = os.path.join(tmpdir, f"line_{i:04d}.wav")
            print(f"  [{i+1}/{len(transcript)}] {speaker}: {line['text'][:60]}...", end=" ", flush=True)
            synthesize_line(line["text"], voice_id, clip_path)
            audio_clips.append(AudioSegment.from_wav(clip_path))
            print("done")

        print("\nStitching audio...")
        combined = AudioSegment.empty()
        gap = AudioSegment.silent(duration=400)
        for clip in audio_clips:
            combined += clip + gap
        combined.export(output_path, format="mp3")
        print(f"Exported: {output_path}")

    return output_path


async def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    client = anthropic.Anthropic()
    profile = SHOW_PROFILES[SHOW_NAME]

    # Validate voice files
    voice_map = {s.name: s.voice_id for s in profile.speaker_config.speakers}
    for speaker, voice_id in voice_map.items():
        if not Path(voice_id).exists():
            print(f"ERROR: Voice file not found for {speaker}: {voice_id}")
            sys.exit(1)

    print(f"Show: {SHOW_NAME}")
    print(f"Editorial direction: {EDITORIAL_DIRECTION}\n")

    # Step 1: Select sources from archive
    print("Selecting sources from archive...")
    try:
        sources, source_insights = await select_episode_sources(
            user_id=USER_ID,
            editorial_direction=EDITORIAL_DIRECTION,
            n=12
        )
        if not sources:
            print("No sources found in archive — using fallback hardcoded insights")
            source_insights = """
Source 1 — Neuroscience of narrative:
Core argument: The brain processes narrative and raw data differently — narrative activates neural coupling between speaker and listener.
Sharpest fact: The brain rehearses narrative as if living it, unable to distinguish story from experience.
Open question: If understanding requires narrative shape, what happens to ideas that resist narrative?

Source 2 — Roman letter-writing and time:
Core argument: Roman letter-writers composed knowing words would arrive days later, forcing imagination of a future reader-self.
Sharpest fact: Cicero dictated almost everything — walking, thinking aloud — because speech preceded certainty for him.
Open question: We've lost the humility of delayed communication — does instant delivery make us think less carefully?

Source 3 — On ideas that clarify when spoken:
Core argument: Some ideas only become coherent when said aloud — speech forces commitment that writing allows you to defer.
Sharpest fact: Heinrich von Kleist described this in 1805: the gradual completion of thoughts while speaking.
Open question: Is the gap between thinking and expression a failure — or the actual site of the work?
"""
        else:
            print(f"Selected {len(sources)} sources from archive")
    except Exception as e:
        print(f"Selector error ({e}) — using fallback hardcoded insights")
        source_insights = """
Source 1 — Fallback content (DB not available):
Core argument: This is placeholder content used when the database is not connected.
Sharpest fact: The pipeline runs end to end even without live source data.
Open question: What happens when we connect real sources?
"""

    # Step 2: Render briefing
    briefing = render_briefing(SHOW_NAME, source_insights)

    # Step 3: Generate outline (Haiku)
    outline = generate_outline(briefing, SHOW_NAME, client)

    # Step 4: Generate transcript (Sonnet)
    transcript = generate_transcript(briefing, outline, SHOW_NAME, client)

    # Step 5: Synthesize + stitch
    output = synthesize_and_stitch(transcript, SHOW_NAME, OUTPUT_PATH)

    print(f"\nDone. Episode saved to: {output}")


if __name__ == "__main__":
    asyncio.run(main())
