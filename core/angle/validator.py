"""
core/angle/validator.py
Validate episode angles before spending LLM credits on generation.

Two layers:
  1. Rule-based (instant, free) — catches gibberish, injections, code, URLs
  2. LLM-based (cheap Haiku call) — checks if the angle is grounded in sources
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass
class AngleValidation:
    valid: bool
    reason: str = ""
    score: int = 0  # 0-5, only set by LLM check


# ── Rule-based patterns ──────────────────────────────────────────────────────

_INJECTION_PATTERNS = re.compile(
    r"(ignore\s+(previous|all|above|prior)\s+instructions"
    r"|forget\s+everything"
    r"|do\s+not\s+follow"
    r"|you\s+are\s+now"
    r"|write\s+me\s+a\b"
    r"|SYSTEM\s*:"
    r"|<\s*/?\s*script"
    r"|act\s+as\s+a\b)",
    re.IGNORECASE,
)

_URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)

_CODE_PATTERNS = re.compile(
    r"(def\s+\w+\s*\(|SELECT\s+\*?\s+FROM|import\s+\w+\s*;|<script|DROP\s+TABLE"
    r"|\}\s*;|\{\s*\n|os\.system|subprocess\.)",
    re.IGNORECASE,
)

_MIN_LENGTH = 10
_MAX_LENGTH = 500
_MIN_REAL_WORDS = 3


def _count_real_words(text: str) -> int:
    """Count words that are actual English-ish words (>2 chars, alpha)."""
    words = text.split()
    return sum(1 for w in words if len(w) > 2 and any(c.isalpha() for c in w))


def _is_gibberish(text: str) -> bool:
    """Check if text is random characters / keyboard mashing."""
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return True
    # Check for repeating characters (zzzzz, aaaa)
    if len(set(c.lower() for c in alpha)) <= 2:
        return True
    # Check consonant-to-vowel ratio — gibberish tends to lack vowels
    vowels = sum(1 for c in alpha if c.lower() in "aeiou")
    if len(alpha) > 5 and vowels == 0:
        return True
    return False


# ── Rule-based validation ─────────────────────────────────────────────────────


def validate_angle_rules(angle: str) -> AngleValidation:
    """
    Instant, free checks. Catches garbage before it reaches the LLM.
    """
    if not angle or not angle.strip():
        return AngleValidation(False, "Empty angle — provide a topic or question")

    angle = angle.strip()

    if len(angle) < _MIN_LENGTH:
        return AngleValidation(False, "Too vague — a good angle needs a specific claim or question")

    if len(angle) > _MAX_LENGTH:
        return AngleValidation(False, "Too long — an angle should be a concise topic, not a full paragraph")

    if _is_gibberish(angle):
        return AngleValidation(False, "Not a valid topic — looks like random text")

    if _INJECTION_PATTERNS.search(angle):
        return AngleValidation(False, "Looks like a prompt instruction, not a topic")

    if _URL_PATTERN.search(angle):
        return AngleValidation(False, "Contains a URL — provide a topic or question instead")

    if _CODE_PATTERNS.search(angle):
        return AngleValidation(False, "Contains code — provide a topic or question instead")

    if _count_real_words(angle) < _MIN_REAL_WORDS:
        return AngleValidation(False, "Too vague — a good angle needs a specific claim or question")

    return AngleValidation(True)


# ── LLM-based validation ─────────────────────────────────────────────────────


async def _call_llm(prompt: str) -> dict:
    """Call Haiku to validate an angle. Returns {score: int, reason: str}."""
    import dspy
    from core.llm_config import resolve

    lm = resolve.llm("outline")  # Haiku — cheap

    with dspy.context(lm=lm):
        response = lm(prompt)

    # Parse the response — expect JSON
    text = response[0] if isinstance(response, list) else str(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to extract score from text
        score_match = re.search(r'"?score"?\s*:\s*(\d)', text)
        reason_match = re.search(r'"?reason"?\s*:\s*"([^"]*)"', text)
        return {
            "score": int(score_match.group(1)) if score_match else 3,
            "reason": reason_match.group(1) if reason_match else text[:200],
        }


async def validate_angle_llm(
    angle: str,
    source_summaries: list[str],
    threshold: int = 3,
) -> AngleValidation:
    """
    LLM check — is this angle grounded in the source material?
    Returns AngleValidation with score (1-5) and reason.
    Fails open — if LLM errors, the angle passes.
    """
    if not source_summaries:
        return AngleValidation(True, "No sources to check against", score=3)

    sources_text = "\n".join(f"- {s}" for s in source_summaries[:10])
    prompt = f"""You are validating a podcast episode angle. Score 1-5 how well this angle is supported by the source material.

SOURCE SUMMARIES:
{sources_text}

PROPOSED ANGLE: {angle}

Score meaning:
1 = Not related to sources at all
2 = Loosely related but a stretch
3 = Somewhat supported, could work
4 = Well supported by the sources
5 = Directly and strongly supported

Respond with JSON only: {{"score": <1-5>, "reason": "<one sentence>"}}"""

    try:
        result = await _call_llm(prompt)
        score = int(result.get("score", 3))
        reason = result.get("reason", "")

        if score >= threshold:
            return AngleValidation(True, reason, score=score)
        else:
            return AngleValidation(False, reason, score=score)

    except Exception as e:
        logger.warning(f"[angle_validator] LLM check failed: {e} — passing angle through")
        return AngleValidation(True, f"LLM check unavailable: {e}", score=3)


# ── Combined validation ──────────────────────────────────────────────────────


async def validate_angle(
    angle: str,
    source_summaries: list[str] | None = None,
) -> AngleValidation:
    """
    Full validation: rules first (free), then LLM (cheap) if rules pass.
    """
    # Step 1: Rules
    rule_result = validate_angle_rules(angle)
    if not rule_result.valid:
        return rule_result

    # Step 2: LLM (only if we have sources to check against)
    if source_summaries:
        return await validate_angle_llm(angle, source_summaries)

    return AngleValidation(True)
