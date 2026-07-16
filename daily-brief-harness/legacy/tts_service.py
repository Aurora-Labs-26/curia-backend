"""
Smallest.ai Lightning TTS service for daily brief audio generation.

Chunks text at SMALLEST_MAX_CHARS (200), synthesizes each chunk,
stitches PCM bytes into a single WAV file.
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import wave
from typing import Optional

import httpx

SMALLEST_MAX_CHARS = 200  # Smallest.ai hard limit
SMALLEST_SAMPLE_RATE = 24000
SMALLEST_API_URL = "https://waves-api.smallest.ai/api/v1/lightning/get_speech"


def _chunk_text(text: str, max_chars: int = SMALLEST_MAX_CHARS) -> list[str]:
    """Split text into chunks at sentence boundaries, then word boundaries."""

    def _by_words(s: str) -> list[str]:
        words = s.split()
        parts: list[str] = []
        cur = ""
        for w in words:
            while len(w) > max_chars:
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(w[:max_chars])
                w = w[max_chars:]
            if not cur:
                cur = w
            elif len(cur) + 1 + len(w) <= max_chars:
                cur += " " + w
            else:
                parts.append(cur)
                cur = w
        if cur:
            parts.append(cur)
        return parts

    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: list[str] = []
    for sent in sentences:
        if len(sent) <= max_chars:
            chunks.append(sent)
        else:
            chunks.extend(_by_words(sent))
    return [c for c in chunks if c.strip()]


def _write_wav(pcm_chunks: list[bytes], sample_rate: int = SMALLEST_SAMPLE_RATE) -> bytes:
    """Combine PCM chunks into a WAV file, returned as bytes."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        for chunk in pcm_chunks:
            w.writeframes(chunk)
    return buf.getvalue()


async def synthesize_text(
    text: str,
    voice_id: Optional[str] = None,
) -> bytes:
    """
    Synthesize full text → WAV bytes.
    200-char chunks, sequential, 60s timeout per chunk (matches curia-backend).
    Silence fallback per chunk if API fails.
    """
    voice_id = voice_id or os.environ["BRIEF_VOICE_ID"]
    api_key = os.environ["SMALLEST_API_KEY"]

    clean_text = re.sub(r"\[[^\]]+\]", "", text).strip()
    clean_text = re.sub(r"\s{2,}", " ", clean_text)
    if not clean_text:
        raise ValueError("No text to synthesize")

    chunks = _chunk_text(clean_text)
    logger.info(f"[tts] {len(chunks)} chunks, sizes={[len(c) for c in chunks]}")
    pcm_parts: list[bytes] = []

    async with httpx.AsyncClient(timeout=60) as client:
        for i, chunk in enumerate(chunks):
            try:
                resp = await client.post(
                    SMALLEST_API_URL,
                    json={
                        "text": chunk,
                        "voice_id": voice_id,
                        "sample_rate": SMALLEST_SAMPLE_RATE,
                        "add_wav_header": False,
                    },
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code != 200:
                    logger.warning(f"[tts] chunk {i} error {resp.status_code}: {resp.text[:100]}, using silence")
                    pcm_parts.append(b"\x00" * (SMALLEST_SAMPLE_RATE * 2))
                else:
                    pcm_parts.append(resp.content)
                    logger.info(f"[tts] chunk {i} ok ({len(resp.content)} bytes)")
            except Exception as exc:
                logger.warning(f"[tts] chunk {i} failed: {exc}, using silence")
                pcm_parts.append(b"\x00" * (SMALLEST_SAMPLE_RATE * 2))

    return _write_wav(pcm_parts)
