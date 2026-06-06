"""
tests/test_quality.py
Quality guidelines and rubric rendering. No LLM calls.
"""
from optimization.guidelines.transcript import TRANSCRIPT_GUIDELINES_V1
from optimization.guidelines.outline import OUTLINE_GUIDELINES_V1


def test_transcript_guidelines_not_empty():
    assert len(TRANSCRIPT_GUIDELINES_V1) > 100


def test_outline_guidelines_not_empty():
    assert len(OUTLINE_GUIDELINES_V1) > 50


def test_prompt_files_contain_quality_rules():
    """Quality rules now live in .txt prompt files, not injected as input fields."""
    from pathlib import Path
    host_a = Path("prompts/transcript_host_a.txt").read_text()
    host_b = Path("prompts/transcript_host_b.txt").read_text()
    merge = Path("prompts/transcript_merge.txt").read_text()
    transcript = Path("prompts/transcript.txt").read_text()

    for prompt in [host_a, host_b, merge, transcript]:
        assert "em dash" in prompt.lower() or "EM DASH" in prompt
        assert "signposting" in prompt.lower() or "SIGNPOSTING" in prompt
        assert "negative parallelism" in prompt.lower() or "NEGATIVE PARALLELISM" in prompt

    # Single-host prompt retains depth rules; host_b and merger do not need them
    assert "EXPLAIN, DO NOT ASSERT" in transcript
    assert "EXPLAIN, DO NOT ASSERT" in host_a
