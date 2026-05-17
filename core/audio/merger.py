"""
core/audio/merger.py
Merge consecutive transcript lines by the same speaker into paragraph chunks.

Before: 80 lines → 80 TTS calls (robotic, slow)
After:  80 lines → ~5-10 paragraphs → natural prosody within each chunk
"""

from __future__ import annotations


def merge_paragraphs(transcript: list[dict]) -> list[dict]:
    """
    Group consecutive lines from the same speaker into single paragraphs.

    Input:  [{speaker: "kenji", text: "A."}, {speaker: "kenji", text: "B."}, {speaker: "arjun", text: "C."}]
    Output: [{speaker: "kenji", text: "A. B."}, {speaker: "arjun", text: "C."}]
    """
    if not transcript:
        return []

    merged: list[dict] = []
    current_speaker: str | None = None
    current_texts: list[str] = []

    for line in transcript:
        speaker = (line.get("speaker") or "").strip().lower()
        text = (line.get("text") or "").strip()
        if not text:
            continue

        if speaker == current_speaker:
            current_texts.append(text)
        else:
            if current_speaker is not None and current_texts:
                merged.append({
                    "speaker": current_speaker,
                    "text": " ".join(current_texts),
                })
            current_speaker = speaker
            current_texts = [text]

    # Flush last group
    if current_speaker is not None and current_texts:
        merged.append({
            "speaker": current_speaker,
            "text": " ".join(current_texts),
        })

    return merged
