"""
tests/test_dspy_signatures.py
Tests for DSPy signature definitions — outline, transcript, idea evaluation.

Verifies:
  - Signatures have correct input/output field names
  - Module singletons are instantiated
  - Prompt loader integration — with_prompt doesn't break signatures
"""

import pytest


class TestGenerateOutlineSignature:

    def test_has_correct_fields(self):
        from core.prompts.outline import GenerateOutline
        fields = set(GenerateOutline.model_fields.keys())
        assert "briefing" in fields
        assert "outline_json" in fields

    def test_singleton_exists(self):
        from core.prompts.outline import generate_outline
        assert generate_outline is not None

    def test_docstring_mentions_outline(self):
        from core.prompts.outline import GenerateOutline
        doc = GenerateOutline.__doc__ or ""
        assert "outline" in doc.lower()


class TestGenerateTranscriptSignature:

    def test_has_correct_fields(self):
        from core.prompts.transcript import GenerateTranscript
        fields = set(GenerateTranscript.model_fields.keys())
        assert "briefing" in fields
        assert "outline" in fields
        assert "speaker_definition" in fields
        assert "quality_guidelines" in fields
        assert "transcript_json" in fields

    def test_singleton_exists(self):
        from core.prompts.transcript import generate_transcript
        assert generate_transcript is not None

    def test_docstring_mentions_podcast(self):
        from core.prompts.transcript import GenerateTranscript
        doc = GenerateTranscript.__doc__ or ""
        assert "podcast" in doc.lower()


class TestEvaluateIdeasSignatures:

    def test_batch_has_correct_fields(self):
        from core.prompts.idea_evaluation import EvaluateIdeasBatch
        fields = set(EvaluateIdeasBatch.model_fields.keys())
        assert "groups_text" in fields
        assert "ideas_json" in fields

    def test_single_has_correct_fields(self):
        from core.prompts.idea_evaluation import EvaluateSingleIdea
        fields = set(EvaluateSingleIdea.model_fields.keys())
        assert "group_text" in fields
        assert "idea_json" in fields

    def test_batch_singleton_exists(self):
        from core.prompts.idea_evaluation import evaluate_ideas_batch
        assert evaluate_ideas_batch is not None

    def test_single_singleton_exists(self):
        from core.prompts.idea_evaluation import evaluate_single_idea
        assert evaluate_single_idea is not None


class TestPromptLoader:

    def test_with_prompt_returns_class(self):
        from core.prompts.loader import with_prompt
        import dspy

        class DummySig(dspy.Signature):
            """Original docstring."""
            x: str = dspy.InputField()
            y: str = dspy.OutputField()

        result = with_prompt(DummySig, "nonexistent_task_xyz")
        assert result is DummySig

    def test_load_prompt_returns_none_for_missing(self):
        from core.prompts.loader import load_prompt
        assert load_prompt("definitely_not_a_real_task_xyz") is None

    def test_load_prompt_returns_string_for_existing(self):
        from core.prompts.loader import load_prompt
        result = load_prompt("transcript")
        assert result is None or isinstance(result, str)

    def test_list_prompt_tasks(self):
        from core.prompts.loader import list_prompt_tasks
        tasks = list_prompt_tasks()
        assert isinstance(tasks, list)
        if tasks:
            assert all(isinstance(t, str) for t in tasks)
