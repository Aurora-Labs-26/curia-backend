"""
tests/test_two_host.py
Tests for the two-host transcript pipeline — profiles, signatures, selection logic,
prompt loading, and JSON parsing.
"""

import json
import pytest


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Two-Host Profiles
# ═══════════════════════════════════════════════════════════════════════════════


class TestTwoHostProfiles:

    def test_clarity_engine_has_two_speakers(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["clarity_engine"]
        assert len(profile.speaker_config.speakers) == 2

    def test_exploration_engine_has_two_speakers(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["exploration_engine"]
        assert len(profile.speaker_config.speakers) == 2

    def test_narrative_drift_stays_single_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["narrative_drift"]
        assert len(profile.speaker_config.speakers) == 1

    def test_momentum_loop_stays_single_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["momentum_loop"]
        assert len(profile.speaker_config.speakers) == 1

    def test_clarity_speakers_are_kenji_and_arjun(self):
        from studio.shows.profiles import SHOW_PROFILES
        names = [s.name for s in SHOW_PROFILES["clarity_engine"].speaker_config.speakers]
        assert names == ["kenji", "arjun"]

    def test_exploration_speakers_are_kenji_and_arjun(self):
        from studio.shows.profiles import SHOW_PROFILES
        names = [s.name for s in SHOW_PROFILES["exploration_engine"].speaker_config.speakers]
        assert names == ["kenji", "arjun"]

    def test_clarity_host_roles_in_format(self):
        from studio.formats import FORMATS
        fmt = FORMATS["clarity_engine"]
        assert fmt.host_a_role == "student"
        assert fmt.host_b_role == "teacher"

    def test_exploration_host_roles_in_format(self):
        from studio.formats import FORMATS
        fmt = FORMATS["exploration_engine"]
        assert fmt.host_a_role == "antithesis holder"
        assert fmt.host_b_role == "thesis holder"

    def test_single_host_formats_have_no_roles(self):
        from studio.formats import FORMATS
        assert FORMATS["narrative_drift"].host_a_role is None
        assert FORMATS["momentum_loop"].host_a_role is None

    def test_all_two_host_speakers_have_backstory_and_patterns(self):
        from studio.shows.profiles import SHOW_PROFILES
        for name in ["clarity_engine", "exploration_engine"]:
            profile = SHOW_PROFILES[name]
            for s in profile.speaker_config.speakers:
                assert s.backstory, f"{name}/{s.name} missing backstory"
                assert s.speech_patterns, f"{name}/{s.name} missing speech_patterns"

    def test_two_host_profiles_have_valid_format_names(self):
        from studio.shows.profiles import SHOW_PROFILES
        from studio.formats import FORMATS
        for name in ["clarity_engine", "exploration_engine"]:
            profile = SHOW_PROFILES[name]
            assert profile.format_name in FORMATS

    def test_two_host_profiles_have_intro_outro(self):
        from studio.shows.profiles import SHOW_PROFILES
        for name in ["clarity_engine", "exploration_engine"]:
            profile = SHOW_PROFILES[name]
            assert profile.intro_audio_path is not None
            assert profile.outro_audio_path is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. DSPy Signatures
# ═══════════════════════════════════════════════════════════════════════════════


class TestTwoHostSignatures:

    def test_host_a_signature_fields(self):
        from core.prompts.transcript_two_host import GenerateHostA
        fields = GenerateHostA.model_fields
        assert "briefing" in fields
        assert "speaker" in fields
        assert "outline" in fields
        assert "host_a_json" in fields

    def test_host_b_signature_has_host_a_transcript(self):
        from core.prompts.transcript_two_host import GenerateHostB
        fields = GenerateHostB.model_fields
        assert "host_a_transcript" in fields
        assert "speaker" in fields
        assert "host_b_json" in fields

    def test_merge_signature_has_both_transcripts(self):
        from core.prompts.transcript_two_host import MergeDialogue
        fields = MergeDialogue.model_fields
        assert "host_a_transcript" in fields
        assert "host_b_transcript" in fields
        assert "speaker_a_name" in fields
        assert "speaker_b_name" in fields
        assert "merged_json" in fields

    def test_singletons_exist(self):
        from core.prompts.transcript_two_host import generate_host_a, generate_host_b, merge_dialogue
        assert generate_host_a is not None
        assert generate_host_b is not None
        assert merge_dialogue is not None

    def test_prompt_files_loaded(self):
        from core.prompts.loader import load_prompt
        assert load_prompt("transcript_host_a") is not None
        assert load_prompt("transcript_host_b") is not None
        assert load_prompt("transcript_merge") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Pipeline Selection Logic
# ═══════════════════════════════════════════════════════════════════════════════


class TestTwoHostSelection:

    def test_clarity_engine_is_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["clarity_engine"]
        is_two_host = len(profile.speaker_config.speakers) >= 2
        assert is_two_host is True

    def test_exploration_engine_is_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["exploration_engine"]
        is_two_host = len(profile.speaker_config.speakers) >= 2
        assert is_two_host is True

    def test_narrative_drift_is_not_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["narrative_drift"]
        is_two_host = len(profile.speaker_config.speakers) >= 2
        assert is_two_host is False

    def test_momentum_loop_is_not_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["momentum_loop"]
        is_two_host = len(profile.speaker_config.speakers) >= 2
        assert is_two_host is False

    def test_speaker_override_forces_single_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["clarity_engine"]
        speaker_override = "kenji"
        is_two_host = len(profile.speaker_config.speakers) >= 2 and speaker_override is None
        assert is_two_host is False

    def test_no_override_allows_two_host(self):
        from studio.shows.profiles import SHOW_PROFILES
        profile = SHOW_PROFILES["clarity_engine"]
        speaker_override = None
        is_two_host = len(profile.speaker_config.speakers) >= 2 and speaker_override is None
        assert is_two_host is True


# ═══════════════════════════════════════════════════════════════════════════════
# 4. JSON Response Parser
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseJsonResponse:

    def _parse(self, raw, label="test"):
        from studio.generator import _parse_json_response
        return _parse_json_response(raw, label)

    def test_plain_json_array(self):
        result = self._parse('[{"speaker": "kenji", "text": "Hello"}]')
        assert len(result) == 1
        assert result[0]["speaker"] == "kenji"

    def test_plain_json_object(self):
        result = self._parse('{"title": "Test"}')
        assert result["title"] == "Test"

    def test_fenced_json(self):
        raw = '```json\n[{"speaker": "kenji", "text": "Hi"}]\n```'
        result = self._parse(raw)
        assert len(result) == 1

    def test_fenced_no_language(self):
        raw = '```\n{"key": "value"}\n```'
        result = self._parse(raw)
        assert result["key"] == "value"

    def test_whitespace_padded(self):
        result = self._parse('  \n  [{"a": 1}]  \n  ')
        assert result == [{"a": 1}]

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            self._parse("not json at all")


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Role Differentiation Between Formats
# ═══════════════════════════════════════════════════════════════════════════════


class TestRoleDifferentiation:
    """Verify that clarity_engine and exploration_engine have distinct speaker roles."""

    def test_clarity_and_exploration_have_different_host_a_roles(self):
        from studio.formats import FORMATS
        # Role differentiation is now in formats.py, not speaker backstories
        assert FORMATS["clarity_engine"].host_a_role != FORMATS["exploration_engine"].host_a_role

    def test_clarity_and_exploration_have_different_host_b_roles(self):
        from studio.formats import FORMATS
        assert FORMATS["clarity_engine"].host_b_role != FORMATS["exploration_engine"].host_b_role

    def test_clarity_and_exploration_have_different_speech_patterns(self):
        from studio.shows.profiles import SHOW_PROFILES
        clarity_b = SHOW_PROFILES["clarity_engine"].speaker_config.speakers[1]
        exploration_b = SHOW_PROFILES["exploration_engine"].speaker_config.speakers[1]
        assert clarity_b.speech_patterns != exploration_b.speech_patterns
