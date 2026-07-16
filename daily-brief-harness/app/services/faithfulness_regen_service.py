"""
Synchronous faithfulness regeneration loop for article segments (Step 6) —
see faithfulness-judge-plan.md section 5. Scoped to faithfulness_article
only: bookends are excluded on purpose (they're grounded in structured
inputs you control, so a recurring flag there means the intro/outro prompt
itself needs a manual fix, not an auto-patch; article segments are grounded
in the real world via web search, so some baseline flag rate is expected and
worth auto-remediating per occurrence).

Called inline from user_brief_runner._resolve_one, replacing what used to be
a single "generate, then judge later in the background" step with a
synchronous "generate -> judge -> regenerate (up to 2 retries) -> judge"
loop, so a flagged critical/moderate claim never reaches a brief marked
"ready". This is why faithfulness_article was removed from
eval_judges_service.AUTOMATED_JUDGE_SETS["generate_brief"] — it's no longer
a post-hoc background check for freshly-generated segments, it's inline.

Judging itself always runs synchronously on this path regardless of the
toggle below — settings_service.is_faithfulness_regen_enabled() governs only
whether a flag triggers a rewrite, not whether the judge call happens at
all. That keeps "faithfulness_article always gets logged for this run" true
independent of the toggle, matching the manual gold path's own behavior.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services import cache_service
from app.services import eval_judges_service
from app.services import eval_logging_service
from app.services import settings_service
from app.services.llm_service import JUDGE_MODEL
from app.services.pipeline import pipeline_manager, ArticleSegmentRequest

logger = logging.getLogger(__name__)

MAX_RETRIES = 2  # up to 2 regenerations beyond the first attempt (3 attempts total)
TRIGGER_SEVERITIES = {"critical", "moderate"}  # "unverifiable" never triggers a rewrite — see faithfulness-judge-plan.md section 2.4/5
_SEVERITY_RANK = {"critical": 3, "moderate": 2, "unverifiable": 1, "none": 0}


def _format_flagged_claims(claims: List[Dict[str, Any]]) -> str:
    if not claims:
        return "(no specific claims listed)"
    return "\n".join(f'- "{c.get("text", "")}" — {c.get("explanation", "")}' for c in claims)


async def generate_verified_segment(
    request: ArticleSegmentRequest, *, api_key: Optional[str], run_id: Optional[str],
    article_id: str, url: Optional[str], segment_type: str, is_local: bool,
    source_available: bool, published_date: Optional[str],
) -> Dict[str, Any]:
    """First-generation path (article_segment_cache miss). Always does at
    least one generate+judge pass; regenerates up to MAX_RETRIES more times
    only while severity is in TRIGGER_SEVERITIES and the toggle is on.
    Returns the winning attempt's seg_result dict — same shape as
    pipeline_manager.run_article_segment_step's return (text/word_count/
    latency_ms/simulated/model/input_tokens/output_tokens) — so callers need
    no changes beyond swapping which function they call. Also writes the
    winning verdict onto the shared cache row's memoized faithfulness
    columns, exactly like the pre-existing automated (post-hoc) path used to
    for a fresh segment, so a later cache-hit never re-judges this text."""
    regen_enabled = await settings_service.is_faithfulness_regen_enabled()

    attempts: List[Dict[str, Any]] = []
    seg_result = await pipeline_manager.run_article_segment_step(request, api_key=api_key)

    max_attempts = (1 + MAX_RETRIES) if regen_enabled else 1
    for attempt_number in range(1, max_attempts + 1):
        judge_result = await eval_judges_service.judge_faithfulness_article(
            seg_result["text"], request.title, request.full_text, source_available,
            published_date=published_date,
        )
        await eval_logging_service.log_judge_output(
            run_id, "faithfulness_article", url=url, segment_type=segment_type, is_local=is_local,
            verdict=judge_result["verdict"], severity=judge_result["severity"], detail=judge_result["detail"],
            judge_model=JUDGE_MODEL, attempt_number=attempt_number,
            input_tokens=judge_result.get("input_tokens"), output_tokens=judge_result.get("output_tokens"),
            latency_ms=judge_result.get("latency_ms"),
        )
        attempts.append({"seg_result": seg_result, "judge_result": judge_result})

        if judge_result["severity"] not in TRIGGER_SEVERITIES or attempt_number == max_attempts:
            break

        regen_request = request.model_copy(update={
            "prior_text": seg_result["text"],
            "prior_feedback": _format_flagged_claims(judge_result["detail"].get("claims", [])),
        })
        seg_result = await pipeline_manager.run_article_segment_step(regen_request, api_key=api_key)

    # Least-severe attempt wins; ties go to the latest attempt (the most
    # recent rewrite is the model's best attempt at that same severity, not
    # a demonstrated regression) — see faithfulness-judge-plan.md section 5.
    best = min(
        enumerate(attempts),
        key=lambda pair: (_SEVERITY_RANK.get(pair[1]["judge_result"]["severity"], 0), -pair[0]),
    )[1]

    if len(attempts) > 1:
        logger.info(
            f"[faithfulness_regen] run_id={run_id} segment_type={segment_type} "
            f"resolved after {len(attempts)} attempt(s), final severity={best['judge_result']['severity']}"
        )

    await cache_service.set_cached_segment_faithfulness(
        str(article_id), segment_type, is_local, best["judge_result"]["severity"], best["judge_result"]["detail"],
    )
    return best["seg_result"]


async def verify_cached_segment(
    *, run_id: Optional[str], article_id: str, url: Optional[str], segment_type: str, is_local: bool,
    text: str, title: str, source_text: str, source_available: bool, published_date: Optional[str],
) -> None:
    """Cache-hit path — no regeneration (the text is already cached/shared
    and possibly already served to other users; rewriting it here wouldn't
    even be visible to whoever already has this run's cached mp3/text). Just
    the same memoized judge-once-and-log behavior the old post-hoc automated
    pass used to provide for a cache hit, moved inline so every segment in a
    run (hit or miss) gets exactly one faithfulness_article log row up
    front, instead of hits being covered by a separate background pass."""
    cached_row = await cache_service.get_cached_segment(article_id, segment_type, is_local)
    if cached_row and cached_row.get("faithfulness_severity") is not None:
        severity = cached_row["faithfulness_severity"]
        detail = cached_row.get("faithfulness_detail") or {"claims": []}
        await eval_logging_service.log_judge_output(
            run_id, "faithfulness_article", url=url, segment_type=segment_type, is_local=is_local,
            verdict="clean" if severity == "none" else "flagged", severity=severity, detail=detail,
            judge_model=JUDGE_MODEL, attempt_number=1,
        )
        return

    judge_result = await eval_judges_service.judge_faithfulness_article(
        text, title, source_text, source_available, published_date=published_date,
    )
    await cache_service.set_cached_segment_faithfulness(
        str(article_id), segment_type, is_local, judge_result["severity"], judge_result["detail"],
    )
    await eval_logging_service.log_judge_output(
        run_id, "faithfulness_article", url=url, segment_type=segment_type, is_local=is_local,
        verdict=judge_result["verdict"], severity=judge_result["severity"], detail=judge_result["detail"],
        judge_model=JUDGE_MODEL, attempt_number=1,
        input_tokens=judge_result.get("input_tokens"), output_tokens=judge_result.get("output_tokens"),
        latency_ms=judge_result.get("latency_ms"),
    )
