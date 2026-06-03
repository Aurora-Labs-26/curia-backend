"""
tests/test_mappers_backend.py
Validate format registry and speaker profile consistency.
"""
from studio.formats import FORMATS, get_format, FormatConfig
from studio.shows.profiles import SHOW_PROFILES


def test_all_formats_exist():
    expected = {"narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine", "crossfire"}
    assert set(FORMATS.keys()) == expected


def test_format_configs_have_required_fields():
    for name, fmt in FORMATS.items():
        assert isinstance(fmt, FormatConfig), f"{name} is not FormatConfig"
        assert fmt.default_length_minutes > 0, f"{name} has no default length"
        assert fmt.default_segment_count > 0, f"{name} has no default segment count"
        assert len(fmt.rules.must_do) > 0, f"{name} has no must_do rules"
        assert len(fmt.rules.must_avoid) > 0, f"{name} has no must_avoid rules"


def test_get_format_raises_on_unknown():
    import pytest
    with pytest.raises(ValueError, match="Unknown format"):
        get_format("not_a_format")


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
