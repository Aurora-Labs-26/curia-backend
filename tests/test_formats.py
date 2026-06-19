"""
tests/test_formats.py
Tests for studio/formats.py — format configs, resolution, serialization.
All pure functions.
"""

import pytest

from studio.formats import (
    FORMATS,
    WORDS_PER_MINUTE,
    INTRO_WORDS,
    OUTRO_WORDS,
    FormatConfig,
    FormatRules,
    get_format,
    resolve_format_name,
    format_config_to_dict,
    DISPLAY_NAMES,
)


# ---------------------------------------------------------------------------
# FormatConfig properties
# ---------------------------------------------------------------------------


class TestFormatConfigProperties:
    def test_target_words(self):
        fmt = FORMATS["momentum_loop"]
        assert fmt.target_words == round(fmt.default_length_minutes * WORDS_PER_MINUTE)

    def test_effective_intro_words_default(self):
        fmt = FORMATS["narrative_drift"]
        assert fmt.intro_words is None
        assert fmt.effective_intro_words == INTRO_WORDS

    def test_effective_intro_words_override(self):
        fmt = FORMATS["momentum_loop"]
        assert fmt.intro_words == 60
        assert fmt.effective_intro_words == 60

    def test_effective_outro_words_default(self):
        fmt = FORMATS["narrative_drift"]
        assert fmt.outro_words is None
        assert fmt.effective_outro_words == OUTRO_WORDS

    def test_effective_outro_words_override(self):
        fmt = FORMATS["momentum_loop"]
        assert fmt.outro_words == 30
        assert fmt.effective_outro_words == 30


# ---------------------------------------------------------------------------
# resolve_format_name
# ---------------------------------------------------------------------------


class TestResolveFormatName:
    def test_backend_name(self):
        assert resolve_format_name("clarity_engine") == "clarity_engine"

    def test_frontend_slug(self):
        assert resolve_format_name("sharp-take") == "clarity_engine"

    def test_all_slugs(self):
        assert resolve_format_name("slow-burn") == "narrative_drift"
        assert resolve_format_name("live-wire") == "momentum_loop"
        assert resolve_format_name("open-verdict") == "exploration_engine"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown format"):
            resolve_format_name("nonexistent")


# ---------------------------------------------------------------------------
# get_format
# ---------------------------------------------------------------------------


class TestGetFormat:
    def test_known_format(self):
        fmt = get_format("clarity_engine")
        assert fmt.name == "clarity_engine"
        assert fmt.host_a_role == "student"
        assert fmt.host_b_role == "teacher"

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown format"):
            get_format("nonexistent")

    def test_all_formats_exist(self):
        for name in ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]:
            fmt = get_format(name)
            assert fmt.name == name


# ---------------------------------------------------------------------------
# format_config_to_dict
# ---------------------------------------------------------------------------


class TestFormatConfigToDict:
    def test_basic_fields(self):
        fmt = get_format("narrative_drift")
        d = format_config_to_dict(fmt)
        assert d["name"] == "narrative_drift"
        assert d["pacing"] == "slow"
        assert d["display_name"] == "Drift"
        assert "rules" in d
        assert "must_do" in d["rules"]
        assert "must_avoid" in d["rules"]
        assert "target_words" in d

    def test_single_host_no_roles(self):
        fmt = get_format("narrative_drift")
        d = format_config_to_dict(fmt)
        assert "host_a_role" not in d
        assert "host_b_role" not in d

    def test_two_host_has_roles(self):
        fmt = get_format("clarity_engine")
        d = format_config_to_dict(fmt)
        assert d["host_a_role"] == "student"
        assert d["host_b_role"] == "teacher"

    def test_exploration_roles(self):
        fmt = get_format("exploration_engine")
        d = format_config_to_dict(fmt)
        assert d["host_a_role"] == "antithesis holder"
        assert d["host_b_role"] == "thesis holder"

    def test_display_names_all_present(self):
        for name in FORMATS:
            assert name in DISPLAY_NAMES


# ---------------------------------------------------------------------------
# Format config data integrity
# ---------------------------------------------------------------------------


class TestFormatDataIntegrity:
    def test_all_formats_have_rules(self):
        for name, fmt in FORMATS.items():
            assert len(fmt.rules.must_do) > 0, f"{name} missing must_do rules"
            assert len(fmt.rules.must_avoid) > 0, f"{name} missing must_avoid rules"

    def test_all_formats_have_positive_length(self):
        for name, fmt in FORMATS.items():
            assert fmt.default_length_minutes > 0, f"{name} has invalid length"

    def test_all_formats_have_positive_segments(self):
        for name, fmt in FORMATS.items():
            assert fmt.default_segment_count > 0, f"{name} has invalid segment count"

    def test_momentum_loop_is_shortest(self):
        ql = FORMATS["momentum_loop"]
        for name, fmt in FORMATS.items():
            if name != "momentum_loop":
                assert fmt.default_length_minutes >= ql.default_length_minutes
