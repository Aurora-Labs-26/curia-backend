"""
scripts/synthesize_intro.py
Synthesize the show intro using XTTS and Kenji's reference WAV.
Output: studio/intro.wav
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

XTTS_PYTHON = "/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/bin/python"
KENJI_WAV = "/Users/bhabanimohapatra/Documents/Projects/curia/studio/kenji_reference.wav"
OUTPUT_PATH = "/Users/bhabanimohapatra/Documents/Projects/curia/studio/intro.wav"

INTRO_TEXT = """Hello.

Welcome in.

Thanks for listening.

But before we begin, let's first take a second to settle.

Right.

We'll keep this quiet at first. And then we'll move into it slowly."""


def synthesize(text: str, voice_id: str, output_path: str):
    script = f"""
import sys
sys.path.insert(0, '/Users/bhabanimohapatra/Documents/Projects/Hypothesis/progress/xtts_env/lib/python3.11/site-packages')
from TTS.api import TTS
tts = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=False)
tts.tts_to_file(text={json.dumps(text)}, speaker_wav={json.dumps(voice_id)}, language="en", file_path={json.dumps(output_path)})
"""
    result = subprocess.run([XTTS_PYTHON, "-c", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"XTTS failed:\n{result.stderr[-500:]}")


if __name__ == "__main__":
    print("Synthesizing intro...")
    print(f"Text:\n{INTRO_TEXT}\n")
    synthesize(INTRO_TEXT, KENJI_WAV, OUTPUT_PATH)
    print(f"Done. Saved to: {OUTPUT_PATH}")
