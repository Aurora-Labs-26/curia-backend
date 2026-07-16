"""
M3 of daily-brief-eval-buildplan.md: LLM-as-a-judge modules, validated
against M2's gold data (harness.eval_gold_labels / eval_gold_scripts) before
being trusted. See db/007_eval_judge_schema.sql for the storage shape.

Two orchestrators share the judge helpers below:
- `run_judges_for_gold_run` — the ORIGINAL, unchanged-in-behavior manual
  path. Only ever runs from an explicit dashboard action ("Run Judges")
  against an already-frozen gold run, never from the live pipeline, so a
  failure should surface as a real error, not degrade silently (functions
  here do NOT swallow exceptions — see eval_logging_service.log_judge_output's
  own docstring for the same reasoning). Runs every judge check every time,
  computed fresh (no memoization) — this is the calibration workflow.
- `run_automated_judges` — NEW (see automated_judging_build_plan.md), fires
  automatically after every live Generate Brief call, on a smaller judge
  set gated by run kind (AUTOMATED_JUDGE_SETS below). Triggered from a
  separate module (app/services/automated_judging.py), which owns its own
  error containment and the on/off toggle — this module doesn't know
  anything about either. faithfulness_article is NOT part of this
  orchestrator (see AUTOMATED_JUDGE_SETS' own comment) — it runs
  synchronously and inline instead, from user_brief_runner._resolve_one via
  faithfulness_regen_service.py, memoized against the shared
  article_segment_cache the same way, just moved earlier so a flagged
  segment can be regenerated before the brief is marked ready (see
  faithfulness-judge-plan.md section 5).

Six concrete judge calls implement the buildplan's three named judges
(the fourth, "Overview-fidelity judge", was removed 2026-07-14 — it
duplicated faithfulness_bookend(intro) against real output and mis-flagged
accurate weather restatements, see static/app.js's former explanatory
comment and progress.md):
- "Relevance/ranking judge"    -> judge_relevance + judge_order_correctness
                                  (+ check_article_diversity, deterministic)
- "Faithfulness judge"         -> judge_faithfulness_article + judge_faithfulness_bookend
- "Tone/flow pairwise judge"   -> judge_tone_flow_pairwise + judge_brief_coherence

Only relevance and order-correctness/order-ranking have real M2 gold data to
compute a formal Cohen's kappa against (compute_relevance_kappa /
compute_order_correctness_agreement / compute_order_ranking_agreement, all
below). The rest have no dedicated gold labels (M2 never built a
hallucination/tone gold set) — per the buildplan's own wording, those are
surfaced for a human spot-check in the Gold Set "Judges" sub-tab instead of
a computed metric.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, List, Optional

from app.services.llm_service import llm_service, JUDGE_MODEL
from app.services import eval_logging_service
from app.services import eval_gold_service
from app.services import cache_service
from app.services import scoring_service
from app.services import harness_db
from app.templates import prompts

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Relevance + order-correctness (buildplan's "Relevance/ranking judge")
# ---------------------------------------------------------------------------


async def judge_relevance(pool: List[Dict[str, Any]], interests: List[str], location: str) -> Dict[str, Any]:
    """One LLM call per pool (non-local or local). `pool` items need
    "url"/"title"/"description". Returns {"results": [...], "cost": {...}} —
    `results` is one {"url","label","reason"} dict per article, resolved
    back from the model's id-tagged response; `cost` is the one call's
    input_tokens/output_tokens/latency_ms (see db/013_judge_cost_logging.sql
    — added so real judge spend is knowable before automated judging runs
    unconditionally on every brief)."""
    if not pool:
        return {"results": [], "cost": {}}
    tagged = [{"id": i, "title": a["title"], "description": a.get("description", "")} for i, a in enumerate(pool)]
    result = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_RELEVANCE_JUDGE_PROMPT,
        user_content=prompts.USER_RELEVANCE_JUDGE_PROMPT.format(
            interests=", ".join(interests) or "(none stated)",
            location=location or "(none stated)",
            articles=json.dumps(tagged, indent=2),
        ),
        step_id=8,
    )
    parsed = result.get("parsed")
    labels = parsed.get("labels", []) if isinstance(parsed, dict) else []
    out = []
    for item in labels:
        idx = item.get("id")
        if not isinstance(idx, int) or not (0 <= idx < len(pool)):
            continue
        out.append({"url": pool[idx]["url"], "label": item.get("label", "miss"), "reason": item.get("reason", "")})
    cost = {
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
    }
    return {"results": out, "cost": cost}


async def judge_order_correctness(pool: List[Dict[str, Any]], chosen: Dict[str, Any], window_size: int) -> Dict[str, Any]:
    """Given the same pool plus whichever article the pipeline placed in the
    Lead/Local slot, judge two things in one call: (1) whether that's
    genuinely the most significant story in the whole pool, and (2) whether
    ranks 2 through `window_size` (the same window eval_gold_service caps
    relevance labeling at) are placed in defensible relative order. The pool
    array is already rank-ordered and id-tagged, so id 0..window_size-1
    already *is* ranks 1..window_size — no extra data to thread in."""
    tagged = [{"id": i, "title": a["title"], "description": a.get("description", "")} for i, a in enumerate(pool)]
    chosen_id = next((i for i, a in enumerate(pool) if a["url"] == chosen["url"]), 0)
    result = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_ORDER_CORRECTNESS_JUDGE_PROMPT,
        user_content=prompts.USER_ORDER_CORRECTNESS_JUDGE_PROMPT.format(
            articles=json.dumps(tagged, indent=2),
            chosen_id=chosen_id,
            chosen_title=chosen["title"],
            window_size=window_size,
            window_size_minus_one=window_size - 1,
        ),
        step_id=9,
    )
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {"agree": True, "alternative_id": None, "reasoning": "", "order_ranking_agree": True, "order_ranking_reasoning": ""}
    alt_id = parsed.get("alternative_id")
    alt_url = pool[alt_id]["url"] if isinstance(alt_id, int) and 0 <= alt_id < len(pool) else None
    return {
        "agree": bool(parsed.get("agree", True)),
        "alternative_url": alt_url,
        "reasoning": parsed.get("reasoning", ""),
        "order_ranking_agree": bool(parsed.get("order_ranking_agree", True)),
        "order_ranking_reasoning": parsed.get("order_ranking_reasoning", ""),
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
    }


def check_article_diversity(top4_articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Deterministic — no LLM call. Embedding cosine similarity between the
    top-4 non-local articles, reusing the same model/threshold precedent as
    scoring_service's own dedup clustering (all-MiniLM-L6-v2,
    CLUSTER_SIM_THRESHOLD=0.72). Despite the buildplan grouping this under
    "relevance/ranking judge", it costs no API call, so per the buildplan's
    deterministic-first principle it's logged to eval_results
    (check_type='deterministic'), not eval_judge_outputs. Manual gold-run
    path only — not part of AUTOMATED_JUDGE_SETS (out of scope for the
    per-brief automated pass, see automated_judging_build_plan.md)."""
    if len(top4_articles) < 2:
        return {"check_name": "diversity_top4_similarity", "check_type": "deterministic", "result_status": "pass"}

    from sklearn.metrics.pairwise import cosine_similarity

    model = scoring_service._get_model()
    texts = [f"{a['title']} {a.get('description', '')}".strip() for a in top4_articles]
    embeddings = model.encode(texts)
    sim_matrix = cosine_similarity(embeddings)

    max_sim = -1.0
    max_pair = ("", "")
    n = len(top4_articles)
    for i in range(n):
        for j in range(i + 1, n):
            if sim_matrix[i][j] > max_sim:
                max_sim = float(sim_matrix[i][j])
                max_pair = (top4_articles[i]["title"], top4_articles[j]["title"])

    flagged = max_sim > scoring_service.CLUSTER_SIM_THRESHOLD
    return {
        "check_name": "diversity_top4_similarity",
        "check_type": "deterministic",
        "result_status": "flag" if flagged else "pass",
        "result_score": max_sim,
        "detail": (
            f"max pairwise similarity {max_sim:.2f} between '{max_pair[0]}' and '{max_pair[1]}' "
            f"(threshold {scoring_service.CLUSTER_SIM_THRESHOLD})"
            if flagged else None
        ),
    }


# ---------------------------------------------------------------------------
# Faithfulness (article-sourced + bookend-sourced) — both share the same
# {"claims": [...], "worst_severity": ...} output contract (see
# faithfulness_judge.py).
# ---------------------------------------------------------------------------


def _parse_severity_result(result: Dict[str, Any]) -> Dict[str, Any]:
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {"claims": [], "worst_severity": "none"}
    worst = parsed.get("worst_severity", "none")
    claims = parsed.get("claims", [])
    detail: Dict[str, Any] = {"claims": claims}
    if "counts" in parsed:
        # Only the redesigned faithfulness_article judge emits this
        # (faithfulness_bookend shares this same parser but doesn't have a
        # "counts" key in its parsed JSON, so this is a no-op for it) — see
        # faithfulness-judge-plan.md section 2.5.
        detail["counts"] = parsed["counts"]
    return {
        "verdict": "clean" if worst == "none" else "flagged",
        "severity": worst,
        "detail": detail,
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
    }


def _parse_severity_side(side: Any) -> Dict[str, Any]:
    """Same claims/worst_severity -> verdict/severity/detail mapping as
    _parse_severity_result, but for one side of a nested multi-target
    response — currently only judge_faithfulness_bookend's combined
    intro+outro call (see its docstring). No cost fields here: the caller
    attaches call-level cost to exactly one of the two sides itself, same
    don't-double-count convention as _judge_relevance_for_pool/
    _judge_order_for_pool."""
    if not isinstance(side, dict):
        side = {"claims": [], "worst_severity": "none"}
    worst = side.get("worst_severity", "none")
    claims = side.get("claims", [])
    return {
        "verdict": "clean" if worst == "none" else "flagged",
        "severity": worst,
        "detail": {"claims": claims},
    }


async def judge_faithfulness_article(
    segment_text: str, title: str, source_text: str, source_available: bool = True,
    published_date: Optional[str] = None,
) -> Dict[str, Any]:
    """`source_available` mirrors eval_fetched_articles.content_fetched — when
    False, source_text is really just a headline/description, not a real
    article body. The judge's own severity call doesn't change (a "critical"
    flag against a headline-only source is still worth surfacing), but this
    tags the result so the report can distinguish "fabricated against a real
    source" from "unavoidably vague source, generator over-specified anyway"
    — see progress.md's Judging #2. Overlaps with Generation #2 (threading
    content_fetched into the segment prompt itself) — that fix should reduce
    how often this case even arises.

    `published_date` is eval_fetched_articles.published_date — raw RSS text,
    format varies by source and is sometimes NULL. Passed through as-is (see
    faithfulness-judge-plan.md); the prompt itself is instructed not to treat
    a missing/unparseable date as license to default every unsure claim to
    "unverifiable"."""
    result = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_FAITHFULNESS_ARTICLE_JUDGE_PROMPT,
        user_content=prompts.USER_FAITHFULNESS_ARTICLE_JUDGE_PROMPT.format(
            title=title or "(untitled)",
            source_text=source_text or "(no source text captured)",
            segment_text=segment_text,
            published_date=published_date or "(unknown)",
        ),
        step_id=10,
    )
    parsed_result = _parse_severity_result(result)
    parsed_result["detail"]["source_available"] = source_available
    return parsed_result


async def judge_faithfulness_bookend(intro_text: str, outro_text: str, inputs_used: Dict[str, Any]) -> Dict[str, Any]:
    """One combined LLM call judging both intro and outro against the same
    structured inputs (2026-07-14 — was two separate calls, one per
    bookend; see faithfulness_judge.py's module comment). Safe to always
    combine: intro/outro come from the same eval_meta_segments row, always
    generated together from the same inputs_used, so there's never a case
    where one exists without the other.

    Returns {"intro": {...}, "outro": {...}, "cost": {...}} — each side has
    the same verdict/severity/detail shape _parse_severity_result produces,
    and "cost" (input_tokens/output_tokens/latency_ms) is for the one call,
    attributed by the caller to a single logged row (same don't-double-count
    convention as relevance/order_correctness)."""
    # eval_meta_segments.inputs_used stores the day's story picks under
    # "ranked_order" (see user_brief_runner.py's log_meta_segments call),
    # not "selections" (that name is IntroOutroRequest's own field at the
    # pipeline-call level, not the persisted logging key).
    selections = inputs_used.get("ranked_order", []) or []
    selections_str = "; ".join(f"{s.get('title', '')} ({s.get('reason', '')})" for s in selections) or "(none)"
    result = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_FAITHFULNESS_BOOKEND_JUDGE_PROMPT,
        user_content=prompts.USER_FAITHFULNESS_BOOKEND_JUDGE_PROMPT.format(
            display_name=inputs_used.get("display_name", ""),
            location_name=inputs_used.get("location_name", ""),
            local_time=inputs_used.get("local_time", ""),
            weather=inputs_used.get("weather", ""),
            selections=selections_str,
            intro_text=intro_text,
            outro_text=outro_text,
        ),
        step_id=11,
    )
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {}
    return {
        "intro": _parse_severity_side(parsed.get("intro")),
        "outro": _parse_severity_side(parsed.get("outro")),
        "cost": {
            "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"),
            "latency_ms": result.get("latency_ms"),
        },
    }


# ---------------------------------------------------------------------------
# Tone/flow pairwise + whole-brief coherence
# ---------------------------------------------------------------------------


async def judge_tone_flow_pairwise(generated_text: str, golden_text: str) -> Dict[str, Any]:
    """Calls the pairwise judge twice with the two scripts swapped
    (position-bias control per the buildplan) and reconciles in Python: if
    the winner flips depending on slot, the verdict is "inconclusive". Cost
    fields are the SUM of both calls (both are real spend for one reconciled
    verdict, not two independent judge outputs)."""
    call_ab = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_TONE_FLOW_PAIRWISE_JUDGE_PROMPT,
        user_content=prompts.USER_TONE_FLOW_PAIRWISE_JUDGE_PROMPT.format(script_a=generated_text, script_b=golden_text),
        step_id=12,
    )
    call_ba = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_TONE_FLOW_PAIRWISE_JUDGE_PROMPT,
        user_content=prompts.USER_TONE_FLOW_PAIRWISE_JUDGE_PROMPT.format(script_a=golden_text, script_b=generated_text),
        step_id=12,
    )
    parsed_ab = call_ab.get("parsed") if isinstance(call_ab.get("parsed"), dict) else {}
    parsed_ba = call_ba.get("parsed") if isinstance(call_ba.get("parsed"), dict) else {}

    identity_ab = {"A": "generated", "B": "golden", "tie": "tie"}
    identity_ba = {"A": "golden", "B": "generated", "tie": "tie"}
    verdict_ab = identity_ab.get(parsed_ab.get("winner"), "tie")
    verdict_ba = identity_ba.get(parsed_ba.get("winner"), "tie")
    reconciled = verdict_ab if verdict_ab == verdict_ba else "inconclusive"

    def _sum_opt(a: Optional[int], b: Optional[int]) -> Optional[int]:
        if a is None and b is None:
            return None
        return (a or 0) + (b or 0)

    return {
        "verdict": reconciled,
        "detail": {
            "call_generated_first": {"winner": parsed_ab.get("winner"), "reasoning": parsed_ab.get("reasoning", "")},
            "call_golden_first": {"winner": parsed_ba.get("winner"), "reasoning": parsed_ba.get("reasoning", "")},
        },
        "input_tokens": _sum_opt(call_ab.get("input_tokens"), call_ba.get("input_tokens")),
        "output_tokens": _sum_opt(call_ab.get("output_tokens"), call_ba.get("output_tokens")),
        "latency_ms": _sum_opt(call_ab.get("latency_ms"), call_ba.get("latency_ms")),
    }


async def judge_brief_coherence(stitched_text: str) -> Dict[str, Any]:
    result = await llm_service.call_llm(
        system_prompt=prompts.SYSTEM_BRIEF_COHERENCE_JUDGE_PROMPT,
        user_content=prompts.USER_BRIEF_COHERENCE_JUDGE_PROMPT.format(stitched_text=stitched_text),
        step_id=13,
    )
    parsed = result.get("parsed")
    if not isinstance(parsed, dict):
        parsed = {"coherence_score": None, "weakest_transition": None, "reasoning": ""}
    return {
        "verdict": str(parsed.get("coherence_score", "")),
        "detail": {"weakest_transition": parsed.get("weakest_transition"), "reasoning": parsed.get("reasoning", "")},
        "input_tokens": result.get("input_tokens"),
        "output_tokens": result.get("output_tokens"),
        "latency_ms": result.get("latency_ms"),
    }


async def list_judge_outputs(run_id: str) -> List[Dict[str, Any]]:
    """Raw eval_judge_outputs rows for one run, with each row's article title
    AND rank resolved via eval_llm_ranking_output. Both are included
    directly rather than left for the dashboard to cross-reference against
    the gold labeling view's candidate list, because that view caps
    candidates at DEFAULT_NON_LOCAL_CANDIDATES/DEFAULT_LOCAL_CANDIDATES for
    display — judges run against the FULL uncapped pool, so plenty of judged
    urls (e.g. rank 5+ of an 18-candidate local pool) wouldn't resolve a
    title or a broadcast position that way.

    Works for ANY run_id, gold or not — no eval_gold_runs join here, which
    is what makes this directly reusable for the new automated-judging
    "Judge Output" tab (see automated_judging_build_plan.md Milestone 6)
    without any change to this function.

    Not ordered by created_at: the model's own JSON response order (which
    row gets inserted first) has nothing to do with broadcast rank, so
    "most recent first" was actually close to arbitrary. Returned in
    (judge_name, is_local, rank) order instead — real rank order within
    each pool — and the dashboard groups/re-sorts by segment_type on top of
    that for the spot-check table (see renderGoldSetJudgesView)."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT jo.judge_name, jo.url, lro.title, lro.rank, jo.segment_type, jo.is_local,
               jo.verdict, jo.severity, jo.reasoning, jo.detail, jo.judge_model, jo.created_at,
               jo.input_tokens, jo.output_tokens, jo.latency_ms, jo.attempt_number
        FROM harness.eval_judge_outputs jo
        LEFT JOIN harness.eval_llm_ranking_output lro ON lro.run_id = jo.run_id AND lro.url = jo.url
        WHERE jo.run_id = $1
        ORDER BY jo.judge_name, jo.is_local NULLS FIRST, lro.rank NULLS FIRST, jo.attempt_number
        """,
        run_id,
    )
    out = []
    for r in rows:
        d = dict(r)
        if d.get("detail"):
            d["detail"] = json.loads(d["detail"])
        d["created_at"] = d["created_at"].isoformat()
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# Shared per-concern helpers — both run_judges_for_gold_run (manual, full
# 8-judge suite, always fresh) and run_automated_judges (background, a
# smaller judge set, faithfulness_article memoized) call these. Splitting
# them out is what lets the automated path call only a subset without
# duplicating logic or risking the two paths drifting apart (see
# automated_judging_build_plan.md Milestone 2 — the manual button's own
# behavior must stay byte-for-byte identical to before this refactor).
# ---------------------------------------------------------------------------


def _pool_item(r: Dict[str, Any], articles_by_url: Dict[str, Any]) -> Dict[str, Any]:
    return {"url": r["url"], "title": r["title"], "description": articles_by_url.get(r["url"], {}).get("description", "")}


async def _judge_relevance_for_pool(
    run_id: str, pool: List[Dict[str, Any]], interests: List[str], location: str, is_local: bool,
    outputs: List[Dict[str, Any]],
) -> None:
    relevance = await judge_relevance(pool, interests, location)
    results, cost = relevance["results"], relevance["cost"]
    for i, item in enumerate(results):
        # Cost is call-level (one call scores every article in the pool), not
        # per-article — attributed to the first logged row only so summing
        # eval_judge_outputs.input_tokens per run doesn't double-count.
        row_cost = cost if i == 0 else {}
        await eval_logging_service.log_judge_output(
            run_id, "relevance", url=item["url"], is_local=is_local,
            verdict=item["label"], reasoning=item["reason"], judge_model=JUDGE_MODEL,
            **row_cost,
        )
    outputs.append({"judge_name": "relevance", "is_local": is_local, "n": len(results)})


async def _judge_order_for_pool(
    run_id: str, pool: List[Dict[str, Any]], is_local: bool, outputs: List[Dict[str, Any]],
) -> None:
    if not pool:
        return
    window_size = eval_gold_service.DEFAULT_LOCAL_CANDIDATES if is_local else eval_gold_service.DEFAULT_NON_LOCAL_CANDIDATES
    order_result = await judge_order_correctness(pool, pool[0], window_size)
    # Both order_correctness and order_ranking rows come from this one call —
    # cost attributed to the order_correctness row only, same
    # don't-double-count reasoning as relevance above.
    await eval_logging_service.log_judge_output(
        run_id, "order_correctness", is_local=is_local,
        verdict="agree" if order_result["agree"] else "disagree",
        reasoning=order_result["reasoning"],
        detail={"alternative_url": order_result.get("alternative_url")},
        judge_model=JUDGE_MODEL,
        input_tokens=order_result.get("input_tokens"),
        output_tokens=order_result.get("output_tokens"),
        latency_ms=order_result.get("latency_ms"),
    )
    outputs.append({"judge_name": "order_correctness", "is_local": is_local, "verdict": order_result["agree"]})

    await eval_logging_service.log_judge_output(
        run_id, "order_ranking", is_local=is_local,
        verdict="agree" if order_result["order_ranking_agree"] else "disagree",
        reasoning=order_result["order_ranking_reasoning"],
        judge_model=JUDGE_MODEL,
    )
    outputs.append({"judge_name": "order_ranking", "is_local": is_local, "verdict": order_result["order_ranking_agree"]})


async def _judge_diversity(run_id: str, llm_ranking: List[Dict[str, Any]], articles_by_url: Dict[str, Any], outputs: List[Dict[str, Any]]) -> None:
    top4 = [_pool_item(r, articles_by_url) for r in llm_ranking if not r["is_local"] and r["rank"] <= 4]
    diversity_result = check_article_diversity(top4)
    await eval_logging_service.log_eval_result(
        run_id, diversity_result["check_name"], diversity_result["check_type"],
        result_status=diversity_result.get("result_status"),
        result_score=diversity_result.get("result_score"),
        detail=diversity_result.get("detail"),
    )
    outputs.append({"judge_name": "diversity_top4_similarity", "result_status": diversity_result.get("result_status")})


async def _judge_faithfulness_article_segments(
    run_id: str, segments: List[Dict[str, Any]], articles_by_url: Dict[str, Any],
    outputs: List[Dict[str, Any]], *, use_memoization: bool,
) -> None:
    """use_memoization=True (automated path only) checks
    harness.article_segment_cache for an existing verdict on this exact
    (article_id, segment_type, is_local) before calling the LLM, and writes
    a fresh one back after computing it — see cache_service.
    get_cached_segment/set_cached_segment_faithfulness and
    db/014_faithfulness_memoization.sql. The manual gold path always
    computes fresh (use_memoization=False), unchanged from before this
    refactor."""
    for seg in segments:
        source = articles_by_url.get(seg["url"], {}) if seg["url"] else {}
        source_text = source.get("full_text") or source.get("description") or ""
        source_available = bool(source.get("content_fetched", True))

        cached_verdict = None
        article_id = seg.get("article_id")
        is_local_seg = seg["segment_type"] == "local"
        if use_memoization and article_id:
            cached_row = await cache_service.get_cached_segment(str(article_id), seg["segment_type"], is_local_seg)
            if cached_row and cached_row.get("faithfulness_severity") is not None:
                cached_verdict = {
                    "verdict": "clean" if cached_row["faithfulness_severity"] == "none" else "flagged",
                    "severity": cached_row["faithfulness_severity"],
                    "detail": cached_row.get("faithfulness_detail") or {"claims": []},
                }

        if cached_verdict is not None:
            result = cached_verdict
            cost_kwargs: Dict[str, Any] = {}
        else:
            result = await judge_faithfulness_article(
                seg["text"], seg.get("title") or "", source_text, source_available,
                published_date=source.get("published_date"),
            )
            cost_kwargs = {
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
                "latency_ms": result.get("latency_ms"),
            }
            if use_memoization and article_id:
                await cache_service.set_cached_segment_faithfulness(
                    str(article_id), seg["segment_type"], is_local_seg, result["severity"], result["detail"],
                )

        await eval_logging_service.log_judge_output(
            run_id, "faithfulness_article", url=seg["url"], segment_type=seg["segment_type"],
            verdict=result["verdict"], severity=result["severity"], detail=result["detail"], judge_model=JUDGE_MODEL,
            **cost_kwargs,
        )
        outputs.append({"judge_name": "faithfulness_article", "segment_type": seg["segment_type"], "verdict": result["verdict"]})


async def _judge_tone_flow_segments(
    run_id: str, segments: List[Dict[str, Any]], golden_scripts: Dict[Any, str], outputs: List[Dict[str, Any]],
) -> None:
    """Manual gold path only — see AUTOMATED_JUDGE_SETS (tone_flow_pairwise
    isn't in any automated set, since it structurally requires a
    hand-authored golden script matching that exact article, which fresh
    daily news won't have)."""
    for seg in segments:
        golden = golden_scripts.get((seg["segment_type"], seg["url"]))
        if not golden:
            continue
        tf_result = await judge_tone_flow_pairwise(seg["text"], golden)
        await eval_logging_service.log_judge_output(
            run_id, "tone_flow_pairwise", url=seg["url"], segment_type=seg["segment_type"],
            verdict=tf_result["verdict"], detail=tf_result["detail"], judge_model=JUDGE_MODEL,
            input_tokens=tf_result.get("input_tokens"), output_tokens=tf_result.get("output_tokens"),
            latency_ms=tf_result.get("latency_ms"),
        )
        outputs.append({"judge_name": "tone_flow_pairwise", "segment_type": seg["segment_type"], "verdict": tf_result["verdict"]})


async def _judge_bookend_faithfulness(run_id: str, meta: Dict[str, Any], outputs: List[Dict[str, Any]]) -> None:
    """Both paths — the only automated check on a same-day regenerate_bookends
    run (see AUTOMATED_JUDGE_SETS), since that's the only thing genuinely
    fresh on that path (the 5 articles/segments are reused untouched).

    One combined LLM call judges intro and outro together (2026-07-14) —
    still logs two eval_judge_outputs rows (segment_type=intro/outro), same
    as before, so every downstream reader is unaffected. Cost attributed to
    the intro row only, don't-double-count convention as elsewhere in this
    file."""
    inputs_used = meta["inputs_used"] or {}
    bookend_result = await judge_faithfulness_bookend(meta["intro_text"], meta["outro_text"], inputs_used)
    for kind in ("intro", "outro"):
        result = bookend_result[kind]
        cost_kwargs = bookend_result["cost"] if kind == "intro" else {}
        await eval_logging_service.log_judge_output(
            run_id, "faithfulness_bookend", segment_type=kind,
            verdict=result["verdict"], severity=result["severity"], detail=result["detail"], judge_model=JUDGE_MODEL,
            **cost_kwargs,
        )
        outputs.append({"judge_name": "faithfulness_bookend", "segment_type": kind, "verdict": result["verdict"]})


async def _judge_bookend_tone_flow(run_id: str, meta: Dict[str, Any], golden_scripts: Dict[Any, str], outputs: List[Dict[str, Any]]) -> None:
    """Manual gold path only, same reasoning as _judge_tone_flow_segments."""
    for kind, text in (("intro", meta["intro_text"]), ("outro", meta["outro_text"])):
        golden = golden_scripts.get((kind, None))
        if not golden:
            continue
        tf_result = await judge_tone_flow_pairwise(text, golden)
        await eval_logging_service.log_judge_output(
            run_id, "tone_flow_pairwise", segment_type=kind,
            verdict=tf_result["verdict"], detail=tf_result["detail"], judge_model=JUDGE_MODEL,
            input_tokens=tf_result.get("input_tokens"), output_tokens=tf_result.get("output_tokens"),
            latency_ms=tf_result.get("latency_ms"),
        )
        outputs.append({"judge_name": "tone_flow_pairwise", "segment_type": kind, "verdict": tf_result["verdict"]})


async def _judge_coherence(run_id: str, meta: Dict[str, Any], segments: List[Dict[str, Any]], outputs: List[Dict[str, Any]]) -> None:
    """Manual gold path only."""
    stitched = "\n\n".join([meta["intro_text"]] + [s["text"] for s in segments] + [meta["outro_text"]])
    bc_result = await judge_brief_coherence(stitched)
    await eval_logging_service.log_judge_output(
        run_id, "brief_coherence", verdict=bc_result["verdict"], detail=bc_result["detail"], judge_model=JUDGE_MODEL,
        input_tokens=bc_result.get("input_tokens"), output_tokens=bc_result.get("output_tokens"),
        latency_ms=bc_result.get("latency_ms"),
    )
    outputs.append({"judge_name": "brief_coherence", "verdict": bc_result["verdict"]})


# ---------------------------------------------------------------------------
# Orchestrator #1 — manual, runs every judge above against one frozen gold
# run. Behavior is unchanged from before the Milestone 2 refactor: same
# checks, same order, same fresh-every-time (no memoization) semantics
# (overview_fidelity removed 2026-07-14 — see the module docstring above).
# ---------------------------------------------------------------------------


async def run_judges_for_gold_run(run_id: str) -> Dict[str, Any]:
    run = await eval_logging_service.get_run_for_judges(run_id)
    if run is None:
        raise ValueError(f"No eval run data found for run_id={run_id}")

    # Wipe any prior run's judge outputs first — see
    # delete_judge_outputs_for_run's docstring for why this must happen
    # before writing fresh ones rather than accumulating across re-runs.
    await eval_logging_service.delete_judge_outputs_for_run(run_id)

    user = run["user"] or {}
    interests = list(user.get("interests") or []) + list(user.get("custom_topics") or [])
    location = user.get("location_name") or ""

    articles_by_url = {a["url"]: a for a in run["fetched_articles"]}
    llm_ranking = run["llm_ranking"]
    golden_scripts = await eval_gold_service.get_active_scripts(run_id)

    outputs: List[Dict[str, Any]] = []

    for is_local in (False, True):
        pool_rows = [r for r in llm_ranking if r["is_local"] == is_local]
        if not pool_rows:
            continue
        pool = [_pool_item(r, articles_by_url) for r in pool_rows]
        await _judge_relevance_for_pool(run_id, pool, interests, location, is_local, outputs)
        await _judge_order_for_pool(run_id, pool, is_local, outputs)

    await _judge_diversity(run_id, llm_ranking, articles_by_url, outputs)
    await _judge_faithfulness_article_segments(run_id, run["segments"], articles_by_url, outputs, use_memoization=False)
    await _judge_tone_flow_segments(run_id, run["segments"], golden_scripts, outputs)

    if run["meta"]:
        await _judge_bookend_faithfulness(run_id, run["meta"], outputs)
        await _judge_bookend_tone_flow(run_id, run["meta"], golden_scripts, outputs)
        await _judge_coherence(run_id, run["meta"], run["segments"], outputs)

    return {"run_id": run_id, "judge_outputs": outputs}


# ---------------------------------------------------------------------------
# Orchestrator #2 — automated, background, no gold/label required. See
# automated_judging_build_plan.md. Triggered from app/services/
# automated_judging.py, which owns the on/off toggle and error containment —
# this function itself doesn't swallow exceptions (matches the manual path's
# convention), so the caller must wrap it.
# ---------------------------------------------------------------------------

# kind -> which judges fire (see automated_judging_build_plan.md Milestone 3).
# generate_brief (full pipeline path) gets the complete automated set;
# regenerate_bookends (same-day shortcut, reuses the 5 articles/segments
# untouched) only gets faithfulness_bookend, since that's the only thing
# genuinely fresh on that path. preopt runs are not in this dict at all —
# out of scope, no user-facing brief.
#
# faithfulness_article is deliberately NOT in this dict (removed 2026-07-15,
# see faithfulness-judge-plan.md section 5) — it used to run here, post-hoc,
# for every segment in a generate_brief run. It's now judged synchronously
# inline instead, from user_brief_runner._resolve_one via
# faithfulness_regen_service.py, for both cache-hit and cache-miss segments
# (misses additionally get the regenerate-on-flag retry loop). Leaving it in
# this dict too would double-log every segment for no benefit — the
# memoized cache verdict would make the second call free, but
# run_automated_judges' delete_judge_outputs_for_run would wipe the
# regen loop's own multi-attempt rows before rewriting a single
# always-attempt-1 row over them.
AUTOMATED_JUDGE_SETS: Dict[str, List[str]] = {
    "generate_brief": ["relevance", "order_correctness", "order_ranking", "faithfulness_bookend"],
    "regenerate_bookends": ["faithfulness_bookend"],
}


async def run_automated_judges(run_id: str, kind: str) -> Dict[str, Any]:
    """Runs AUTOMATED_JUDGE_SETS[kind] against run_id — no gold/freeze/label
    required. get_run_for_judges (eval_logging_service.py) already works on
    any run_id regardless of gold status, so no change was needed there.
    Raises on failure (does not swallow exceptions) — the caller
    (automated_judging.maybe_run_automated_judging) is responsible for
    catching and logging, since THIS function has no way to know it's being
    called from a background task that must never crash brief generation."""
    judge_names = AUTOMATED_JUDGE_SETS.get(kind)
    if not judge_names:
        return {"run_id": run_id, "judge_outputs": [], "skipped": f"kind={kind} not in AUTOMATED_JUDGE_SETS"}

    run = await eval_logging_service.get_run_for_judges(run_id)
    if run is None:
        raise ValueError(f"No eval run data found for run_id={run_id}")

    # Safe no-op in the common case (a fresh run_id has no prior judge
    # output), kept for consistency/safety against a rare retry rather than
    # because automated runs are ever re-judged the way gold runs are.
    # Scoped to judge_names (not a blanket delete) — a generate_brief run_id
    # already has faithfulness_article rows logged synchronously before this
    # function ever runs (see faithfulness_regen_service.py); a blanket
    # delete would wipe that regen loop's multi-attempt trail right before
    # this pass rewrites everything else.
    await eval_logging_service.delete_judge_outputs_for_run(run_id, judge_names=judge_names)

    articles_by_url = {a["url"]: a for a in run["fetched_articles"]}
    llm_ranking = run["llm_ranking"]

    outputs: List[Dict[str, Any]] = []

    if "relevance" in judge_names or "order_correctness" in judge_names:
        user = run["user"] or {}
        interests = list(user.get("interests") or []) + list(user.get("custom_topics") or [])
        location = user.get("location_name") or ""
        for is_local in (False, True):
            pool_rows = [r for r in llm_ranking if r["is_local"] == is_local]
            if not pool_rows:
                continue
            pool = [_pool_item(r, articles_by_url) for r in pool_rows]
            if "relevance" in judge_names:
                await _judge_relevance_for_pool(run_id, pool, interests, location, is_local, outputs)
            if "order_correctness" in judge_names:
                await _judge_order_for_pool(run_id, pool, is_local, outputs)

    if "faithfulness_article" in judge_names:
        await _judge_faithfulness_article_segments(run_id, run["segments"], articles_by_url, outputs, use_memoization=True)

    if "faithfulness_bookend" in judge_names and run["meta"]:
        await _judge_bookend_faithfulness(run_id, run["meta"], outputs)

    return {"run_id": run_id, "judge_outputs": outputs}


# ---------------------------------------------------------------------------
# Automated-judging read paths (Judge Output tab, see
# automated_judging_build_plan.md Milestone 6) — deliberately NOT joined
# against eval_gold_runs, unlike list_gold_runs/list_recent_runs in
# eval_gold_service.py, so runs judged automatically (never frozen) show up.
# ---------------------------------------------------------------------------


async def list_recent_automated_runs(limit: int = 50) -> List[Dict[str, Any]]:
    """Runs that have at least one automated judge_output row, newest first
    — the Judge Output tab's list. `kind` distinguishes a full run (5
    judges) from a bookends-only one (faithfulness_bookend only) so the UI
    can set the right expectation before you open one.

    Also resolves chosen_topics/custom_topics (same array_agg pattern as
    eval_gold_service.list_gold_runs) so the Run Detail header can show them
    alongside email/location/timestamp. Joining user_topics/topics fans out
    eval_judge_outputs rows before aggregation, so the count columns must be
    COUNT(DISTINCT jo.id), not a bare COUNT(*), or they'd be inflated by
    however many topics the user has.

    hit_count/miss_count are scoped to judge_name = 'relevance' specifically
    (its verdict values are the only 'hit'/'miss' in eval_judge_outputs —
    other judges use agree/disagree, clean/flagged, etc.) so the card list
    can show relevance's hit/miss split without a teammate having to guess
    where those aggregate numbers come from."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT r.id AS run_id, r.kind, r.started_at, u.email, u.location_name,
               COUNT(DISTINCT jo.id) AS judge_output_count,
               COUNT(DISTINCT jo.id) FILTER (WHERE jo.severity = 'critical') AS critical_count,
               COUNT(DISTINCT jo.id) FILTER (WHERE jo.severity = 'moderate') AS moderate_count,
               COUNT(DISTINCT jo.id) FILTER (WHERE jo.judge_name = 'relevance' AND jo.verdict = 'hit') AS hit_count,
               COUNT(DISTINCT jo.id) FILTER (WHERE jo.judge_name = 'relevance' AND jo.verdict = 'miss') AS miss_count,
               array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'chosen'), NULL) AS chosen_topics,
               array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'custom'), NULL) AS custom_topics
        FROM harness.eval_judge_outputs jo
        JOIN harness.eval_runs r ON r.id = jo.run_id
        LEFT JOIN harness.users u ON u.id = r.user_id
        LEFT JOIN harness.user_topics ut ON ut.user_id = u.id
        LEFT JOIN harness.topics t ON t.id = ut.topic_id
        WHERE r.kind IN ('generate_brief', 'regenerate_bookends')
        GROUP BY r.id, r.kind, r.started_at, u.email, u.location_name
        ORDER BY r.started_at DESC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def compute_automated_judging_rollup(days: int = 30) -> Dict[str, Any]:
    """Counts by judge_name/verdict/severity over the last `days` days of
    automated judge output — the actual point of the Judge Output tab (a
    per-run spot-check table alone doesn't answer "is this trending, and
    where", see judging_pipeline_scope_analysis.md Part 4C). Grouped by
    judge_name + verdict/severity only for now, not yet by topic/local — the
    build plan flags that as a real follow-up once real volume accumulates,
    not something to guess the right dimensions for on zero real data."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT jo.judge_name, jo.verdict, jo.severity, COUNT(*) AS n
        FROM harness.eval_judge_outputs jo
        JOIN harness.eval_runs r ON r.id = jo.run_id
        WHERE r.kind IN ('generate_brief', 'regenerate_bookends')
          AND jo.created_at > now() - ($1 || ' days')::interval
        GROUP BY jo.judge_name, jo.verdict, jo.severity
        ORDER BY jo.judge_name, n DESC
        """,
        str(days),
    )
    by_judge: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_judge.setdefault(r["judge_name"], []).append(
            {"verdict": r["verdict"], "severity": r["severity"], "n": r["n"]}
        )
    return {"days": days, "by_judge": by_judge}


# ---------------------------------------------------------------------------
# Validation: Cohen's kappa against M2 gold data (relevance + order-
# correctness only — the other judges have no dedicated gold labels, per
# this module's own docstring).
# ---------------------------------------------------------------------------


def _safe_kappa(y1: List[Any], y2: List[Any], labels: List[Any]) -> Optional[float]:
    if not y1:
        return None
    from sklearn.metrics import cohen_kappa_score
    try:
        k = cohen_kappa_score(y1, y2, labels=labels)
        if k is None or (isinstance(k, float) and math.isnan(k)):
            return None
        return float(k)
    except Exception:
        return None


async def compute_relevance_kappa() -> Dict[str, Any]:
    """Per-profile only — a pooled figure across every profile ever judged
    was actively misleading (mixed profiles at very different iteration
    stages into one number nobody could act on) and had no consumer, so it
    isn't computed at all rather than computed-and-hidden."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT gr.profile_label, gl.label AS gold_label, jo.verdict AS judge_label
        FROM harness.eval_judge_outputs jo
        JOIN harness.eval_gold_runs gr ON gr.run_id = jo.run_id
        JOIN harness.eval_gold_labels gl
          ON gl.run_id = jo.run_id AND gl.url = jo.url AND gl.superseded_at IS NULL
        WHERE jo.judge_name = 'relevance'
        """
    )
    labels_order = ["hit", "miss"]
    if not rows:
        return {"per_profile": {}}

    by_profile: Dict[str, Dict[str, List[str]]] = {}
    for r in rows:
        bucket = by_profile.setdefault(r["profile_label"], {"gold": [], "judge": []})
        bucket["gold"].append(r["gold_label"])
        bucket["judge"].append(r["judge_label"])

    per_profile = {
        label: {"kappa": _safe_kappa(v["gold"], v["judge"], labels_order), "n": len(v["gold"])}
        for label, v in by_profile.items()
    }
    return {"per_profile": per_profile}


async def _compute_order_agreement(judge_name: str, gold_column_case_sql: str) -> Dict[str, Any]:
    """Shared by compute_order_correctness_agreement (top-pick vs. whole
    pool) and compute_order_ranking_agreement (ranks 2-N vs. each other) —
    same shape, different judge_name and gold column. Gold side is a direct
    human label (see db/009_order_correctness_labels.sql /
    db/012_order_ranking_label.sql), not derived from the pipeline's own
    rank — the old derived approach was close to circular (it mostly
    measured whether the judge agreed with the pipeline's past self). NULL
    (unmarked) is treated as agree, same convention as significance_rank
    was."""
    pool = await harness_db.get_pool()
    judge_rows = await pool.fetch(
        f"""
        SELECT jo.run_id, gr.profile_label, jo.is_local, jo.verdict AS judge_verdict,
               {gold_column_case_sql} AS gold_correct
        FROM harness.eval_judge_outputs jo
        JOIN harness.eval_gold_runs gr ON gr.run_id = jo.run_id
        WHERE jo.judge_name = '{judge_name}'
        """
    )
    if not judge_rows:
        return {"per_profile": {}}

    by_profile: Dict[str, Dict[str, List[str]]] = {}

    for row in judge_rows:
        # gold_correct NULL (unmarked) defaults to True (agree).
        gold_correct = row["gold_correct"] if row["gold_correct"] is not None else True
        gold_verdict = "agree" if gold_correct else "disagree"
        bucket = by_profile.setdefault(row["profile_label"], {"gold": [], "judge": []})
        bucket["gold"].append(gold_verdict)
        bucket["judge"].append(row["judge_verdict"])

    labels_order = ["agree", "disagree"]
    per_profile = {
        label: {"kappa": _safe_kappa(v["gold"], v["judge"], labels_order), "n": len(v["gold"])}
        for label, v in by_profile.items()
    }
    return {"per_profile": per_profile}


async def compute_order_correctness_agreement() -> Dict[str, Any]:
    """Top-pick check: eval_gold_runs.non_local_top_correct / local_top_correct
    hold one explicit yes/no per pool, per run, for whether #1 was genuinely
    the most significant story in the whole pool."""
    return await _compute_order_agreement(
        "order_correctness",
        "CASE WHEN jo.is_local THEN gr.local_top_correct ELSE gr.non_local_top_correct END",
    )


async def compute_order_ranking_agreement() -> Dict[str, Any]:
    """Ranks-2-through-window check: eval_gold_runs.non_local_order_correct /
    local_order_correct hold one explicit yes/no per pool, per run, for
    whether ranks 2 through the labeling window are in defensible relative
    order (see db/012_order_ranking_label.sql)."""
    return await _compute_order_agreement(
        "order_ranking",
        "CASE WHEN jo.is_local THEN gr.local_order_correct ELSE gr.non_local_order_correct END",
    )
