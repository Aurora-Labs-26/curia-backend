-- Curia Daily Brief — M3: LLM judge outputs
--
-- One generic table for every judge's verdict (relevance, order-correctness,
-- faithfulness x2, tone/flow pairwise, brief coherence, overview-fidelity)
-- rather than five near-duplicate tables — mirrors eval_gold_scripts' shape
-- (run_id + optional url/segment_type identifiers), judge-specific
-- structured payload goes in `detail jsonb` (same pattern as
-- eval_meta_segments.inputs_used).
--
-- Judges only ever run against already-frozen eval_gold_runs, writing fresh
-- rows each time — there's no "editing" concept here to protect with a
-- freeze trigger the way eval_gold_labels/eval_gold_scripts need one.
--
-- Lives in the same `harness` schema, additive only, never read by the live
-- Pre-Opt / Generate-Brief pipeline itself (see daily-brief-eval-buildplan.md
-- M3 — this is offline validation against M2's gold profiles, not a live
-- pipeline step; that's M5's job).
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

CREATE TABLE IF NOT EXISTS harness.eval_judge_outputs (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    judge_name    text NOT NULL,
    url           text NULL,
    segment_type  text NULL,
    is_local      boolean NULL,
    verdict       text NOT NULL,
    severity      text NULL,
    reasoning     text NULL,
    detail        jsonb NULL,
    judge_model   text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_judge_outputs_run_id ON harness.eval_judge_outputs (run_id);
CREATE INDEX IF NOT EXISTS ix_eval_judge_outputs_run_judge ON harness.eval_judge_outputs (run_id, judge_name);
