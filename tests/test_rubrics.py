"""
tests/test_rubrics.py
Rubric template rendering — pure Python, no DB or LLM call.
Uses the Python-fallback path in optimization/guidelines.
"""

from core.kb import Dislikes, Identity, Interests, Preferences, UserKB
from optimization.rubrics.generator import (
    _has_user_signal,
    generate_judge_prompt,
)


def test_empty_kb_renders_guideline_only_fallback():
    kb = UserKB()  # all defaults — no user signal
    prompt = generate_judge_prompt(task="transcript", user_kb=kb, output="<X>")
    assert "COMPANY QUALITY FLOOR" in prompt
    assert "No user-specific preferences yet" in prompt
    assert "<X>" in prompt


def test_populated_kb_renders_user_preferences_block():
    kb = UserKB(
        identity=Identity(name="arihunter"),
        interests=Interests(
            topics=["AI safety", "memory"],
            current_obsession="how attention degrades",
        ),
        preferences=Preferences(
            preferred_tone="dry",
            preferred_length_minutes=11,
            tolerates_ambiguity="high",
            novelty_appetite=0.3,
        ),
        dislikes=Dislikes(themes=["productivity hacks"], tones=["sponsor-y"]),
    )
    prompt = generate_judge_prompt(task="transcript", user_kb=kb, output="<X>")
    # Quality floor still present
    assert "COMPANY QUALITY FLOOR" in prompt
    # Each populated KB field surfaces as a bullet
    assert "Preferred tone: dry" in prompt
    assert "Target length: 11 minutes" in prompt
    assert "Ambiguity tolerance: high" in prompt
    assert "AI safety, memory" in prompt
    assert "how attention degrades" in prompt
    assert "productivity hacks" in prompt
    assert "sponsor-y" in prompt


def test_outline_template_renders():
    kb = UserKB(
        preferences=Preferences(preferred_formats=["clarity_engine"], tolerates_ambiguity="high"),
        interests=Interests(topics=["focus"]),
    )
    prompt = generate_judge_prompt(task="outline", user_kb=kb, output="<O>")
    assert "OUTLINE TO EVALUATE" in prompt
    assert "<O>" in prompt
    assert "clarity_engine" in prompt


def test_has_user_signal_detection():
    assert _has_user_signal(UserKB()) is False
    assert _has_user_signal(UserKB(preferences=Preferences(preferred_tone="dry"))) is True
    assert _has_user_signal(UserKB(interests=Interests(topics=["ai"]))) is True
    assert _has_user_signal(UserKB(dislikes=Dislikes(themes=["x"]))) is True
    assert _has_user_signal(UserKB(preferences=Preferences(novelty_appetite=0.1))) is True
    assert _has_user_signal(UserKB(preferences=Preferences(novelty_appetite=0.5))) is False
