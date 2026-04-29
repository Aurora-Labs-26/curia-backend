"""
studio/briefing_builder.py
Deterministic briefing packet builder.
No LLM. Assembles format config + source primitives + constraints into a structured packet.
"""

import json
from typing import Optional

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


def build_briefing_packet(
    format_name: str,
    sources: list[dict],
    insights: dict[str, dict],
    editorial_direction: str = "",
    segment_count_override: Optional[int] = None,
    length_override: Optional[int] = None,
) -> dict:
    """
    Build a complete briefing packet dict.

    Args:
        format_name: one of narrative_drift / clarity_engine / momentum_loop / exploration_engine
        sources: list of source records [{id, title}]
        insights: dict of bare_source_id → {insight_type: content}
        editorial_direction: user-supplied or idea.angle fallback
        segment_count_override: override format default segment count
        length_override: override format default length

    Returns:
        briefing packet dict ready to be serialized as JSON for the outline LLM
    """
    fmt = get_format(format_name)

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

    return packet


def briefing_packet_to_str(packet: dict) -> str:
    """Serialize briefing packet to a JSON string for injection into LLM human message."""
    return json.dumps(packet, indent=2, ensure_ascii=False)
