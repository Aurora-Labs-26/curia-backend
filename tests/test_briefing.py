"""
tests/test_briefing.py
Tests for studio/briefing_builder.py — packet assembly, listener context, length overrides.
"""

import json

import pytest

from core.kb.schema import Dislikes, Interests, Preferences, UserKB
from studio.briefing_builder import (
    _kb_length_override,
    _kb_listener_context,
    briefing_packet_to_str,
    build_briefing_packet,
    build_source_primitives,
)


# ---------------------------------------------------------------------------
# build_source_primitives
# ---------------------------------------------------------------------------


class TestBuildSourcePrimitives:
    def test_empty_sources(self):
        assert build_source_primitives([], {}) == []

    def test_basic_source(self):
        sources = [{"id": "abc", "title": "Test Article", "url": "https://example.com/post"}]
        insights = {"abc": {"key_insights": "insight1", "core_tensions": "tension1"}}
        result = build_source_primitives(sources, insights)
        assert len(result) == 1
        assert result[0]["title"] == "Test Article"
        assert result[0]["key_insights"] == "insight1"
        assert result[0]["domain"] == "example.com"

    def test_source_id_prefix_stripped(self):
        sources = [{"id": "source:xyz", "title": "T"}]
        insights = {"xyz": {"key_insights": "K"}}
        result = build_source_primitives(sources, insights)
        assert result[0]["key_insights"] == "K"

    def test_no_insights(self):
        sources = [{"id": "abc", "title": "T"}]
        result = build_source_primitives(sources, {})
        assert result[0]["key_insights"] is None
        assert result[0]["core_tensions"] is None

    def test_no_url(self):
        sources = [{"id": "abc", "title": "T"}]
        result = build_source_primitives(sources, {})
        assert result[0]["url"] is None
        assert result[0]["domain"] is None

    def test_www_stripped_from_domain(self):
        sources = [{"id": "a", "url": "https://www.example.com/post"}]
        result = build_source_primitives(sources, {})
        assert result[0]["domain"] == "example.com"

    def test_missing_title_defaults_to_untitled(self):
        sources = [{"id": "a"}]
        result = build_source_primitives(sources, {})
        assert result[0]["title"] == "Untitled"

    def test_multiple_sources(self):
        sources = [
            {"id": "a", "title": "A"},
            {"id": "b", "title": "B"},
        ]
        insights = {"a": {"key_insights": "KA"}, "b": {"core_tensions": "TB"}}
        result = build_source_primitives(sources, insights)
        assert len(result) == 2
        assert result[0]["key_insights"] == "KA"
        assert result[1]["core_tensions"] == "TB"


# ---------------------------------------------------------------------------
# _kb_listener_context
# ---------------------------------------------------------------------------


class TestKbListenerContext:
    def test_none_kb(self):
        assert _kb_listener_context(None) is None

    def test_default_kb_returns_none(self):
        kb = UserKB()
        assert _kb_listener_context(kb) is None

    def test_preferred_tone_included(self):
        kb = UserKB(preferences=Preferences(preferred_tone="dry"))
        ctx = _kb_listener_context(kb)
        assert ctx["preferred_tone"] == "dry"

    def test_preferred_length_included(self):
        kb = UserKB(preferences=Preferences(preferred_length_minutes=8))
        ctx = _kb_listener_context(kb)
        assert ctx["preferred_length_minutes"] == 8

    def test_medium_ambiguity_excluded(self):
        kb = UserKB(preferences=Preferences(tolerates_ambiguity="medium"))
        assert _kb_listener_context(kb) is None

    def test_high_ambiguity_included(self):
        kb = UserKB(preferences=Preferences(tolerates_ambiguity="high"))
        ctx = _kb_listener_context(kb)
        assert ctx["ambiguity_tolerance"] == "high"

    def test_default_novelty_excluded(self):
        kb = UserKB(preferences=Preferences(novelty_appetite=0.5))
        assert _kb_listener_context(kb) is None

    def test_non_default_novelty_included(self):
        kb = UserKB(preferences=Preferences(novelty_appetite=0.9))
        ctx = _kb_listener_context(kb)
        assert ctx["novelty_appetite"] == 0.9

    def test_zero_novelty_included(self):
        kb = UserKB(preferences=Preferences(novelty_appetite=0.0))
        ctx = _kb_listener_context(kb)
        assert ctx["novelty_appetite"] == 0.0

    def test_topics_included(self):
        kb = UserKB(interests=Interests(topics=["AI", "climate"]))
        ctx = _kb_listener_context(kb)
        assert ctx["active_interests"] == ["AI", "climate"]

    def test_empty_topics_excluded(self):
        kb = UserKB(interests=Interests(topics=[]))
        assert _kb_listener_context(kb) is None

    def test_current_obsession_included(self):
        kb = UserKB(interests=Interests(current_obsession="transformers"))
        ctx = _kb_listener_context(kb)
        assert ctx["current_obsession"] == "transformers"

    def test_avoid_themes_included(self):
        kb = UserKB(dislikes=Dislikes(themes=["politics"]))
        ctx = _kb_listener_context(kb)
        assert ctx["avoid_themes"] == ["politics"]

    def test_avoid_tones_included(self):
        kb = UserKB(dislikes=Dislikes(tones=["punchy"]))
        ctx = _kb_listener_context(kb)
        assert ctx["avoid_tones"] == ["punchy"]

    def test_multiple_fields(self):
        kb = UserKB(
            preferences=Preferences(preferred_tone="warm", novelty_appetite=0.8),
            interests=Interests(topics=["tech"]),
        )
        ctx = _kb_listener_context(kb)
        assert "preferred_tone" in ctx
        assert "novelty_appetite" in ctx
        assert "active_interests" in ctx


# ---------------------------------------------------------------------------
# _kb_length_override
# ---------------------------------------------------------------------------


class TestKbLengthOverride:
    def test_none_kb(self):
        assert _kb_length_override(None) is None

    def test_no_preferred_length(self):
        kb = UserKB()
        assert _kb_length_override(kb) is None

    def test_with_preferred_length(self):
        kb = UserKB(preferences=Preferences(preferred_length_minutes=15))
        assert _kb_length_override(kb) == 15


# ---------------------------------------------------------------------------
# build_briefing_packet
# ---------------------------------------------------------------------------


class TestBuildBriefingPacket:
    def test_basic_packet(self):
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=[{"id": "s1", "title": "T1", "url": "https://ex.com/a"}],
            insights={"s1": {"key_insights": "KI"}},
        )
        assert packet["format"] == "clarity_engine"
        assert "format_config" in packet
        assert "episode_constraints" in packet
        assert "source_primitives" in packet
        assert len(packet["source_primitives"]) == 1

    def test_default_editorial_direction(self):
        packet = build_briefing_packet("clarity_engine", [], {})
        assert "most interesting thread" in packet["editorial_direction"]

    def test_custom_editorial_direction(self):
        packet = build_briefing_packet("clarity_engine", [], {}, editorial_direction="Focus on AI safety.")
        assert packet["editorial_direction"] == "Focus on AI safety."

    def test_length_override(self):
        packet = build_briefing_packet("clarity_engine", [], {}, length_override=20)
        assert packet["episode_constraints"]["target_length_minutes"] == 20

    def test_kb_length_override(self):
        kb = UserKB(preferences=Preferences(preferred_length_minutes=8))
        packet = build_briefing_packet("clarity_engine", [], {}, user_kb=kb)
        assert packet["episode_constraints"]["target_length_minutes"] == 8

    def test_explicit_length_beats_kb(self):
        kb = UserKB(preferences=Preferences(preferred_length_minutes=8))
        packet = build_briefing_packet("clarity_engine", [], {}, length_override=20, user_kb=kb)
        assert packet["episode_constraints"]["target_length_minutes"] == 20

    def test_segment_count_override(self):
        packet = build_briefing_packet("clarity_engine", [], {}, segment_count_override=10)
        assert packet["episode_constraints"]["segment_count"] == 10

    def test_listener_context_included(self):
        kb = UserKB(preferences=Preferences(preferred_tone="dry"))
        packet = build_briefing_packet("clarity_engine", [], {}, user_kb=kb)
        assert "listener_context" in packet
        assert packet["listener_context"]["preferred_tone"] == "dry"

    def test_no_listener_context_for_default_kb(self):
        kb = UserKB()
        packet = build_briefing_packet("clarity_engine", [], {}, user_kb=kb)
        assert "listener_context" not in packet

    def test_intro_outro_budgets(self):
        packet = build_briefing_packet("clarity_engine", [], {})
        ec = packet["episode_constraints"]
        assert ec["intro_budget_min"] < ec["intro_budget_words"] < ec["intro_budget_max"]
        assert ec["outro_budget_min"] < ec["outro_budget_words"] < ec["outro_budget_max"]


# ---------------------------------------------------------------------------
# briefing_packet_to_str
# ---------------------------------------------------------------------------


class TestBriefingPacketToStr:
    def test_serializes_to_json(self):
        packet = {"format": "test", "data": [1, 2, 3]}
        result = briefing_packet_to_str(packet)
        parsed = json.loads(result)
        assert parsed["format"] == "test"

    def test_preserves_unicode(self):
        packet = {"text": "café résumé"}
        result = briefing_packet_to_str(packet)
        assert "café" in result
