"""
tests/test_schemas_api.py
Tests for api/schemas.py — Pydantic request model validation, especially speaker_pair.
"""

import pytest

from api.schemas import CreateEpisodeRequest


class TestCreateEpisodeRequestSpeakerPair:
    def test_none_allowed(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=None)
        assert req.speaker_pair is None

    def test_valid_pair(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["kenji", "arjun"])
        assert req.speaker_pair == ["kenji", "arjun"]

    def test_pair_sorted_by_priority(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["arjun", "kenji"])
        assert req.speaker_pair == ["kenji", "arjun"]  # kenji has priority 1

    def test_emeka_arjun_sorted(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["emeka", "arjun"])
        assert req.speaker_pair == ["arjun", "emeka"]

    def test_too_few_raises(self):
        with pytest.raises(Exception, match="exactly 2"):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["kenji"])

    def test_too_many_raises(self):
        with pytest.raises(Exception, match="exactly 2"):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["kenji", "arjun", "emeka"])

    def test_duplicates_raise(self):
        with pytest.raises(Exception, match="distinct"):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=["kenji", "kenji"])

    def test_empty_list_raises(self):
        with pytest.raises(Exception):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker_pair=[])


class TestCreateEpisodeRequestLengthMinutes:
    def test_valid_length(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", length_minutes=10)
        assert req.length_minutes == 10

    def test_min_boundary(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", length_minutes=3)
        assert req.length_minutes == 3

    def test_max_boundary(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", length_minutes=30)
        assert req.length_minutes == 30

    def test_below_min_raises(self):
        with pytest.raises(Exception):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", length_minutes=2)

    def test_above_max_raises(self):
        with pytest.raises(Exception):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", length_minutes=31)

    def test_none_allowed(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine")
        assert req.length_minutes is None


class TestCreateEpisodeRequestSpeaker:
    def test_valid_speaker(self):
        req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker="kenji")
        assert req.speaker == "kenji"

    def test_invalid_speaker_raises(self):
        with pytest.raises(Exception):
            CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker="unknown")

    def test_all_valid_speakers(self):
        for name in ["kenji", "arjun", "emeka"]:
            req = CreateEpisodeRequest(source_ids=[], show_name="clarity_engine", speaker=name)
            assert req.speaker == name
