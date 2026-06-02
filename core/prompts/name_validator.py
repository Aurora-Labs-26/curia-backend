"""
core/prompts/name_validator.py
Validate whether a string is a real first name using a cheap Haiku call.
Returns the first name if valid, None otherwise.
"""

from __future__ import annotations

import dspy
from loguru import logger

from core.db.connection import db_fetchrow
from core.llm_config import resolve


class ValidateName(dspy.Signature):
    """You are checking if a string is a real human first name.

Reply ONLY with "yes" or "no". Nothing else.
- "yes" if the input is a plausible human first name (any culture/language)
- "no" if it is an email address, username, gibberish, a company name, or not a name at all"""

    candidate: str = dspy.InputField(desc="The string to check")
    answer: str = dspy.OutputField(desc='"yes" or "no"')


_validator = dspy.Predict(ValidateName)


def _extract_first_name(full_name: str | None) -> str | None:
    """Extract a plausible first name from a full name string."""
    if not full_name:
        return None
    name = full_name.strip()
    if not name:
        return None
    # Take the first word as the first name
    first = name.split()[0].strip()
    # Basic sanity: at least 2 chars, no @, no digits
    if len(first) < 2 or "@" in first or any(c.isdigit() for c in first):
        return None
    return first


def _validate_with_haiku(candidate: str) -> bool:
    """Use Haiku to check if the candidate is a real first name."""
    try:
        with dspy.context(lm=resolve.llm("outline")):  # outline → Haiku (cheapest)
            result = _validator(candidate=candidate)
        return result.answer.strip().lower().startswith("yes")
    except Exception as e:
        logger.warning(f"[name_validator] Haiku validation failed: {e}; skipping name")
        return False


async def resolve_listener_name(user_id: str) -> str | None:
    """
    Fetch the user's name from DB, extract first name, validate with Haiku.
    Returns the first name if valid, None otherwise.
    """
    row = await db_fetchrow(
        "SELECT name FROM users WHERE id = $id",
        {"id": user_id},
    )
    if not row:
        return None

    first_name = _extract_first_name(row.get("name"))
    if not first_name:
        return None

    if _validate_with_haiku(first_name):
        logger.info(f"[name_validator] resolved listener name: {first_name}")
        return first_name

    logger.info(f"[name_validator] '{first_name}' did not pass validation; skipping")
    return None
