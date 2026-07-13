"""
core/audio/merger.py
Merge consecutive transcript lines by the same speaker into paragraph chunks.

Before: 80 lines → 80 TTS calls (robotic, slow)
After:  80 lines → ~5-10 paragraphs → natural prosody within each chunk

Each merged paragraph carries line_indices and line_char_ranges so that
word-level timestamps from the TTS provider can be mapped back to the
original transcript line indices for the mobile player.

A chunk also flushes on outline-segment change, not just speaker change.
Without this, a single-host show (speaker never changes) would merge the
entire transcript into one TTS call, leaving no seam anywhere to insert the
segment-transition pause/BGM-crossfade/SFX. Each output paragraph carries
its segment id so downstream stitching knows where those seams are.
"""

from __future__ import annotations


def merge_paragraphs(transcript: list[dict]) -> list[dict]:
    """
    Group consecutive lines from the same speaker AND the same outline segment
    into single paragraphs.

    Input:  [{speaker: "kenji", text: "A.", segment: 1}, {speaker: "kenji", text: "B.", segment: 1},
             {speaker: "kenji", text: "C.", segment: 2}]
    Output: [
        {speaker: "kenji", text: "A. B.", segment: 1, line_indices: [0, 1], line_char_ranges: [(0, 2), (4, 6)]},
        {speaker: "kenji", text: "C.",    segment: 2, line_indices: [2],    line_char_ranges: [(0, 2)]},
    ]

    line_char_ranges: list of (start_char, end_char) for each original line within the
    merged text. Used by the timing mapper to assign word-level timestamps to lines.
    """
    if not transcript:
        return []

    merged: list[dict] = []
    current_speaker: str | None = None
    current_segment: int | None = None
    current_texts: list[str] = []
    current_indices: list[int] = []

    def _flush():
        if current_speaker is None or not current_texts:
            return
        # Build merged text and compute char ranges for each original line
        char_ranges: list[tuple[int, int]] = []
        pos = 0
        for t in current_texts:
            char_ranges.append((pos, pos + len(t)))
            pos += len(t) + 1  # +1 for the space separator

        merged_text = " ".join(current_texts)
        merged.append({
            "speaker": current_speaker,
            "text": merged_text,
            "segment": current_segment,
            "line_indices": list(current_indices),
            "line_char_ranges": char_ranges,
        })

    for idx, line in enumerate(transcript):
        speaker = (line.get("speaker") or "").strip().lower()
        segment = line.get("segment")
        text = (line.get("text") or "").strip()
        if not text:
            continue

        if speaker == current_speaker and segment == current_segment:
            current_texts.append(text)
            current_indices.append(idx)
        else:
            _flush()
            current_speaker = speaker
            current_segment = segment
            current_texts = [text]
            current_indices = [idx]

    _flush()
    return merged
