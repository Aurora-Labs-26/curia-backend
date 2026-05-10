"""
optimization/rubrics/generator.py
Render the judge prompt for a (task, user_kb) pair.

Templates live in optimization/rubrics/templates/<task>.j2 and combine:
  - Company guidelines (fixed, from optimization/guidelines/)
  - User KB (per-user, from core/kb/)

Pure function — no I/O beyond reading the template file. Caller passes the
output string at the time of judging (templates have an `{{ output }}` slot).

When rendering with no KB (or an empty one), the template falls back to a
guideline-only prompt — `has_user_signal` flag is set False.
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.kb import UserKB
from optimization.guidelines import get_guidelines, get_guidelines_async

_TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(_TEMPLATE_DIR),
    autoescape=select_autoescape([]),    # plain-text prompt, no escaping needed
    # trim_blocks / lstrip_blocks left OFF — newlines between bullet lines matter
    # in prompts; we use the `{%-` / `-%}` strip syntax explicitly where we want
    # whitespace control (e.g. around section headers).
    trim_blocks=False,
    lstrip_blocks=False,
)


def _has_user_signal(kb: UserKB) -> bool:
    """True if the KB contains anything that could shape the rubric — false for empty defaults."""
    if kb.preferences.preferred_tone:
        return True
    if kb.preferences.preferred_formats:
        return True
    if kb.interests.topics or kb.interests.current_obsession:
        return True
    if kb.dislikes.themes or kb.dislikes.tones or kb.dislikes.formats:
        return True
    if kb.preferences.tolerates_ambiguity != "medium":
        return True
    if kb.preferences.novelty_appetite != 0.5:
        return True
    return False


def generate_judge_prompt(
    task: str,
    user_kb: UserKB | None,
    output: str = "{{ output }}",       # caller substitutes the actual output later
) -> str:
    """
    Render the full judge prompt for `task`. Sync — uses guideline cache or fallback.
    Prefer `generate_judge_prompt_async()` when called from async code.
    """
    template = _env.get_template(f"{task}.j2")
    guidelines = get_guidelines(task)
    kb = user_kb or UserKB()
    return template.render(
        guidelines=guidelines,
        user_kb=kb.model_dump(),
        has_user_signal=_has_user_signal(kb),
        output=output,
    )


async def generate_judge_prompt_async(
    task: str,
    user_kb: UserKB | None,
    output: str = "{{ output }}",
) -> str:
    """Async variant — refreshes DB-backed guidelines cache. Use this in API/worker code."""
    template = _env.get_template(f"{task}.j2")
    guidelines = await get_guidelines_async(task)
    kb = user_kb or UserKB()
    return template.render(
        guidelines=guidelines,
        user_kb=kb.model_dump(),
        has_user_signal=_has_user_signal(kb),
        output=output,
    )
