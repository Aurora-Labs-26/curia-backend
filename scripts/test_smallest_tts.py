"""
scripts/test_smallest_tts.py
Quick smoke test for Smallest.ai Lightning TTS.
Synthesizes one line per speaker and writes WAV files to /tmp/.

Usage:
    python3 scripts/test_smallest_tts.py
"""

import os
import sys

# Make sure we can import from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from core.tts import synthesize_for_speaker

TESTS = [
    ("kenji",  "emily",  "Memory isn't a recording device. It's a reconstruction machine."),
    ("arjun",  "george", "The interesting questions are always upstream of the policy solution."),
    ("emeka",  "james",  "Most coordination problems look like technical problems until they don't."),
]

def main():
    print("Testing Smallest.ai Lightning TTS...\n")
    all_passed = True

    for speaker, voice_id, text in TESTS:
        out_path = f"/tmp/test_tts_{speaker}.wav"
        print(f"  [{speaker}] voice={voice_id}")
        print(f"    text: {text[:60]}...")
        try:
            fmt = synthesize_for_speaker(text=text, speaker=speaker, output_path=out_path)
            size = os.path.getsize(out_path)
            print(f"    ✓ output: {out_path} ({size:,} bytes, format={fmt})\n")
        except Exception as e:
            print(f"    ✗ FAILED: {e}\n")
            all_passed = False

    if all_passed:
        print("All speakers synthesized successfully.")
        print("Files written to /tmp/test_tts_kenji.wav, /tmp/test_tts_arjun.wav, /tmp/test_tts_emeka.wav")
    else:
        print("One or more speakers failed.")
        sys.exit(1)

if __name__ == "__main__":
    main()
