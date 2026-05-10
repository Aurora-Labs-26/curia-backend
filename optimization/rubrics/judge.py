"""
optimization/rubrics/judge.py
Run the rubric as an LLM-as-judge call. Returns a structured Judgment.

Uses the `judge` task binding from config/models.yaml (Sonnet by default).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import dspy
from loguru import logger

from core.kb import UserKB, load_kb
from core.llm_config import resolve

from .generator import generate_judge_prompt


# ---------------------------------------------------------------------------
# Judgment dataclass
# ---------------------------------------------------------------------------


@dataclass
class Judgment:
    overall_score: float                    # 0..1
    preference_score: float                 # 0..1
    floor_violations: list[str] = field(default_factory=list)
    feedback: str = ""
    raw: str = ""                            # raw judge response (for debug)

    @property
    def passed_floor(self) -> bool:
        return not self.floor_violations

    def to_dict(self) -> dict:
        return {
            "overall_score": self.overall_score,
            "preference_score": self.preference_score,
            "floor_violations": self.floor_violations,
            "feedback": self.feedback,
        }


# ---------------------------------------------------------------------------
# Judge call
# ---------------------------------------------------------------------------


_JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _extract_json(raw: str) -> dict:
    """Best-effort JSON extraction from a judge response (handles fenced code blocks etc)."""
    raw = raw.strip()
    if raw.startswith("```"):
        # ```json ... ```  or  ``` ... ```
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.split("```", 1)[0]
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = _JSON_OBJECT_RE.search(raw)
        if not m:
            raise
        return json.loads(m.group(0))


async def judge(
    task: str,
    output: str,
    user_id: str | None = None,
    user_kb: UserKB | None = None,
) -> Judgment:
    """
    Run the LLM judge for `task` against `output`.

    Pass either `user_id` (we'll load_kb) or `user_kb` directly. If neither is
    provided we judge against guidelines only.
    """
    if user_kb is None and user_id is not None:
        try:
            user_kb = await load_kb(user_id)
        except Exception as e:
            logger.warning(f"[judge] could not load KB for {user_id}: {e}; using guidelines-only")
            user_kb = None

    judge_prompt = generate_judge_prompt(task=task, user_kb=user_kb, output=output)

    lm = resolve.llm("judge")
    try:
        # dspy.LM is callable; supports `messages=...` for chat-style providers.
        # We pass a single user message with the fully-rendered prompt.
        response = lm(messages=[{"role": "user", "content": judge_prompt}])
        # dspy.LM returns a list of completions
        raw = response[0] if isinstance(response, list) else str(response)
    except Exception as e:
        logger.error(f"[judge] LLM call failed: {e}")
        # Fall back to neutral judgment so the pipeline doesn't break — but mark it
        return Judgment(
            overall_score=0.5,
            preference_score=0.5,
            floor_violations=[],
            feedback=f"[judge call failed: {e}]",
            raw="",
        )

    try:
        parsed = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"[judge] could not parse judge response as JSON: {raw[:200]}")
        return Judgment(
            overall_score=0.5,
            preference_score=0.5,
            floor_violations=[],
            feedback=f"[judge response unparseable: {e}]",
            raw=raw,
        )

    return Judgment(
        overall_score=float(parsed.get("overall_score", 0.5)),
        preference_score=float(parsed.get("preference_score", 0.5)),
        floor_violations=list(parsed.get("floor_violations") or []),
        feedback=str(parsed.get("feedback") or ""),
        raw=raw,
    )
