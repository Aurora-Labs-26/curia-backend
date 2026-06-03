"""
core/audio/splitter.py
Split merged paragraphs into TTS-sized segments.

Respects:
  - max_chars: TTS API character limit (default 4500 to stay under 5000 with overhead)
  - Speaker changes always force a new segment
  - Long paragraphs are split at sentence boundaries
"""

from __future__ import annotations

import re


def _split_at_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_chars."""
    sentences = re.split(r'(?<=[.!?])\s+', text)

    # If no sentence boundaries found and text exceeds limit, hard-split on spaces
    if len(sentences) == 1 and len(text) > max_chars:
        return _hard_split(text, max_chars)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        # If a single sentence exceeds max_chars, hard-split it
        if len(sentence) > max_chars:
            if current:
                chunks.append(" ".join(current))
                current = []
                current_len = 0
            chunks.extend(_hard_split(sentence, max_chars))
            continue
        added_len = len(sentence) + (1 if current else 0)
        if current_len + added_len > max_chars and current:
            chunks.append(" ".join(current))
            current = [sentence]
            current_len = len(sentence)
        else:
            current.append(sentence)
            current_len += added_len

    if current:
        chunks.append(" ".join(current))

    return chunks


def _hard_split(text: str, max_chars: int) -> list[str]:
    """Last resort: split on word boundaries when no sentence punctuation exists."""
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for word in words:
        added_len = len(word) + (1 if current else 0)
        if current_len + added_len > max_chars and current:
            chunks.append(" ".join(current))
            current = [word]
            current_len = len(word)
        else:
            current.append(word)
            current_len += added_len

    if current:
        chunks.append(" ".join(current))

    return chunks


def split_into_segments(
    paragraphs: list[dict],
    max_chars: int = 2000,
) -> list[dict]:
    """
    Split paragraphs into segments suitable for individual TTS calls.

    Rules:
      - Speaker change → new segment
      - Single paragraph exceeding max_chars → split at sentence boundaries
      - Consecutive same-speaker paragraphs merged until max_chars
    """
    if not paragraphs:
        return []

    segments: list[dict] = []
    current_speaker: str | None = None
    current_text = ""

    def flush():
        nonlocal current_speaker, current_text
        if current_speaker is not None and current_text.strip():
            text = current_text.strip()
            if len(text) <= max_chars:
                segments.append({"speaker": current_speaker, "text": text})
            else:
                for chunk in _split_at_sentences(text, max_chars):
                    segments.append({"speaker": current_speaker, "text": chunk})
        current_text = ""

    for para in paragraphs:
        speaker = (para.get("speaker") or "").strip().lower()
        text = (para.get("text") or "").strip()
        if not text:
            continue

        if speaker != current_speaker:
            flush()
            current_speaker = speaker
            current_text = text
        else:
            combined = current_text + " " + text if current_text else text
            if len(combined) <= max_chars:
                current_text = combined
            else:
                flush()
                current_speaker = speaker
                current_text = text

    flush()
    return segments
