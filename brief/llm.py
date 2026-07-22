"""
brief/llm.py
Daily-brief LLM chokepoint, ported from daily-brief-harness's llm_service.py
onto core/llm_config (see "dailybrief analysis v1.md" §7).

What changed in the port:
  - model selection comes from config/models.yaml task bindings
    (brief.score_curate / brief.segment / brief.bookends) via resolve.llm —
    no hardcoded model IDs
  - no "Simulated Mode" mock fallback (prod data-integrity hazard) and no
    per-request X-Anthropic-API-Key override
  - judge steps (relevance / order / faithfulness / tone) are PARKED with the
    eval harness — routing them here raises until that phase is built

Calls are synchronous (dspy) — async callers wrap in run_in_executor, same as
the transformations pipeline.
"""

from __future__ import annotations

import json
import re

from loguru import logger

# Steps that are live in the integrated brief pipeline. The harness's judge
# steps are intentionally absent (parked as future analytics).
BRIEF_STEPS = ("score_curate", "segment", "bookends")


def _resolve_lm(task: str):
    """Seam for tests; returns the configured dspy LM for a task binding."""
    from core.llm_config import resolve
    return resolve.llm(task)


def _call_llm_sync(system: str, user: str, step: str, json_response: bool):
    if step not in BRIEF_STEPS:
        raise ValueError(
            f"unknown brief step {step!r} — live steps are {BRIEF_STEPS}; "
            "judge steps are parked with the eval harness"
        )
    lm = _resolve_lm(f"brief.{step}")
    raw = lm(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])[0]

    if not json_response:
        return raw.strip()

    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    try:
        return json.loads(cleaned)
    except Exception as e:
        logger.warning(f"[brief.llm] step={step} returned non-JSON ({e}); head={raw[:120]!r}")
        raise ValueError(f"brief step {step!r} produced invalid JSON: {e}") from e


async def call_llm(
    system: str,
    user: str,
    step: str,
    json_response: bool = True,
) -> dict | list | str:
    """
    One brief-pipeline LLM call (async — dspy runs in the default executor,
    same pattern as the ingest transformations). Raises on unknown step or
    (in json mode) unparseable output — callers own their retry policy,
    exactly as in the original harness.
    """
    import asyncio
    return await asyncio.get_running_loop().run_in_executor(
        None, _call_llm_sync, system, user, step, json_response
    )
