"""
scripts/synthesize_intro.py
Pre-render the show intro WAV using the configured TTS for the chosen speaker.
Run once whenever you want to regenerate the intro.

Output: studio/intro.wav

Speaker is "kenji" by default. Voice + TTS model are resolved from
config/models.yaml under bindings.speaker.kenji.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from core.tts import synthesize_for_speaker

INTRO_TEXT = (
    "Hello. Welcome in. Thanks for listening. "
    "But before we begin, let's first take a second to settle. "
    "Right. We'll keep this quiet at first. "
    "And then we'll move into it slowly."
)

OUTPUT_PATH = str(Path(__file__).parent.parent / "studio" / "intro.wav")
SPEAKER = "kenji"


def main() -> None:
    print(f"Synthesizing intro → {OUTPUT_PATH}")
    print(f"Speaker: {SPEAKER}")
    print(f"Text: {INTRO_TEXT[:80]}...")
    synthesize_for_speaker(text=INTRO_TEXT, speaker=SPEAKER, output_path=OUTPUT_PATH)
    print(f"Done. Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
