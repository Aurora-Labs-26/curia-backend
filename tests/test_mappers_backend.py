"""
tests/test_mappers_backend.py
Validate format registry and speaker profile consistency.
"""
import pytest
from studio.formats import FORMATS, get_format, resolve_format_name, FormatConfig
from studio.shows.profiles import SHOW_PROFILES

FRONTEND_TO_BACKEND = {
    "slow-burn": "narrative_drift",
    "sharp-take": "clarity_engine",
    "live-wire": "momentum_loop",
    "open-verdict": "exploration_engine",
}


def test_all_formats_exist():
    expected = {"narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"}
    assert set(FORMATS.keys()) == expected


def test_format_configs_have_required_fields():
    for name, fmt in FORMATS.items():
        assert isinstance(fmt, FormatConfig), f"{name} is not FormatConfig"
        assert fmt.default_length_minutes > 0, f"{name} has no default length"
        assert fmt.default_segment_count > 0, f"{name} has no default segment count"
        assert len(fmt.rules.must_do) > 0, f"{name} has no must_do rules"
        assert len(fmt.rules.must_avoid) > 0, f"{name} has no must_avoid rules"


def test_format_names_match_keys():
    for key, fmt in FORMATS.items():
        assert fmt.name == key, f"key '{key}' != fmt.name '{fmt.name}'"


def test_resolve_format_name_accepts_backend_names():
    for name in FORMATS:
        assert resolve_format_name(name) == name


def test_resolve_format_name_accepts_frontend_slugs():
    for frontend_name, backend_name in FRONTEND_TO_BACKEND.items():
        assert resolve_format_name(frontend_name) == backend_name


def test_resolve_format_name_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown format"):
        resolve_format_name("not_a_format")


def test_get_format_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown format"):
        get_format("not_a_format")


def test_get_format_returns_correct_config():
    for name in FORMATS:
        fmt = get_format(name)
        assert fmt.name == name


def test_show_profiles_match_formats():
    """Every format should have a corresponding show profile."""
    for fmt_name in FORMATS:
        assert fmt_name in SHOW_PROFILES, f"format '{fmt_name}' has no show profile"


def test_speaker_profiles_have_required_fields():
    for show_name, profile in SHOW_PROFILES.items():
        assert profile.speaker_config is not None, f"{show_name} missing speaker_config"
        assert len(profile.speaker_config.speakers) > 0, f"{show_name} has no speakers"
        for speaker in profile.speaker_config.speakers:
            assert speaker.name, f"{show_name} has speaker with no name"
