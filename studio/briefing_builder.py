"""
studio/briefing_builder.py
Deterministic briefing packet builder.
No LLM. Assembles format config + source primitives + constraints + (optionally)
the user's KB-derived listener context into a structured packet.

The outline LLM reads everything in the packet; adding `listener_context` here
means the outline naturally adapts to user preferences without prompt changes.
"""

import json
from typing import Optional

from core.kb import UserKB
from studio.formats import get_format, format_config_to_dict

PRIMITIVE_FIELDS = ["key_insights", "human_stakes", "core_tensions", "counterpoints", "examples"]


def build_source_primitives(sources: list[dict], insights: dict[str, dict]) -> list[dict]:
    """
    Assemble per-source primitive dicts from the insights map.
    insights keyed by bare source_id (no 'source:' prefix).
    """
    primitives = []
    for source in sources:
        sid = str(source.get("id", "")).replace("source:", "")
        source_insights = insights.get(sid, {})
        primitives.append({
            "title": source.get("title", "Untitled"),
            "key_insights": source_insights.get("key_insights") or None,
            "human_stakes": source_insights.get("human_stakes") or None,
            "core_tensions": source_insights.get("core_tensions") or None,
            "counterpoints": source_insights.get("counterpoints") or None,
            "examples": source_insights.get("examples") or None,
        })
    return primitives


def _kb_listener_context(kb: Optional[UserKB]) -> Optional[dict]:
    """Extract the parts of the KB the outline/transcript LLMs should know about."""
    if kb is None:
        return None
    prefs = kb.preferences
    interests = kb.interests
    dislikes = kb.dislikes
    ctx: dict = {}
    if prefs.preferred_tone:
        ctx["preferred_tone"] = prefs.preferred_tone
    if prefs.preferred_length_minutes:
        ctx["preferred_length_minutes"] = prefs.preferred_length_minutes
    if prefs.tolerates_ambiguity and prefs.tolerates_ambiguity != "medium":
        ctx["ambiguity_tolerance"] = prefs.tolerates_ambiguity
    if prefs.novelty_appetite is not None and prefs.novelty_appetite != 0.5:
        ctx["novelty_appetite"] = prefs.novelty_appetite
    if interests.topics:
        ctx["active_interests"] = list(interests.topics)
    if interests.current_obsession:
        ctx["current_obsession"] = interests.current_obsession
    if dislikes.themes:
        ctx["avoid_themes"] = list(dislikes.themes)
    if dislikes.tones:
        ctx["avoid_tones"] = list(dislikes.tones)
    if not ctx:
        return None
    return ctx


def _kb_length_override(kb: Optional[UserKB]) -> Optional[int]:
    """Use the user's preferred length when set."""
    if kb is None:
        return None
    return kb.preferences.preferred_length_minutes or None


def build_briefing_packet(
    format_name: str,
    sources: list[dict],
    insights: dict[str, dict],
    editorial_direction: str = "",
    segment_count_override: Optional[int] = None,
    length_override: Optional[int] = None,
    user_kb: Optional[UserKB] = None,
) -> dict:
    """
    Build a complete briefing packet dict.

    Args:
        format_name: one of narrative_drift / clarity_engine / momentum_loop / exploration_engine
        sources: list of source records [{id, title}]
        insights: dict of bare_source_id → {insight_type: content}
        editorial_direction: user-supplied or idea.angle fallback
        segment_count_override: override format default segment count
        length_override: override format default length (KB takes precedence if neither passed
            explicitly)
        user_kb: optional UserKB. When provided, listener_context is derived and the user's
            preferred_length_minutes is used as length_override (unless a more specific
            length_override was passed in directly).

    Returns:
        briefing packet dict ready to be serialized as JSON for the outline LLM
    """
    fmt = get_format(format_name)

    # KB-derived length is a sane override only if no explicit one was passed.
    if length_override is None:
        length_override = _kb_length_override(user_kb)

    packet = {
        "format": format_name,
        "format_config": format_config_to_dict(fmt),
        "episode_constraints": {
            "target_length_minutes": length_override or fmt.default_length_minutes,
            "segment_count": segment_count_override or fmt.default_segment_count,
        },
        "editorial_direction": editorial_direction or "Follow the most interesting thread in the material.",
        "source_primitives": build_source_primitives(sources, insights),
    }

    listener_context = _kb_listener_context(user_kb)
    if listener_context:
        packet["listener_context"] = listener_context

    return packet


def briefing_packet_to_str(packet: dict) -> str:
    """Serialize briefing packet to a JSON string for injection into LLM human message."""
    return json.dumps(packet, indent=2, ensure_ascii=False)
