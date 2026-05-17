"""
core/audio/stream_manager.py
Manages streaming audio to a WebSocket client segment-by-segment.

Protocol (messages sent to client):
  1. {"type": "metadata", "total_segments": N, "speakers": [...]}
  2. {"type": "progress", "segment": 0, "speaker": "kenji", "text_preview": "..."}
     <binary: WAV audio bytes for segment 0>
  3. {"type": "progress", "segment": 1, ...}
     <binary: WAV audio bytes for segment 1>
  ...
  N+1. {"type": "complete", "segments_sent": N}

On TTS error for a segment:
  {"type": "error", "segment": i, "error": "..."}
  (continues with next segment)
"""

from __future__ import annotations

import io
import json
import wave
from typing import Callable, Awaitable, Optional, Protocol

from loguru import logger

from .merger import merge_paragraphs
from .splitter import split_into_segments


class WebSocketLike(Protocol):
    """Minimal interface for a WebSocket — works with FastAPI and mocks."""
    async def send_text(self, data: str) -> None: ...
    async def send_bytes(self, data: bytes) -> None: ...


class StreamManager:
    """
    Orchestrates streaming audio to a WebSocket client.

    Args:
        tts_fn: async callable(text, speaker) → bytes (WAV audio)
        save_path: if set, save concatenated audio to this file for replay
        max_chars: max characters per TTS segment
    """

    def __init__(
        self,
        tts_fn: Callable[[str, str], Awaitable[bytes]],
        save_path: Optional[str] = None,
        max_chars: int = 4500,
    ):
        self.tts_fn = tts_fn
        self.save_path = save_path
        self.max_chars = max_chars

    async def stream_to_websocket(
        self,
        transcript: list[dict],
        ws: WebSocketLike,
    ) -> None:
        """
        Stream audio segments to a WebSocket client.

        Sends metadata → (progress + audio) per segment → completion.
        """
        merged = merge_paragraphs(transcript) if transcript else []
        segments = split_into_segments(merged, max_chars=self.max_chars) if merged else []

        # Send metadata
        await ws.send_text(json.dumps({
            "type": "metadata",
            "total_segments": len(segments),
            "speakers": list({s["speaker"] for s in segments}),
        }))

        if not segments:
            await ws.send_text(json.dumps({
                "type": "complete",
                "segments_sent": 0,
            }))
            return

        all_pcm: list[bytes] = []
        sample_rate = 22050
        sampwidth = 2
        n_channels = 1
        segments_sent = 0

        for i, seg in enumerate(segments):
            # Send progress
            await ws.send_text(json.dumps({
                "type": "progress",
                "segment": i,
                "speaker": seg["speaker"],
                "text_preview": seg["text"][:80],
            }))

            try:
                audio_bytes = await self.tts_fn(seg["text"], seg["speaker"])

                if audio_bytes:
                    # Extract PCM for saving
                    buf = io.BytesIO(audio_bytes)
                    try:
                        with wave.open(buf, "rb") as w:
                            sample_rate = w.getframerate()
                            sampwidth = w.getsampwidth()
                            n_channels = w.getnchannels()
                            all_pcm.append(w.readframes(w.getnframes()))
                    except wave.Error:
                        all_pcm.append(audio_bytes)

                    await ws.send_bytes(audio_bytes)
                    segments_sent += 1

            except Exception as e:
                logger.error(f"TTS error for segment {i}: {e}")
                await ws.send_text(json.dumps({
                    "type": "error",
                    "segment": i,
                    "error": str(e),
                }))

        # Save for replay
        if self.save_path and all_pcm:
            with wave.open(self.save_path, "wb") as out:
                out.setnchannels(n_channels)
                out.setsampwidth(sampwidth)
                out.setframerate(sample_rate)
                for pcm in all_pcm:
                    out.writeframes(pcm)
            logger.info(f"Saved streaming audio to {self.save_path}")

        # Send completion
        await ws.send_text(json.dumps({
            "type": "complete",
            "segments_sent": segments_sent,
        }))
