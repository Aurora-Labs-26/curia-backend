"""
core/audio/ssml.py
Build SSML markup for batch TTS calls.

Wraps segments in <speak> tags with <break> tags between them.
This gives TTS models proper pause cues without artificial silence insertion.
"""

from __future__ import annotations

import html


def build_ssml(segments: list[dict], break_ms: int = 800) -> str:
    """
    Build an SSML string from segments with break tags between them.

    Args:
        segments: list of {speaker, text} dicts
        break_ms: milliseconds of pause between segments

    Returns:
        SSML string wrapped in <speak> tags
    """
    if not segments:
        return "<speak></speak>"

    parts: list[str] = []
    for i, seg in enumerate(segments):
        text = html.escape(seg.get("text", ""))
        parts.append(text)
        if i < len(segments) - 1:
            parts.append(f'<break time="{break_ms}ms"/>')

    return "<speak>" + "".join(parts) + "</speak>"
