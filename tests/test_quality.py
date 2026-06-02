"""
tests/test_quality.py
Quality guidelines and rubric rendering. No LLM calls.
"""
from optimization.guidelines.transcript import TRANSCRIPT_GUIDELINES_V1
from optimization.guidelines.outline import OUTLINE_GUIDELINES_V1


def test_transcript_guidelines_not_empty():
    assert len(TRANSCRIPT_GUIDELINES_V1) > 100
    assert "QUALITY FLOOR" in TRANSCRIPT_GUIDELINES_V1
    assert "BANNED" in TRANSCRIPT_GUIDELINES_V1
    assert "REQUIRED" in TRANSCRIPT_GUIDELINES_V1


def test_outline_guidelines_not_empty():
    assert len(OUTLINE_GUIDELINES_V1) > 50


def test_transcript_guidelines_mention_key_rules():
    """Key rules the judge enforces should exist in guidelines."""
    g = TRANSCRIPT_GUIDELINES_V1
    assert "Meta-commentary" in g or "meta-commentary" in g.lower()
    assert "JSON" in g or "json" in g
    assert "format" in g.lower()


def test_transcript_prompt_includes_quality_constraints_field():
    """GenerateTranscript DSPy Signature should have quality_constraints input."""
    from core.prompts.transcript import GenerateTranscript
    # DSPy Signature classes are Pydantic models; fields appear in model_fields
    assert "quality_constraints" in GenerateTranscript.model_fields, (
        "GenerateTranscript is missing the 'quality_constraints' input field"
    )
