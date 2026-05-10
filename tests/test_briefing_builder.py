"""
tests/test_briefing_builder.py
Briefing packet assembly — pure Python, no DB.
"""

from core.kb import Interests, Preferences, UserKB
from studio.briefing_builder import build_briefing_packet


SAMPLE_SOURCES = [
    {"id": "00000000-0000-0000-0000-000000000001", "title": "Alpha"},
    {"id": "00000000-0000-0000-0000-000000000002", "title": "Beta"},
]
SAMPLE_INSIGHTS = {
    "00000000-0000-0000-0000-000000000001": {
        "key_insights": "1. Alpha insight.",
        "core_tensions": "Force A vs Force B. What if?",
    },
    "00000000-0000-0000-0000-000000000002": {
        "key_insights": "1. Beta insight.",
        "examples": "Example named X.",
    },
}


def test_minimum_packet_shape():
    packet = build_briefing_packet(
        format_name="clarity_engine",
        sources=SAMPLE_SOURCES,
        insights=SAMPLE_INSIGHTS,
        editorial_direction="attention as a finite resource",
    )
    assert packet["format"] == "clarity_engine"
    assert packet["episode_constraints"]["target_length_minutes"] > 0
    assert len(packet["source_primitives"]) == 2
    assert "listener_context" not in packet  # no KB → no listener_context block


def test_kb_injects_listener_context_and_overrides_length():
    kb = UserKB(
        interests=Interests(topics=["AI safety"], current_obsession="attention"),
        preferences=Preferences(
            preferred_length_minutes=15,
            preferred_tone="dry",
            tolerates_ambiguity="high",
            novelty_appetite=0.3,
        ),
    )
    packet = build_briefing_packet(
        format_name="clarity_engine",
        sources=SAMPLE_SOURCES,
        insights=SAMPLE_INSIGHTS,
        editorial_direction="attention",
        user_kb=kb,
    )
    # KB-driven length wins over format default
    assert packet["episode_constraints"]["target_length_minutes"] == 15
    # listener_context block exists and reflects the KB
    ctx = packet.get("listener_context")
    assert ctx is not None
    assert ctx.get("preferred_tone") == "dry"
    assert ctx.get("ambiguity_tolerance") == "high"
    assert "AI safety" in ctx.get("active_interests", [])
    assert ctx.get("current_obsession") == "attention"


def test_explicit_length_override_beats_kb():
    kb = UserKB(preferences=Preferences(preferred_length_minutes=15))
    packet = build_briefing_packet(
        format_name="clarity_engine",
        sources=SAMPLE_SOURCES,
        insights=SAMPLE_INSIGHTS,
        editorial_direction="x",
        length_override=8,
        user_kb=kb,
    )
    assert packet["episode_constraints"]["target_length_minutes"] == 8
