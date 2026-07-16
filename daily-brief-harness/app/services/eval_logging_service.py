"""
DB-facing logic for the eval-pipeline instrumentation schema
(`harness.eval_*` tables — see db/003_eval_schema.sql). Pure logging
side-channel for the LLM-eval harness (see daily-brief-eval-buildplan.md) —
never read by the live Pre-Opt / Generate-Brief pipeline itself.

Every function here catches its own exceptions and logs a warning instead of
raising — a Postgres hiccup or a bug in this module must never break a real
brief generation. Callers in preopt_runner.py/user_brief_runner.py can await
these functions the same way they already await cache_service's, with no
extra try/except boilerplate at each call site.

start_eval_run returns None on failure; every other function here is a
guarded no-op when passed a None run_id, so a broken eval-logging path
degrades to "no eval rows for this run", never an exception.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from app.services import harness_db

logger = logging.getLogger(__name__)


async def start_eval_run(
    kind: str,
    *,
    topic_id: Optional[str] = None,
    user_id: Optional[str] = None,
    brief_id: Optional[str] = None,
) -> Optional[str]:
    """kind in {"preopt", "generate_brief", "regenerate_bookends"}. Id is
    Postgres-generated (gen_random_uuid() default), matching cache_service.py's
    existing convention rather than minting one in Python."""
    try:
        pool = await harness_db.get_pool()
        row = await pool.fetchrow(
            """
            INSERT INTO harness.eval_runs (kind, topic_id, user_id, brief_id)
            VALUES ($1::harness.eval_run_kind, $2, $3, $4)
            RETURNING id
            """,
            kind, topic_id, user_id, brief_id,
        )
        return str(row["id"])
    except Exception as e:
        logger.warning(f"eval_logging_service.start_eval_run failed (kind={kind}): {e}")
        return None


async def finish_eval_run(run_id: Optional[str], status: str, error: Optional[str] = None) -> None:
    """status in {"completed", "failed"}."""
    if run_id is None:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.execute(
            """
            UPDATE harness.eval_runs
            SET status = $2::harness.eval_run_status, error = $3, finished_at = now()
            WHERE id = $1
            """,
            run_id, status, error,
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.finish_eval_run failed (run_id={run_id}): {e}")


async def log_fetched_articles(run_id: Optional[str], articles: List[Dict[str, Any]], source_kind: str) -> None:
    """One row per raw article dict (title/description/source/url/published_date/topic
    — see news_service's fetch return shape). Duplicate urls within the same run
    (e.g. the same story surfacing in both a custom-topic and local fetch) are
    silently ignored via ON CONFLICT (run_id, url) DO NOTHING rather than raising."""
    if run_id is None or not articles:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.executemany(
            """
            INSERT INTO harness.eval_fetched_articles
                (run_id, url, title, description, source, published_date, topic_tag, source_kind)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (run_id, url) DO NOTHING
            """,
            [
                (
                    run_id,
                    a.get("url", ""),
                    a.get("title", ""),
                    a.get("description"),
                    a.get("source"),
                    a.get("published_date"),
                    a.get("topic"),
                    source_kind,
                )
                for a in articles
            ],
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_fetched_articles failed (run_id={run_id}): {e}")


async def update_fetched_article_content(run_id: Optional[str], selections: List[Dict[str, Any]]) -> None:
    """Backfills full_text/content_fetched/used_alternate_source for the curated
    subset that went through the content-fetch step, matched by (run_id, url)
    against rows log_fetched_articles already wrote."""
    if run_id is None or not selections:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.executemany(
            """
            UPDATE harness.eval_fetched_articles
            SET full_text = $3, content_fetched = $4, used_alternate_source = $5
            WHERE run_id = $1 AND url = $2
            """,
            [
                (
                    run_id,
                    s.get("url", ""),
                    s.get("full_text"),
                    s.get("content_fetched"),
                    s.get("used_alternate_source"),
                )
                for s in selections
            ],
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.update_fetched_article_content failed (run_id={run_id}): {e}")


async def log_ranking_output(run_id: Optional[str], ranked: List[Dict[str, Any]]) -> None:
    """Logs the local ranker's full breakdown for every article it considered,
    included or not — see scoring_service.rank_articles' `ranked` list shape
    (composite/scores/rank/included/cluster_size)."""
    if run_id is None or not ranked:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.executemany(
            """
            INSERT INTO harness.eval_ranking_output
                (run_id, url, title, is_local, included, rank, composite_score, scores_json, cluster_size)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9)
            """,
            [
                (
                    run_id,
                    a.get("url", ""),
                    a.get("title", ""),
                    bool(a.get("is_local", False)),
                    bool(a.get("included", False)),
                    a.get("rank"),
                    a.get("composite"),
                    json.dumps(a.get("scores")) if a.get("scores") is not None else None,
                    a.get("cluster_size"),
                )
                for a in ranked
            ],
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_ranking_output failed (run_id={run_id}): {e}")


async def log_llm_ranking_output(
    run_id: Optional[str],
    ranked_order: List[Dict[str, Any]],
    local_ranked_order: List[Dict[str, Any]],
) -> None:
    """Logs Step 3's (Score & Curate, an LLM call) own full ranked_order/
    local_ranked_order — distinct from log_ranking_output, which only
    captures Step 2b's no-LLM pre-filter. Each item's "url" must already be
    resolved (see pipeline.run_score_curate_step, which attaches it via the
    item's "id" before returning). Rank is the item's 1-based position in
    its own list; "reason" is only present for the top few ranks per the
    Score & Curate prompt's own output rules, NULL below that."""
    if run_id is None:
        return
    rows = [
        (run_id, item.get("url", ""), item.get("title", ""), is_local, i + 1, item.get("reason"))
        for is_local, items in ((False, ranked_order), (True, local_ranked_order))
        for i, item in enumerate(items or [])
    ]
    if not rows:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.executemany(
            """
            INSERT INTO harness.eval_llm_ranking_output
                (run_id, url, title, is_local, rank, reason)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (run_id, is_local, url) DO NOTHING
            """,
            rows,
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_llm_ranking_output failed (run_id={run_id}): {e}")


async def log_segment_transcript(
    run_id: Optional[str],
    *,
    article_id: Optional[str],
    segment_type: str,
    text: str,
    cache_hit: bool,
    word_count: Optional[int] = None,
    model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    simulated: Optional[bool] = None,
) -> None:
    """Append-only per-run log, distinct from harness.article_segment_cache
    (a mutable upsert-by-key cache). Logged for cache hits too — those rows
    just echo the cached text with no latency/token/model data."""
    if run_id is None:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.execute(
            """
            INSERT INTO harness.eval_segment_transcripts
                (run_id, article_id, segment_type, text, word_count, cache_hit,
                 model, input_tokens, output_tokens, latency_ms, simulated)
            VALUES ($1, $2, $3::harness.segment_type, $4, $5, $6, $7, $8, $9, $10, $11)
            """,
            run_id, article_id, segment_type, text, word_count, cache_hit,
            model, input_tokens, output_tokens, latency_ms, simulated,
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_segment_transcript failed (run_id={run_id}): {e}")


async def log_meta_segments(
    run_id: Optional[str],
    *,
    intro: str,
    outro: str,
    inputs_used: Dict[str, Any],
    model: Optional[str] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    simulated: Optional[bool] = None,
) -> None:
    """One row per run for the whole Intro/Outro call — both are
    always generated together from one shared set of inputs, so a single row
    (rather than one per segment type) avoids duplicating `inputs_used`."""
    if run_id is None:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.execute(
            """
            INSERT INTO harness.eval_meta_segments
                (run_id, intro_text, outro_text, inputs_used,
                 model, input_tokens, output_tokens, latency_ms, simulated)
            VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9)
            """,
            run_id, intro, outro, json.dumps(inputs_used),
            model, input_tokens, output_tokens, latency_ms, simulated,
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_meta_segments failed (run_id={run_id}): {e}")


async def get_run_for_checks(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetches everything eval_checks_service needs to validate one run: the
    run's own kind/status/error, every logged segment transcript, and its
    meta-segments row (if any). Deliberately does not read
    eval_fetched_articles/eval_ranking_output — M1's deterministic checks
    (TTS-readiness, word budgets, personalization, structure) don't need them;
    that's M3 faithfulness/diversity-judge territory."""
    if run_id is None:
        return None
    try:
        pool = await harness_db.get_pool()
        run = await pool.fetchrow(
            "SELECT id, kind, status, error FROM harness.eval_runs WHERE id = $1", run_id
        )
        if not run:
            return None
        segments = await pool.fetch(
            """
            SELECT article_id, segment_type, text, word_count, cache_hit,
                   latency_ms, input_tokens, output_tokens
            FROM harness.eval_segment_transcripts WHERE run_id = $1
            """,
            run_id,
        )
        meta = await pool.fetchrow(
            """
            SELECT intro_text, outro_text, inputs_used,
                   latency_ms, input_tokens, output_tokens
            FROM harness.eval_meta_segments WHERE run_id = $1
            """,
            run_id,
        )
        meta_dict = None
        if meta:
            meta_dict = dict(meta)
            meta_dict["inputs_used"] = json.loads(meta_dict["inputs_used"])
        return {
            "id": str(run["id"]),
            "kind": run["kind"],
            "status": run["status"],
            "error": run["error"],
            "segments": [dict(s) for s in segments],
            "meta": meta_dict,
        }
    except Exception as e:
        logger.warning(f"eval_logging_service.get_run_for_checks failed (run_id={run_id}): {e}")
        return None


async def get_run_for_judges(run_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Superset of get_run_for_checks, for M3 judge consumption: pulls
    eval_fetched_articles (incl. full_text — the faithfulness judges'
    ground truth), eval_llm_ranking_output (Step 3's editorial ranking —
    the relevance/order-correctness judges' input),
    segments resolved to their article's url (join through harness.articles,
    same join eval_gold_service.get_labeling_view already does), and meta
    segments. get_run_for_checks deliberately skips all of this — see its
    own docstring: "that's M3 faithfulness/diversity-judge territory"."""
    if run_id is None:
        return None
    try:
        pool = await harness_db.get_pool()
        run = await pool.fetchrow(
            "SELECT id, kind, status, error, user_id FROM harness.eval_runs WHERE id = $1", run_id
        )
        if not run:
            return None
        user = None
        if run["user_id"]:
            user_row = await pool.fetchrow(
                """
                SELECT u.location_name,
                       array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'chosen'), NULL) AS interests,
                       array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'custom'), NULL) AS custom_topics
                FROM harness.users u
                LEFT JOIN harness.user_topics ut ON ut.user_id = u.id
                LEFT JOIN harness.topics t ON t.id = ut.topic_id
                WHERE u.id = $1
                GROUP BY u.id, u.location_name
                """,
                run["user_id"],
            )
            if user_row:
                user = dict(user_row)
        fetched_articles = await pool.fetch(
            """
            SELECT url, title, description, source, published_date, topic_tag,
                   source_kind, full_text, content_fetched, used_alternate_source
            FROM harness.eval_fetched_articles WHERE run_id = $1
            """,
            run_id,
        )
        llm_ranking = await pool.fetch(
            """
            SELECT url, title, is_local, rank, reason
            FROM harness.eval_llm_ranking_output WHERE run_id = $1
            ORDER BY is_local, rank
            """,
            run_id,
        )
        # ORDER BY broadcast position, not created_at: segments are generated
        # concurrently (asyncio.gather, for latency), so insertion order is
        # whichever LLM call happened to finish first, not the real
        # lead->standard->local sequence. Un-ordered rows here were silently
        # scrambling brief_coherence's stitched input (see judging_analysis_
        # actionables.md Judging #6) — is_local ASC puts every non-local
        # segment (lead + standards, by rank) before the single local one,
        # matching real broadcast order, same join pattern as
        # eval_gold_service.get_labeling_view.
        # s.article_id (added alongside a.url/a.title, not replacing them) is
        # what the automated path's faithfulness_article memoization needs to
        # look up harness.article_segment_cache directly (see
        # cache_service.get_cached_segment) — is_local doesn't need its own
        # column since article_segment_cache's own CHECK constraint already
        # enforces is_local = (segment_type = 'local'), so it's derivable.
        segments = await pool.fetch(
            """
            SELECT s.segment_type, s.text, s.word_count, s.article_id, a.url, a.title
            FROM harness.eval_segment_transcripts s
            LEFT JOIN harness.articles a ON a.id = s.article_id
            LEFT JOIN harness.eval_llm_ranking_output lro ON lro.run_id = s.run_id AND lro.url = a.url
            WHERE s.run_id = $1
            ORDER BY lro.is_local ASC, lro.rank ASC
            """,
            run_id,
        )
        meta = await pool.fetchrow(
            "SELECT intro_text, outro_text, inputs_used FROM harness.eval_meta_segments WHERE run_id = $1",
            run_id,
        )
        meta_dict = None
        if meta:
            meta_dict = dict(meta)
            meta_dict["inputs_used"] = json.loads(meta_dict["inputs_used"])
        return {
            "id": str(run["id"]),
            "kind": run["kind"],
            "status": run["status"],
            "user": user,
            "fetched_articles": [dict(a) for a in fetched_articles],
            "llm_ranking": [dict(r) for r in llm_ranking],
            "segments": [dict(s) for s in segments],
            "meta": meta_dict,
        }
    except Exception as e:
        logger.warning(f"eval_logging_service.get_run_for_judges failed (run_id={run_id}): {e}")
        return None


async def delete_judge_outputs_for_run(run_id: str, judge_names: Optional[List[str]] = None) -> None:
    """Wipes existing harness.eval_judge_outputs rows for a run before a
    fresh judging pass writes new ones. Judges are meant to be re-run
    repeatedly (after a prompt fix, per the buildplan's M6 ongoing-
    recalibration guidance) — without this, re-running would just accumulate
    duplicate rows from every past attempt, corrupting both the "Judges" UI
    view and the kappa validation report, which read all rows for a run with
    no notion of "latest". No supersede-and-keep-history pattern needed here
    (unlike eval_gold_labels/eval_gold_scripts) since judge output isn't a
    human edit being preserved, it's a fully automated recomputation each
    time. Same non-swallowing convention as log_judge_output.

    judge_names, when given, scopes the delete to only those judges —
    required by run_automated_judges (eval_judges_service.py): faithfulness_
    article is judged synchronously inline before this function's caller
    ever runs (see faithfulness_regen_service.py), so a blanket delete here
    would wipe its already-logged, possibly multi-attempt rows for no
    reason, right before rewriting a single always-attempt-1 row over them.
    run_judges_for_gold_run still wants (and passes) the blanket wipe — it's
    the one true full-recompute path."""
    pool = await harness_db.get_pool()
    if judge_names is None:
        await pool.execute("DELETE FROM harness.eval_judge_outputs WHERE run_id = $1", run_id)
    else:
        await pool.execute(
            "DELETE FROM harness.eval_judge_outputs WHERE run_id = $1 AND judge_name = ANY($2::text[])",
            run_id, judge_names,
        )


async def log_judge_output(
    run_id: Optional[str],
    judge_name: str,
    *,
    url: Optional[str] = None,
    segment_type: Optional[str] = None,
    is_local: Optional[bool] = None,
    verdict: str,
    severity: Optional[str] = None,
    reasoning: Optional[str] = None,
    detail: Optional[Dict[str, Any]] = None,
    judge_model: str,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    attempt_number: int = 1,
) -> None:
    """Insert into harness.eval_judge_outputs (db/007_eval_judge_schema.sql,
    cost columns added by db/013_judge_cost_logging.sql, attempt_number added
    by db/016_faithfulness_regen.sql). judge_name in
    {"relevance", "order_correctness", "order_ranking", "faithfulness_article",
    "faithfulness_bookend", "tone_flow_pairwise", "brief_coherence"}.

    attempt_number is only ever >1 for faithfulness_article rows produced by
    faithfulness_regen_service's regenerate-on-flag loop (see
    faithfulness-judge-plan.md section 5) — every other judge/caller leaves
    it at the default 1.

    input_tokens/output_tokens/latency_ms are optional (None for callers that
    don't have them, e.g. a judge call whose result was reconciled from two
    sub-calls already logged separately) — added specifically so real judge
    spend is knowable before automated judging (see
    automated_judging_build_plan.md) starts running unconditionally on every
    Generate Brief call; before this, the only way to see judge cost was a
    one-off diagnostic call reading response.usage directly.

    Unlike every other function in this file, this one does NOT catch its
    own exceptions — it's only ever called from eval_judges_service's
    run_judges_for_gold_run, itself only ever triggered by an explicit
    dashboard action against an already-frozen gold run, never from the live
    pipeline (same reasoning as eval_gold_service.py's save_label/
    save_script: a failure here should surface as a real error the human
    triggering it can see, not silently disappear as "no judge result". The
    new automated path (eval_judges_service.run_automated_judges) wraps its
    own call site in a try/except instead of changing this function's
    behavior — see automated_judging.py."""
    if run_id is None:
        return
    pool = await harness_db.get_pool()
    await pool.execute(
        """
        INSERT INTO harness.eval_judge_outputs
            (run_id, judge_name, url, segment_type, is_local, verdict, severity, reasoning, detail, judge_model,
             input_tokens, output_tokens, latency_ms, attempt_number)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11, $12, $13, $14)
        """,
        run_id, judge_name, url, segment_type, is_local, verdict, severity, reasoning,
        json.dumps(detail) if detail is not None else None, judge_model,
        input_tokens, output_tokens, latency_ms, attempt_number,
    )


async def log_eval_result(
    run_id: Optional[str],
    check_name: str,
    check_type: str,
    *,
    result_status: Optional[str] = None,
    result_score: Optional[float] = None,
    detail: Optional[str] = None,
    judge_model: Optional[str] = None,
) -> None:
    """check_type in {"deterministic", "llm_judge"}; result_status in
    {"pass", "fail", "flag"} (nullable — a purely informational/score-only
    check, e.g. a cost summary, can omit it)."""
    if run_id is None:
        return
    try:
        pool = await harness_db.get_pool()
        await pool.execute(
            """
            INSERT INTO harness.eval_results
                (run_id, check_name, check_type, result_status, result_score, detail, judge_model)
            VALUES ($1, $2, $3::harness.eval_check_type, $4::harness.eval_result_status, $5, $6, $7)
            """,
            run_id, check_name, check_type, result_status, result_score, detail, judge_model,
        )
    except Exception as e:
        logger.warning(f"eval_logging_service.log_eval_result failed (run_id={run_id}, check={check_name}): {e}")
