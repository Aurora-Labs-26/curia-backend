-- Curia Daily Brief — soften the M2 freeze from a hard DB wall to a soft flag
--
-- 005_eval_gold_schema.sql's trigger blocked UPDATE/DELETE on 5 tables the
-- moment any run was marked gold-active, to protect eval_gold_labels /
-- eval_gold_scripts from silently going stale. That's too coarse: no gold
-- label or script is keyed off eval_ranking_output / eval_segment_transcripts
-- / eval_meta_segments content, only off (run_id, url) / (run_id,
-- segment_type, article_url) identity — regenerating those tables' rows
-- in place doesn't break anything they reference.
--
-- Product direction changed too: instead of a fixed set of 6 named "golden"
-- profiles that must never be touched, ANY eval_run can be frozen (pinned)
-- via harness.eval_gold_runs as needed, and pipeline/judge prompts need to
-- keep being iterated on against those same runs without a DB-level wall in
-- the way. eval_gold_runs.is_active remains the (now purely informational)
-- pinned marker — nothing here drops that table or its data; the 6 existing
-- rows and everything they reference are untouched.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'eval_fetched_articles',
        'eval_ranking_output',
        'eval_llm_ranking_output',
        'eval_segment_transcripts',
        'eval_meta_segments'
    ]
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_block_frozen_mutation ON harness.%I', t);
    END LOOP;
END $$;

DROP FUNCTION IF EXISTS harness.eval_block_frozen_mutation();
