"""
tests/test_length_calibration.py
Tests for format-driven length calibration using word-count budgets.
"""

import pytest

from studio.formats import (
    FORMATS,
    FormatConfig,
    WORDS_PER_MINUTE,
    INTRO_WORDS,
    OUTRO_WORDS,
    NARRATIVE_DRIFT,
    CLARITY_ENGINE,
    format_config_to_dict,
)
from studio.briefing_builder import build_briefing_packet


SAMPLE_SOURCES = [
    {"id": "00000000-0000-0000-0000-000000000001", "title": "Alpha"},
]
SAMPLE_INSIGHTS = {
    "00000000-0000-0000-0000-000000000001": {
        "key_insights": "1. Test insight.",
    },
}


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_words_per_minute_constant(self):
        assert WORDS_PER_MINUTE == 150

    def test_intro_outro_constants(self):
        assert INTRO_WORDS == 150
        assert OUTRO_WORDS == 70

    def test_narrative_drift_longer_than_clarity_engine(self):
        assert NARRATIVE_DRIFT.default_length_minutes > CLARITY_ENGINE.default_length_minutes


# ---------------------------------------------------------------------------
# format_config_to_dict
# ---------------------------------------------------------------------------


class TestFormatConfigDict:
    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_dict_includes_display_name(self, fmt):
        d = format_config_to_dict(fmt)
        assert "display_name" in d
        assert isinstance(d["display_name"], str)
        assert len(d["display_name"]) > 0

    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_dict_includes_target_words(self, fmt):
        d = format_config_to_dict(fmt)
        assert "target_words" in d
        assert isinstance(d["target_words"], int)
        assert d["target_words"] > 0

    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_dict_includes_structure_and_rules(self, fmt):
        d = format_config_to_dict(fmt)
        assert "structure_pattern" in d
        assert "rules" in d
        assert "must_do" in d["rules"]
        assert "must_avoid" in d["rules"]


# ---------------------------------------------------------------------------
# Briefing packet episode_constraints
# ---------------------------------------------------------------------------


class TestBriefingPacketLength:
    def test_default_target_words_from_format(self):
        packet = build_briefing_packet(
            format_name="narrative_drift",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 12
        assert ec["target_words"] == round(12 * WORDS_PER_MINUTE)
        assert ec["intro_budget_words"] == INTRO_WORDS
        assert ec["outro_budget_words"] == OUTRO_WORDS

    def test_length_override_recalculates_target_words(self):
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
            length_override=20,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 20
        assert ec["target_words"] == round(20 * WORDS_PER_MINUTE)

    def test_short_episode_override(self):
        packet = build_briefing_packet(
            format_name="momentum_loop",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
            length_override=5,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 5
        assert ec["target_words"] == round(5 * WORDS_PER_MINUTE)

    def test_all_formats_produce_different_constraints(self):
        """Different formats should produce different target_words based on default_length_minutes."""
        results = {}
        for name in FORMATS:
            packet = build_briefing_packet(
                format_name=name,
                sources=SAMPLE_SOURCES,
                insights=SAMPLE_INSIGHTS,
            )
            results[name] = packet["episode_constraints"]["target_words"]

        assert results["narrative_drift"] == results["exploration_engine"]
        assert results["narrative_drift"] > results["clarity_engine"]

    def test_no_per_segment_budgets_in_packet(self):
        """Briefing packet should not include per_segment_word_budgets — LLM distributes freely."""
        packet = build_briefing_packet(
            format_name="exploration_engine",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
        )
        assert "per_segment_word_budgets" not in packet["episode_constraints"]
        assert "target_words_per_segment" not in packet["episode_constraints"]


# ---------------------------------------------------------------------------
# Prompt files reference target_words
# ---------------------------------------------------------------------------


class TestPromptWordInstructions:
    def test_transcript_prompt_references_target_words(self):
        from pathlib import Path
        prompt = Path("prompts/transcript.txt").read_text()
        assert "target_words" in prompt
        assert "intro_budget_words" in prompt

    def test_outline_prompt_references_target_words(self):
        from pathlib import Path
        prompt = Path("prompts/outline.txt").read_text()
        assert "target_words" in prompt
        assert "segment_count" in prompt
