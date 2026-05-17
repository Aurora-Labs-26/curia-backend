"""
core/audio/stitcher.py
Segment-based audio stitcher. Replaces the old per-line loop.

Pipeline: transcript → merge → split → TTS per-segment → stitch WAVs → output

Used by both batch and streaming pipelines:
  - Batch: prepare_segments → synthesize_segments → stitch_wavs → export MP3
  - Streaming: prepare_segments → yield segments → TTS + stream each
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Callable

from .merger import merge_paragraphs
from .splitter import split_into_segments


def prepare_segments(
    transcript: list[dict],
    max_chars: int = 4500,
) -> list[dict]:
    """
    Transform raw transcript lines into TTS-ready segments.
    Merges same-speaker runs, then splits at TTS API limits.

    Returns list of {speaker, text} dicts — far fewer than input lines.
    """
    if not transcript:
        return []
    merged = merge_paragraphs(transcript)
    return split_into_segments(merged, max_chars=max_chars)


def synthesize_segments(
    segments: list[dict],
    output_dir: Path | str,
    synthesize_fn: Callable[[str, str, str], None],
) -> list[str]:
    """
    Call TTS once per segment. Returns list of WAV file paths.

    Args:
        segments: from prepare_segments()
        output_dir: directory to write WAV files
        synthesize_fn: callable(text, speaker, output_path) — the TTS function
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    wav_paths: list[str] = []

    for i, seg in enumerate(segments):
        out_path = str(output_dir / f"segment_{i:04d}.wav")
        synthesize_fn(seg["text"], seg["speaker"], out_path)
        wav_paths.append(out_path)

    return wav_paths


def stitch_wavs(
    wav_paths: list[str],
    output_path: str,
    gap_ms: int = 400,
) -> None:
    """
    Stitch WAV files with silence gaps between them. Writes to output_path.

    Uses raw wave module (no pydub dependency) for lightweight stitching.
    All inputs must be mono 16-bit PCM at the same sample rate.
    """
    if not wav_paths:
        return

    # Read first file to get format
    with wave.open(wav_paths[0], "rb") as first:
        n_channels = first.getnchannels()
        sampwidth = first.getsampwidth()
        framerate = first.getframerate()

    # Build silence gap
    gap_frames = int(framerate * gap_ms / 1000)
    gap_bytes = b"\x00" * (gap_frames * n_channels * sampwidth)

    with wave.open(output_path, "wb") as out:
        out.setnchannels(n_channels)
        out.setsampwidth(sampwidth)
        out.setframerate(framerate)

        for i, path in enumerate(wav_paths):
            with wave.open(path, "rb") as inp:
                out.writeframes(inp.readframes(inp.getnframes()))
            # Add gap after each segment except the last
            if i < len(wav_paths) - 1:
                out.writeframes(gap_bytes)
