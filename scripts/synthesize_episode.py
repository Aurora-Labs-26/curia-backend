"""
scripts/synthesize_episode.py
Synthesize a full episode from a generation_run record.
Prepends intro.wav, synthesizes each transcript line, stitches into final MP3.
Usage: python scripts/synthesize_episode.py <generation_run_id>
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.db.connection import db_query

XTTS_PYTHON = "/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/bin/python"
KENJI_WAV = "/Users/bhabanimohapatra/Documents/Projects/curia/studio/kenji_reference.wav"
INTRO_WAV = "/Users/bhabanimohapatra/Documents/Projects/curia/studio/intro.wav"
OUTPUT_DIR = "/Users/bhabanimohapatra/Documents/Projects/curia/data/episodes"


def synthesize_line(text: str, output_path: str):
    script = f"""
import sys
sys.path.insert(0, '/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/lib/python3.11/site-packages')
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
tts.tts_to_file(text={json.dumps(text)}, speaker_wav={json.dumps(KENJI_WAV)}, language="en", file_path={json.dumps(output_path)})
"""
    result = subprocess.run([XTTS_PYTHON, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"XTTS failed:\n{result.stderr[-500:]}")


def stitch_wavs(wav_paths: list[str], output_path: str):
    """Concatenate WAV files with a short silence gap between lines."""
    script = f"""
import sys
sys.path.insert(0, '/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/lib/python3.11/site-packages')
from pydub import AudioSegment

paths = {json.dumps(wav_paths)}
gap = AudioSegment.silent(duration=500)
combined = AudioSegment.empty()
for i, p in enumerate(paths):
    seg = AudioSegment.from_wav(p)
    combined += seg
    if i < len(paths) - 1:
        combined += gap

combined.export({json.dumps(output_path)}, format="mp3", bitrate="128k")
print("Stitched:", len(paths), "clips ->", {json.dumps(output_path)})
"""
    result = subprocess.run([XTTS_PYTHON, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Stitch failed:\n{result.stderr[-500:]}")
    print(result.stdout.strip())


async def synthesize_episode(run_id: str):
    result = await db_query(f"SELECT * FROM {run_id}", {})
    if not result:
        print(f"Generation run not found: {run_id}")
        sys.exit(1)

    run = result[0]
    title = run.get("episode_title", "episode")
    transcript = json.loads(run["transcript"])

    print(f"Episode: {title}")
    print(f"Lines: {len(transcript)}")

    await asyncio.to_thread(os.makedirs, OUTPUT_DIR, exist_ok=True)
    safe_title = title.replace(" ", "_").replace(":", "").replace("'", "")[:60]
    output_path = os.path.join(OUTPUT_DIR, f"{safe_title}.mp3")

    with tempfile.TemporaryDirectory() as tmpdir:
        wav_paths = []

        # Prepend intro
        if await asyncio.to_thread(os.path.exists, INTRO_WAV):
            wav_paths.append(INTRO_WAV)
            print("Added intro.wav")
        else:
            print("Warning: intro.wav not found, skipping")

        # Synthesize each transcript line
        for i, line in enumerate(transcript):
            text = line.get("text", "").strip()
            if not text:
                continue
            clip_path = os.path.join(tmpdir, f"line_{i:03d}.wav")
            print(f"  [{i+1}/{len(transcript)}] {text[:60]}...")
            await asyncio.to_thread(synthesize_line, text, clip_path)
            wav_paths.append(clip_path)

        # Stitch all clips
        print(f"\nStitching {len(wav_paths)} clips...")
        await asyncio.to_thread(stitch_wavs, wav_paths, output_path)

    print(f"\nDone. Episode saved to:\n  {output_path}")


async def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else "generation_run:5gvfl3qypqqi4ux90gbs"
    await synthesize_episode(run_id)


if __name__ == "__main__":
    asyncio.run(main())
