"""
core/audio/stream.py
Async generator that streams audio segment-by-segment.

Usage:
    async for chunk in stream_episode_audio(transcript, tts_fn=my_tts):
        websocket.send_bytes(chunk)  # or write to progressive file

The streaming pipeline:
  1. Merge same-speaker lines → paragraphs
  2. Split into TTS-sized segments
  3. For each segment: call TTS async → yield audio bytes immediately
  4. Optionally save all bytes to a file for replay
"""

from __future__ import annotations

import io
import wave
from typing import AsyncIterator, Callable, Awaitable, Optional, Union

from .merger import merge_paragraphs
from .splitter import split_into_segments


async def stream_episode_audio(
    transcript: list[dict],
    tts_fn: Callable[[str, str], Awaitable[bytes]],
    max_chars: int = 4500,
    save_path: Optional[str] = None,
    include_metadata: bool = False,
) -> AsyncIterator[Union[dict, bytes]]:
    """
    Async generator yielding audio chunks, one per segment.

    Args:
        transcript: list of {speaker, text} dicts (raw transcript lines)
        tts_fn: async callable(text, speaker) → bytes (WAV audio bytes)
        max_chars: max chars per TTS call
        save_path: if set, concatenate all chunks and save as WAV file
        include_metadata: if True, yield a metadata dict before audio chunks

    Yields:
        dict (metadata, if include_metadata) then bytes (WAV audio per segment)
    """
    if not transcript:
        return

    merged = merge_paragraphs(transcript)
    segments = split_into_segments(merged, max_chars=max_chars)

    if not segments:
        return

    if include_metadata:
        yield {
            "total_segments": len(segments),
            "speakers": list({s["speaker"] for s in segments}),
        }

    all_pcm: list[bytes] = []
    sample_rate = 22050
    sampwidth = 2
    n_channels = 1

    for seg in segments:
        audio_bytes = await tts_fn(seg["text"], seg["speaker"])
        if not audio_bytes:
            continue

        # Extract PCM from WAV bytes for concatenation
        buf = io.BytesIO(audio_bytes)
        try:
            with wave.open(buf, "rb") as w:
                sample_rate = w.getframerate()
                sampwidth = w.getsampwidth()
                n_channels = w.getnchannels()
                all_pcm.append(w.readframes(w.getnframes()))
        except wave.Error:
            # Not a valid WAV — yield raw bytes anyway
            all_pcm.append(audio_bytes)

        yield audio_bytes

    # Save concatenated audio for replay
    if save_path and all_pcm:
        with wave.open(save_path, "wb") as out:
            out.setnchannels(n_channels)
            out.setsampwidth(sampwidth)
            out.setframerate(sample_rate)
            for pcm in all_pcm:
                out.writeframes(pcm)
