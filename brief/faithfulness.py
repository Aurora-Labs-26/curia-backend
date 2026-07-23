"""
brief/faithfulness.py
Inline faithfulness gate for article segments — the un-parked, slimmed
version of the harness's faithfulness_regen_service.

Loop (per cache-miss segment): generate → judge → if flagged
(critical/moderate), regenerate ONCE with the judge's flagged claims threaded
into the prompt (ArticleSegmentRequest.prior_text/prior_feedback) → judge
again → ship attempt 2 regardless of its verdict. Two generation attempts
max; the brief is never blocked on a stubborn flag.

Differences from the harness original, on purpose:
  - source-comparison judging only (the harness's article variant judged via
    a web-search tool — not available inline here, and the source text is
    what the segment was written from anyway)
  - 2 attempts total, not 3; the final attempt always ships
  - judge failure fails OPEN ("unknown" severity, no retry) — a judge outage
    must never take down brief generation

The winning verdict is attached to the returned seg_result under
"faithfulness"; callers memoize it onto the cache row AFTER
put_cached_segment (which nulls the memo columns) via
store.set_cached_segment_faithfulness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from brief.llm import call_llm

MAX_ATTEMPTS = 2
TRIGGER_SEVERITIES = {"critical", "moderate"}
_SEVERITY_RANK = {"critical": 3, "moderate": 2, "stylistic": 1, "none": 0}

SYSTEM_FAITHFULNESS_JUDGE_PROMPT = """You are a rigorous fact-checker. You will be given a spoken news-segment script and the source article text it was written from. Check every factual claim in the segment against the source, using exactly three severity levels:

1. "critical" — a fabricated fact, entity, number, date, or quote that is not present in, or is directly contradicted by, the source. This includes invented statistics, wrong dates, misattributed quotes, or people/organizations that don't appear in the source.
2. "moderate" — an unsupported inference or editorializing claim that goes beyond what the source actually says, but isn't a fabrication (a reasonable-sounding extrapolation, a claim of significance the source doesn't make, unwarranted causal language).
3. "stylistic" — conversational framing, connective tissue, or paraphrase that carries no independent factual claim. Never list these.

If the source text is marked as UNAVAILABLE, the segment was written from only the headline — then flag as "critical" any specific detail (number, quote, named entity, date) that could not have come from the headline alone.

Respond with ONLY a JSON object:
{"claims": [{"text": "<the claim>", "severity": "critical|moderate", "explanation": "<why>"}]}

Only list "critical" or "moderate" claims. If there are none, return {"claims": []}."""

USER_FAITHFULNESS_JUDGE_PROMPT = """SEGMENT SCRIPT:
{segment_text}

ARTICLE TITLE: {title}

SOURCE TEXT{unavailable_note}:
{source_text}"""


def _worst_severity(claims: List[Dict[str, Any]]) -> str:
    worst = "none"
    for c in claims:
        sev = str(c.get("severity", "")).lower()
        if _SEVERITY_RANK.get(sev, 0) > _SEVERITY_RANK.get(worst, 0):
            worst = sev
    return worst


def _format_flagged_claims(claims: List[Dict[str, Any]]) -> str:
    if not claims:
        return "(no specific claims listed)"
    return "\n".join(f'- "{c.get("text", "")}" — {c.get("explanation", "")}' for c in claims)


async def judge_segment(
    text: str, title: str, source_text: str, source_available: bool = True,
) -> Dict[str, Any]:
    """One judge call → {"severity": none|moderate|critical|unknown, "claims": [...]}.
    Any failure (LLM error, unparseable output) returns severity "unknown" —
    treated as non-flagged by the caller (fail-open)."""
    try:
        user = USER_FAITHFULNESS_JUDGE_PROMPT.format(
            segment_text=text,
            title=title,
            unavailable_note="" if source_available and source_text.strip() else " (UNAVAILABLE — headline-only generation)",
            source_text=source_text.strip() or "(no source text)",
        )
        parsed = await call_llm(SYSTEM_FAITHFULNESS_JUDGE_PROMPT, user, step="judge")
        claims = parsed.get("claims") or []
        if not isinstance(claims, list):
            claims = []
        return {"severity": _worst_severity(claims), "claims": claims}
    except Exception as e:
        logger.warning(f"[brief.faithfulness] judge failed open: {e}")
        return {"severity": "unknown", "claims": []}


async def generate_verified_segment(
    req, *, api_key: Optional[str] = None, run_id: Optional[str] = None,
    article_id: Optional[str] = None, url: Optional[str] = None,
    segment_type: Optional[str] = None, is_local: Optional[bool] = None,
    source_available: bool = True, published_date: Optional[str] = None, **kw,
) -> Dict[str, Any]:
    """Generate → judge → (one feedback retry) → ship. Same return shape as
    pipeline_manager.run_article_segment_step, plus a "faithfulness" key with
    the shipped attempt's verdict."""
    from brief.pipeline import pipeline_manager

    seg_result = await pipeline_manager.run_article_segment_step(req, api_key=api_key)
    verdict = await judge_segment(
        seg_result["text"], req.title, req.full_text, source_available=req.content_fetched
    )

    if verdict["severity"] in TRIGGER_SEVERITIES:
        logger.info(
            f"[brief.faithfulness] {segment_type or req.segment_type} flagged "
            f"{verdict['severity']} ({len(verdict['claims'])} claims) — regenerating once"
        )
        retry_req = req.model_copy(update={
            "prior_text": seg_result["text"],
            "prior_feedback": _format_flagged_claims(verdict["claims"]),
        })
        seg_result = await pipeline_manager.run_article_segment_step(retry_req, api_key=api_key)
        verdict = await judge_segment(
            seg_result["text"], req.title, req.full_text, source_available=req.content_fetched
        )
        if verdict["severity"] in TRIGGER_SEVERITIES:
            # Second attempt still flagged — ship it anyway (explicit design:
            # the brief never blocks), verdict memoized so the cache row
            # carries the honest severity.
            logger.warning(
                f"[brief.faithfulness] {segment_type or req.segment_type} still "
                f"{verdict['severity']} after retry — shipping attempt 2"
            )

    return {**seg_result, "faithfulness": verdict}


async def verify_cached_segment(*args, **kwargs) -> None:
    """Cache-hit path stays un-judged inline (the text may already have been
    served to other users; its verdict was memoized at generation time)."""
    return None
