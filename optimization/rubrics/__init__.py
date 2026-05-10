"""
optimization/rubrics
====================
Generates judge prompts from (task, user KB, company guidelines) and runs them.

Public API:
    generate_judge_prompt(task, user_kb)    Pure: returns the rendered prompt text
    judge(task, output, user_kb)            async: calls LLM judge, returns Judgment

Two consumers, same artifact:
    1. Runtime judge — score every output, optionally re-roll
    2. GEPA metric (future) — same prompt drives optimization
"""

from .generator import generate_judge_prompt
from .judge import Judgment, judge

__all__ = ["generate_judge_prompt", "judge", "Judgment"]
