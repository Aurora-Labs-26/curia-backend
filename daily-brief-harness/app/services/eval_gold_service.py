"""
DB-facing logic for the M2 gold-labeled test set (`harness.eval_gold_*`
tables — see db/005_eval_gold_schema.sql).

Unlike eval_logging_service.py, functions here do NOT swallow exceptions.
They're only ever called from explicit, human-driven dashboard actions
(freezing a run, saving a label, writing a golden script) — never from the
automatic Pre-Opt / Generate-Brief pipeline path — so a failure here should
surface as a real API error, not degrade silently.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from app.services import harness_db

# How many candidates (per pool) get pulled into the labeling view by
# default — a display default, not an enforced limit. Nothing blocks
# labeling further down; see eval_logging_service's log_llm_ranking_output
# for how the full ranked_order/local_ranked_order gets logged in the
# first place.
DEFAULT_NON_LOCAL_CANDIDATES = 8
DEFAULT_LOCAL_CANDIDATES = 4


async def list_recent_runs(kind: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
    """Recent eval_runs, for picking which one to freeze after reviewing it.
    Joins user email/location (like list_gold_runs does) since a bare
    user_id UUID isn't enough to recognize which run is which when choosing
    one to freeze."""
    pool = await harness_db.get_pool()
    if kind:
        rows = await pool.fetch(
            """
            SELECT r.id, r.kind, r.status, r.topic_id, r.user_id, r.brief_id, r.started_at,
                   u.email, u.location_name,
                   g.profile_label, g.is_active AS gold_is_active
            FROM harness.eval_runs r
            LEFT JOIN harness.eval_gold_runs g ON g.run_id = r.id
            LEFT JOIN harness.users u ON u.id = r.user_id
            WHERE r.kind = $1::harness.eval_run_kind
            ORDER BY r.started_at DESC LIMIT $2
            """,
            kind, limit,
        )
    else:
        rows = await pool.fetch(
            """
            SELECT r.id, r.kind, r.status, r.topic_id, r.user_id, r.brief_id, r.started_at,
                   u.email, u.location_name,
                   g.profile_label, g.is_active AS gold_is_active
            FROM harness.eval_runs r
            LEFT JOIN harness.eval_gold_runs g ON g.run_id = r.id
            LEFT JOIN harness.users u ON u.id = r.user_id
            ORDER BY r.started_at DESC LIMIT $1
            """,
            limit,
        )
    return [dict(r) for r in rows]


async def list_gold_runs() -> List[Dict[str, Any]]:
    """Card info for the Gold Set tab's run picker — the user's own email,
    location, and topics, not internal bookkeeping fields (profile_label/
    kind/frozen_at are useful for debugging but not what identifies a
    profile to a human labeling it)."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT g.run_id, g.profile_label, g.is_active, g.frozen_at,
               r.kind, r.status, r.started_at,
               u.email, u.location_name,
               array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'chosen'), NULL) AS chosen_topics,
               array_remove(array_agg(DISTINCT t.name) FILTER (WHERE ut.type = 'custom'), NULL) AS custom_topics
        FROM harness.eval_gold_runs g
        JOIN harness.eval_runs r ON r.id = g.run_id
        LEFT JOIN harness.users u ON u.id = r.user_id
        LEFT JOIN harness.user_topics ut ON ut.user_id = u.id
        LEFT JOIN harness.topics t ON t.id = ut.topic_id
        GROUP BY g.run_id, g.profile_label, g.is_active, g.frozen_at, r.kind, r.status, r.started_at, u.email, u.location_name
        ORDER BY g.frozen_at DESC
        """
    )
    return [dict(r) for r in rows]


async def freeze_run(run_id: str, profile_label: str) -> Dict[str, Any]:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO harness.eval_gold_runs (run_id, profile_label, is_active, frozen_at)
        VALUES ($1, $2, true, now())
        ON CONFLICT (run_id) DO UPDATE
            SET profile_label = EXCLUDED.profile_label, is_active = true, frozen_at = now()
        RETURNING run_id, profile_label, is_active, frozen_at
        """,
        run_id, profile_label,
    )
    return dict(row)


async def unfreeze_run(run_id: str) -> None:
    pool = await harness_db.get_pool()
    await pool.execute(
        "UPDATE harness.eval_gold_runs SET is_active = false WHERE run_id = $1", run_id
    )


async def delete_gold_run(run_id: str) -> None:
    """Removes the eval_gold_runs pin only — cleanup for stale unfrozen
    entries cluttering the Gold Set list. Does not touch eval_runs or
    anything it logged (articles, segments, rankings): eval_gold_labels /
    eval_gold_scripts / eval_judge_outputs for this run_id become orphaned
    (harmless leftover rows, invisible everywhere in the UI since every
    Gold Set view requires an eval_gold_runs row to exist first) rather
    than being cascade-deleted, since this is meant to be a purely
    UI-list cleanup action, not a data-destruction one."""
    pool = await harness_db.get_pool()
    await pool.execute("DELETE FROM harness.eval_gold_runs WHERE run_id = $1", run_id)


async def get_labeling_view(run_id: str) -> Optional[Dict[str, Any]]:
    """Everything needed to render the labeling tab for one frozen run:
    candidate articles (scoped separately per local/non-local pool, per
    Step 3's own ranking split), existing labels, segment/meta-segment
    text, and existing golden scripts."""
    pool = await harness_db.get_pool()

    gold = await pool.fetchrow(
        """
        SELECT run_id, profile_label, is_active, frozen_at,
               non_local_top_correct, local_top_correct,
               non_local_order_correct, local_order_correct
        FROM harness.eval_gold_runs WHERE run_id = $1
        """,
        run_id,
    )
    if not gold:
        return None

    run = await pool.fetchrow(
        "SELECT id, kind, status, topic_id, user_id, brief_id FROM harness.eval_runs WHERE id = $1", run_id
    )

    llm_rank_rows = await pool.fetch(
        """
        SELECT is_local, rank, url, title, reason
        FROM harness.eval_llm_ranking_output
        WHERE run_id = $1
        ORDER BY is_local, rank
        """,
        run_id,
    )

    existing_labels = await pool.fetch(
        """
        SELECT url, label, notes
        FROM harness.eval_gold_labels
        WHERE run_id = $1 AND superseded_at IS NULL
        """,
        run_id,
    )
    labels_by_url = {r["url"]: dict(r) for r in existing_labels}

    def _build_pool(is_local: bool, cap: int) -> List[Dict[str, Any]]:
        items = [r for r in llm_rank_rows if r["is_local"] == is_local][:cap]
        selected_cutoff = 1 if is_local else 4
        out = []
        for r in items:
            existing = labels_by_url.get(r["url"], {})
            out.append({
                "rank": r["rank"],
                "url": r["url"],
                "title": r["title"],
                "reason": r["reason"],
                "is_selected": r["rank"] <= selected_cutoff,
                "label": existing.get("label"),
                "notes": existing.get("notes"),
            })
        return out

    non_local = _build_pool(False, DEFAULT_NON_LOCAL_CANDIDATES)
    local = _build_pool(True, DEFAULT_LOCAL_CANDIDATES)

    segments = await pool.fetch(
        """
        SELECT article_id, segment_type, text
        FROM harness.eval_segment_transcripts
        WHERE run_id = $1
        """,
        run_id,
    )
    meta = await pool.fetchrow(
        "SELECT intro_text, outro_text FROM harness.eval_meta_segments WHERE run_id = $1",
        run_id,
    )

    existing_scripts = await pool.fetch(
        """
        SELECT segment_type, article_url, script_text
        FROM harness.eval_gold_scripts
        WHERE run_id = $1 AND superseded_at IS NULL
        """,
        run_id,
    )
    scripts_by_key = {(r["segment_type"], r["article_url"]): r["script_text"] for r in existing_scripts}

    # Segment transcripts don't carry their article's url directly (only
    # article_id) — resolve via eval_fetched_articles/eval_ranking_output
    # is overkill here; join through harness.articles instead, since every
    # logged segment's article_id already points there.
    article_urls = await pool.fetch(
        "SELECT id, url FROM harness.articles WHERE id = ANY($1::uuid[])",
        [s["article_id"] for s in segments if s["article_id"]],
    )
    url_by_article_id = {str(r["id"]): r["url"] for r in article_urls}

    # Broadcast order (see user_brief_runner.py's manifest assembly): intro,
    # lead, standard(s) in rank order, local, outro. Neither the DB
    # query above nor eval_meta_segments carries this ordering on its own —
    # it has to be reconstructed here.
    non_local_rank_by_url = {r["url"]: r["rank"] for r in llm_rank_rows if not r["is_local"]}
    SEGMENT_ORDER = {"intro": 0, "lead": 1, "standard": 2, "local": 3, "outro": 4}

    segment_views = []
    for s in segments:
        article_url = url_by_article_id.get(str(s["article_id"])) if s["article_id"] else None
        segment_views.append({
            "segment_type": s["segment_type"],
            "article_url": article_url,
            "generated_text": s["text"],
            "golden_text": scripts_by_key.get((s["segment_type"], article_url)),
            "_sort_key": (SEGMENT_ORDER.get(s["segment_type"], 9), non_local_rank_by_url.get(article_url, 0)),
        })
    if meta:
        for seg_type, text in (("intro", meta["intro_text"]), ("outro", meta["outro_text"])):
            segment_views.append({
                "segment_type": seg_type,
                "article_url": None,
                "generated_text": text,
                "golden_text": scripts_by_key.get((seg_type, None)),
                "_sort_key": (SEGMENT_ORDER[seg_type], 0),
            })

    segment_views.sort(key=lambda v: v["_sort_key"])
    for v in segment_views:
        del v["_sort_key"]

    return {
        "run_id": str(run["id"]),
        "kind": run["kind"],
        "status": run["status"],
        "profile_label": gold["profile_label"],
        "is_active": gold["is_active"],
        "frozen_at": gold["frozen_at"].isoformat(),
        "non_local": non_local,
        "local": local,
        "segments": segment_views,
        "non_local_top_correct": gold["non_local_top_correct"],
        "local_top_correct": gold["local_top_correct"],
        "non_local_order_correct": gold["non_local_order_correct"],
        "local_order_correct": gold["local_order_correct"],
    }


async def _save_gold_run_bool(run_id: str, column: str, correct: Optional[bool]) -> Dict[str, Any]:
    """Shared by save_top_pick_label and save_order_ranking_label — both are
    a single nullable-boolean UPDATE on eval_gold_runs, just a different
    column. `column` is always one of a small hardcoded set from this
    module, never user input, so direct interpolation is safe here (same
    pattern the original save_top_pick_label already used)."""
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        f"UPDATE harness.eval_gold_runs SET {column} = $2 WHERE run_id = $1 RETURNING run_id, {column}",
        run_id, correct,
    )
    return dict(row) if row else {}


async def save_top_pick_label(run_id: str, is_local: bool, correct: Optional[bool]) -> Dict[str, Any]:
    """Direct human label for compute_order_correctness_agreement — was the
    pool's #1-ranked article actually the right top pick? NULL (the default,
    also settable explicitly to undo a mark) means unmarked/agree, same
    convention as significance_rank. See db/009_order_correctness_labels.sql."""
    column = "local_top_correct" if is_local else "non_local_top_correct"
    return await _save_gold_run_bool(run_id, column, correct)


async def save_order_ranking_label(run_id: str, is_local: bool, correct: Optional[bool]) -> Dict[str, Any]:
    """Direct human label for compute_order_ranking_agreement — are ranks 2
    through the labeling window placed in defensible relative order? Same
    NULL-means-agree convention. See db/012_order_ranking_label.sql."""
    column = "local_order_correct" if is_local else "non_local_order_correct"
    return await _save_gold_run_bool(run_id, column, correct)


async def save_label(
    run_id: str,
    url: str,
    label: str,
    notes: Optional[str] = None,
) -> None:
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE harness.eval_gold_labels SET superseded_at = now()
                WHERE run_id = $1 AND url = $2 AND superseded_at IS NULL
                """,
                run_id, url,
            )
            await conn.execute(
                """
                INSERT INTO harness.eval_gold_labels (run_id, url, label, notes)
                VALUES ($1, $2, $3::harness.eval_relevance_label, $4)
                """,
                run_id, url, label, notes,
            )


async def get_active_scripts(run_id: str) -> Dict[Any, str]:
    """(segment_type, article_url) -> script_text for every active golden
    script of a run. Same shape get_labeling_view already builds internally
    as scripts_by_key — pulled out as its own function since
    eval_judges_service's tone/flow pairwise judge needs to look up one
    golden reference at a time without re-fetching the whole labeling view
    (candidates + segments) just for this."""
    pool = await harness_db.get_pool()
    rows = await pool.fetch(
        """
        SELECT segment_type, article_url, script_text
        FROM harness.eval_gold_scripts
        WHERE run_id = $1 AND superseded_at IS NULL
        """,
        run_id,
    )
    return {(r["segment_type"], r["article_url"]): r["script_text"] for r in rows}


async def save_script(
    run_id: str,
    segment_type: str,
    article_url: Optional[str],
    script_text: str,
) -> None:
    pool = await harness_db.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                UPDATE harness.eval_gold_scripts SET superseded_at = now()
                WHERE run_id = $1 AND segment_type = $2
                  AND COALESCE(article_url, '') = COALESCE($3, '') AND superseded_at IS NULL
                """,
                run_id, segment_type, article_url,
            )
            await conn.execute(
                """
                INSERT INTO harness.eval_gold_scripts (run_id, segment_type, article_url, script_text)
                VALUES ($1, $2, $3, $4)
                """,
                run_id, segment_type, article_url, script_text,
            )
