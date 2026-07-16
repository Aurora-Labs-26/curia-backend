-- Curia Daily Brief — eval-pipeline instrumentation (M0 addendum)
--
-- Captures Step 3's (Score & Curate, an LLM call — see
-- app/templates/prompts/score_curate.py) own full ranked_order/
-- local_ranked_order output, including its per-article "reason" text for
-- the top ranks. This is distinct from harness.eval_ranking_output, which
-- only captures Step 2b's no-LLM pre-filter (scoring_service.rank_articles)
-- — the two are separate ranking passes over the same pool, and both are
-- worth keeping since Step 3's editorial reasoning is the signal M2/M3's
-- order-correctness ground truth actually needs to be checked against.
--
-- Lives in the same `harness` schema, additive only, never read by the
-- live Pre-Opt / Generate-Brief pipeline itself. Written by
-- app/services/eval_logging_service.py.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

CREATE TABLE IF NOT EXISTS harness.eval_llm_ranking_output (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    url             text NOT NULL,
    title           text NOT NULL,
    is_local        boolean NOT NULL,
    rank            int NOT NULL,
    reason          text NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, is_local, url)
);

CREATE INDEX IF NOT EXISTS ix_eval_llm_ranking_output_run_id ON harness.eval_llm_ranking_output (run_id);
