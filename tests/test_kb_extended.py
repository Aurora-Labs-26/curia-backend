"""
tests/test_kb_extended.py
Extended tests for core/kb — schema validation edge cases + store logic.
"""

from unittest.mock import AsyncMock, patch

import pytest

from core.kb.schema import (
    Dislikes,
    Identity,
    Interests,
    ListeningContext,
    Preferences,
    UserKB,
)
from core.kb.store import _coerce_kb_payload, empty_kb, load_kb, save_kb


# ---------------------------------------------------------------------------
# UserKB schema edge cases
# ---------------------------------------------------------------------------


class TestUserKBEdgeCases:
    def test_preferred_length_boundary_min(self):
        p = Preferences(preferred_length_minutes=3)
        assert p.preferred_length_minutes == 3

    def test_preferred_length_boundary_max(self):
        p = Preferences(preferred_length_minutes=30)
        assert p.preferred_length_minutes == 30

    def test_novelty_zero(self):
        p = Preferences(novelty_appetite=0.0)
        assert p.novelty_appetite == 0.0

    def test_novelty_one(self):
        p = Preferences(novelty_appetite=1.0)
        assert p.novelty_appetite == 1.0

    def test_all_tones(self):
        for tone in ["dry", "warm", "analytical", "conversational", "literary", "punchy", "dispassionate"]:
            p = Preferences(preferred_tone=tone)
            assert p.preferred_tone == tone

    def test_invalid_tone(self):
        with pytest.raises(Exception):
            Preferences(preferred_tone="sarcastic")

    def test_all_ambiguity_values(self):
        for val in ["low", "medium", "high"]:
            p = Preferences(tolerates_ambiguity=val)
            assert p.tolerates_ambiguity == val

    def test_all_format_names(self):
        for fmt in ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]:
            p = Preferences(preferred_formats=[fmt])
            assert fmt in p.preferred_formats

    def test_extra_field_identity_rejected(self):
        with pytest.raises(Exception):
            Identity(name="x", bogus="y")

    def test_extra_field_interests_rejected(self):
        with pytest.raises(Exception):
            Interests(topics=[], bogus="y")

    def test_model_validate_from_dict(self):
        data = {
            "identity": {"name": "Test"},
            "preferences": {"preferred_length_minutes": 10},
            "version": 1,
        }
        kb = UserKB.model_validate(data)
        assert kb.identity.name == "Test"

    def test_round_trip(self):
        kb = UserKB(
            identity=Identity(name="User"),
            preferences=Preferences(preferred_tone="dry", novelty_appetite=0.3),
        )
        data = kb.model_dump()
        kb2 = UserKB.model_validate(data)
        assert kb2 == kb


# ---------------------------------------------------------------------------
# _coerce_kb_payload
# ---------------------------------------------------------------------------


class TestCoercePayload:
    def test_none(self):
        assert _coerce_kb_payload(None) == {}

    def test_dict_passthrough(self):
        d = {"identity": {"name": "Test"}}
        assert _coerce_kb_payload(d) is d

    def test_valid_json_string(self):
        assert _coerce_kb_payload('{"version": 1}') == {"version": 1}

    def test_invalid_json_string(self):
        assert _coerce_kb_payload("not-json{") == {}

    def test_int_returns_empty(self):
        assert _coerce_kb_payload(42) == {}

    def test_list_returns_empty(self):
        assert _coerce_kb_payload([1, 2]) == {}

    def test_empty_string(self):
        assert _coerce_kb_payload("") == {}


# ---------------------------------------------------------------------------
# load_kb
# ---------------------------------------------------------------------------


class TestLoadKb:
    async def test_user_not_found_raises(self):
        with patch("core.kb.store.db_fetchrow", new=AsyncMock(return_value=None)):
            with pytest.raises(ValueError, match="not found"):
                await load_kb("missing-user")

    async def test_null_kb_returns_empty(self):
        with patch("core.kb.store.db_fetchrow", new=AsyncMock(return_value={"user_kb": None})):
            kb = await load_kb("u1")
        assert isinstance(kb, UserKB)
        assert kb.identity.name is None

    async def test_valid_kb_loaded(self):
        payload = {"identity": {"name": "Alice"}, "version": 2}
        with patch("core.kb.store.db_fetchrow", new=AsyncMock(return_value={"user_kb": payload})):
            kb = await load_kb("u1")
        assert kb.identity.name == "Alice"
        assert kb.version == 2

    async def test_invalid_kb_returns_empty(self):
        # extra field should fail validation with extra="forbid"
        payload = {"identity": {"name": "Alice", "bogus_field": "x"}, "version": 1}
        with patch("core.kb.store.db_fetchrow", new=AsyncMock(return_value={"user_kb": payload})):
            kb = await load_kb("u1")
        # Should return empty_kb on validation failure
        assert kb.identity.name is None

    async def test_string_payload_coerced(self):
        with patch("core.kb.store.db_fetchrow", new=AsyncMock(return_value={"user_kb": '{"version": 3}'})):
            kb = await load_kb("u1")
        assert kb.version == 3


# ---------------------------------------------------------------------------
# save_kb
# ---------------------------------------------------------------------------


class TestSaveKb:
    async def test_saves_to_db(self):
        kb = UserKB(identity=Identity(name="Bob"), version=2)
        mock_exec = AsyncMock()
        with patch("core.kb.store.db_execute", new=mock_exec):
            await save_kb("u1", kb)
        mock_exec.assert_awaited_once()
        call_args = mock_exec.call_args
        assert "u1" in str(call_args)
