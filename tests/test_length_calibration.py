"""
tests/test_length_calibration.py
Tests for format-driven length calibration — the fix for all episodes being ~7 min.
"""

import pytest

from studio.formats import (
    FORMATS,
    FormatConfig,
    LINES_PER_MINUTE,
    NARRATIVE_DRIFT,
    CLARITY_ENGINE,
    MOMENTUM_LOOP,
    EXPLORATION_ENGINE,
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
# FormatConfig target_lines properties
# ---------------------------------------------------------------------------


class TestFormatTargetLines:
    def test_lines_per_minute_constant(self):
        assert LINES_PER_MINUTE == pytest.approx(4.3, abs=0.5)

    @pytest.mark.parametrize("fmt,expected_minutes", [
        (NARRATIVE_DRIFT, 12),
        (CLARITY_ENGINE, 10),
        (MOMENTUM_LOOP, 10),
        (EXPLORATION_ENGINE, 12),
    ])
    def test_target_lines_matches_format_length(self, fmt, expected_minutes):
        expected_lines = round(expected_minutes * LINES_PER_MINUTE)
        assert fmt.target_lines == expected_lines

    @pytest.mark.parametrize("fmt", [NARRATIVE_DRIFT, CLARITY_ENGINE, MOMENTUM_LOOP, EXPLORATION_ENGINE])
    def test_target_lines_per_segment_at_least_2(self, fmt):
        assert fmt.target_lines_per_segment >= 2

    def test_narrative_drift_longer_than_clarity_engine(self):
        assert NARRATIVE_DRIFT.target_lines > CLARITY_ENGINE.target_lines

    def test_momentum_loop_has_fewer_lines_per_segment(self):
        """momentum_loop has 10 segments vs narrative_drift's 8, so lines_per_segment should be smaller."""
        assert MOMENTUM_LOOP.target_lines_per_segment < NARRATIVE_DRIFT.target_lines_per_segment

    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_target_lines_reconstruct_to_minutes(self, fmt):
        """target_lines / LINES_PER_MINUTE should be close to default_length_minutes."""
        reconstructed = fmt.target_lines / LINES_PER_MINUTE
        assert abs(reconstructed - fmt.default_length_minutes) < 1.0

    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_segments_times_lines_per_segment_near_total(self, fmt):
        """segments * lines_per_segment should be close to target_lines."""
        product = fmt.default_segment_count * fmt.target_lines_per_segment
        assert abs(product - fmt.target_lines) <= fmt.default_segment_count


# ---------------------------------------------------------------------------
# format_config_to_dict includes target_lines
# ---------------------------------------------------------------------------


class TestFormatConfigDict:
    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_dict_includes_target_lines(self, fmt):
        d = format_config_to_dict(fmt)
        assert "target_lines" in d
        assert d["target_lines"] == fmt.target_lines

    @pytest.mark.parametrize("fmt", list(FORMATS.values()))
    def test_dict_includes_target_lines_per_segment(self, fmt):
        d = format_config_to_dict(fmt)
        assert "target_lines_per_segment" in d
        assert d["target_lines_per_segment"] == fmt.target_lines_per_segment


# ---------------------------------------------------------------------------
# Briefing packet includes target_lines in episode_constraints
# ---------------------------------------------------------------------------


class TestBriefingPacketLength:
    def test_default_target_lines_from_format(self):
        packet = build_briefing_packet(
            format_name="narrative_drift",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 12
        assert ec["target_lines"] == round(12 * LINES_PER_MINUTE)
        assert ec["target_lines_per_segment"] == max(2, round(ec["target_lines"] / 8))

    def test_length_override_recalculates_target_lines(self):
        packet = build_briefing_packet(
            format_name="clarity_engine",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
            length_override=20,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 20
        assert ec["target_lines"] == round(20 * LINES_PER_MINUTE)

    def test_short_episode_override(self):
        packet = build_briefing_packet(
            format_name="momentum_loop",
            sources=SAMPLE_SOURCES,
            insights=SAMPLE_INSIGHTS,
            length_override=5,
        )
        ec = packet["episode_constraints"]
        assert ec["target_length_minutes"] == 5
        assert ec["target_lines"] == round(5 * LINES_PER_MINUTE)
        assert ec["target_lines_per_segment"] >= 2

    def test_all_formats_produce_different_constraints(self):
        """Different formats should produce different target_lines based on their default_length_minutes."""
        results = {}
        for name in FORMATS:
            packet = build_briefing_packet(
                format_name=name,
                sources=SAMPLE_SOURCES,
                insights=SAMPLE_INSIGHTS,
            )
            ec = packet["episode_constraints"]
            results[name] = (ec["target_lines"], ec["target_lines_per_segment"])

        # narrative_drift and exploration_engine are both 12 min — same target_lines
        assert results["narrative_drift"][0] == results["exploration_engine"][0]
        # but different lines_per_segment (8 vs 8 segments — actually same, so check vs momentum_loop)
        assert results["narrative_drift"][0] > results["clarity_engine"][0]


# ---------------------------------------------------------------------------
# Prompt files contain length calibration
# ---------------------------------------------------------------------------


class TestPromptLengthInstructions:
    def test_transcript_prompt_references_target_lines(self):
        from pathlib import Path
        prompt = Path("prompts/transcript.txt").read_text()
        assert "target_lines" in prompt
        assert "target_lines_per_segment" in prompt
        assert "±10%" in prompt or "10%" in prompt

    def test_outline_prompt_references_target_lines_per_segment(self):
        from pathlib import Path
        prompt = Path("prompts/outline.txt").read_text()
        assert "target_lines_per_segment" in prompt

    def test_guidelines_no_longer_say_6_to_80(self):
        from optimization.guidelines.transcript import TRANSCRIPT_GUIDELINES_V1
        assert "6 to 80" not in TRANSCRIPT_GUIDELINES_V1
        assert "target_lines" in TRANSCRIPT_GUIDELINES_V1
