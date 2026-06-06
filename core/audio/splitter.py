"""
core/audio/splitter.py
Split merged paragraphs into TTS-sized segments.

Respects:
  - max_chars: TTS API character limit (default 4500 to stay under 5000 with overhead)
  - Speaker changes always force a new segment
  - Long paragraphs are split at sentence boundaries

Carries line_indices and line_char_ranges metadata from merger.py through to each
output segment so that word-level timestamps can be mapped back to original transcript
line indices.
"""

from __future__ import annotations

import re


def _split_at_sentences(text: str, max_chars: int) -> list[str]:
    """Split text into chunks at sentence boundaries, respecting max_chars."""
    sentences = re.split(r'(?<=[.!?])\s+', text)

    if len(sentences) == 1 and len(text) > max_chars:
        return _hard_split(text, max_chars)

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
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


def _assign_line_metadata_to_chunk(
    chunk: str,
    chunk_start: int,
    line_indices: list[int],
    line_char_ranges: list[tuple[int, int]],
) -> tuple[list[int], list[tuple[int, int]]]:
    """
    Given a chunk that starts at `chunk_start` in the original merged text,
    return the subset of line_indices and line_char_ranges that overlap this chunk,
    with char ranges remapped to be relative to chunk_start.
    """
    chunk_end = chunk_start + len(chunk)
    out_indices: list[int] = []
    out_ranges: list[tuple[int, int]] = []

    for idx, (start, end) in zip(line_indices, line_char_ranges):
        # Line overlaps this chunk if ranges intersect
        if end <= chunk_start or start >= chunk_end:
            continue
        # Clamp and remap relative to chunk_start
        clamped_start = max(start, chunk_start) - chunk_start
        clamped_end = min(end, chunk_end) - chunk_start
        out_indices.append(idx)
        out_ranges.append((clamped_start, clamped_end))

    return out_indices, out_ranges


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

    Each output segment carries:
      speaker, text, line_indices, line_char_ranges
    where line_char_ranges are relative to the segment's text (not the original paragraph).
    """
    if not paragraphs:
        return []

    segments: list[dict] = []
    current_speaker: str | None = None
    current_text = ""
    current_line_indices: list[int] = []
    current_line_char_ranges: list[tuple[int, int]] = []

    def _flush():
        nonlocal current_speaker, current_text, current_line_indices, current_line_char_ranges
        if current_speaker is None or not current_text.strip():
            return
        text = current_text.strip()
        if len(text) <= max_chars:
            segments.append({
                "speaker": current_speaker,
                "text": text,
                "line_indices": list(current_line_indices),
                "line_char_ranges": list(current_line_char_ranges),
            })
        else:
            chunks = _split_at_sentences(text, max_chars)
            cursor = 0
            for chunk in chunks:
                chunk_indices, chunk_ranges = _assign_line_metadata_to_chunk(
                    chunk, cursor, current_line_indices, current_line_char_ranges
                )
                segments.append({
                    "speaker": current_speaker,
                    "text": chunk,
                    "line_indices": chunk_indices,
                    "line_char_ranges": chunk_ranges,
                })
                cursor += len(chunk) + 1  # +1 for the space between chunks
        current_text = ""
        current_line_indices = []
        current_line_char_ranges = []

    for para in paragraphs:
        speaker = (para.get("speaker") or "").strip().lower()
        text = (para.get("text") or "").strip()
        line_indices = para.get("line_indices", [])
        line_char_ranges = para.get("line_char_ranges", [])

        if not text:
            continue

        if speaker != current_speaker:
            _flush()
            current_speaker = speaker
            current_text = text
            current_line_indices = list(line_indices)
            current_line_char_ranges = list(line_char_ranges)
        else:
            # Merge same-speaker paragraphs — remap char ranges relative to combined text
            offset = len(current_text) + 1  # +1 for the space
            combined = current_text + " " + text if current_text else text
            if len(combined) <= max_chars:
                current_text = combined
                current_line_indices.extend(line_indices)
                current_line_char_ranges.extend(
                    (start + offset, end + offset) for start, end in line_char_ranges
                )
            else:
                _flush()
                current_speaker = speaker
                current_text = text
                current_line_indices = list(line_indices)
                current_line_char_ranges = list(line_char_ranges)

    _flush()
    return segments
