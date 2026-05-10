"""
tests/test_kb.py
KB schema validation — pure Python, no DB.
"""

import pytest

from core.kb import (
    Dislikes,
    Identity,
    Interests,
    ListeningContext,
    Preferences,
    UserKB,
    empty_kb,
)


def test_empty_kb_has_sane_defaults():
    kb = empty_kb()
    assert kb.identity.name is None
    assert kb.interests.topics == []
    assert kb.preferences.preferred_length_minutes == 11
    assert kb.preferences.tolerates_ambiguity == "medium"
    assert kb.preferences.novelty_appetite == 0.5
    assert kb.dislikes.themes == []


def test_kb_round_trips_via_dict():
    kb = UserKB(
        identity=Identity(name="arihunter"),
        interests=Interests(
            topics=["AI safety", "memory"],
            current_obsession="how attention degrades over a day",
        ),
        preferences=Preferences(
            preferred_length_minutes=11,
            preferred_formats=["clarity_engine"],
            preferred_tone="dry",
            tolerates_ambiguity="high",
            novelty_appetite=0.3,
        ),
        listening_context=ListeningContext(when="morning_commute"),
        dislikes=Dislikes(themes=["productivity hacks"]),
    )
    payload = kb.model_dump()
    rebuilt = UserKB.model_validate(payload)
    assert rebuilt == kb


def test_kb_invalid_format_rejected():
    with pytest.raises(Exception):
        Preferences(preferred_formats=["not_a_real_format"])


def test_kb_invalid_tolerance_rejected():
    with pytest.raises(Exception):
        Preferences(tolerates_ambiguity="extreme")


def test_kb_novelty_bounds():
    Preferences(novelty_appetite=0.0)
    Preferences(novelty_appetite=1.0)
    with pytest.raises(Exception):
        Preferences(novelty_appetite=1.5)
    with pytest.raises(Exception):
        Preferences(novelty_appetite=-0.1)


def test_kb_length_bounds():
    Preferences(preferred_length_minutes=3)
    Preferences(preferred_length_minutes=30)
    with pytest.raises(Exception):
        Preferences(preferred_length_minutes=2)
    with pytest.raises(Exception):
        Preferences(preferred_length_minutes=31)


def test_kb_extra_fields_rejected():
    with pytest.raises(Exception):
        UserKB.model_validate(
            {"identity": {"name": "x"}, "extra_unknown_field": "boom"}
        )
