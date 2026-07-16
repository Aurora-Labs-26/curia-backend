-- Curia Daily Brief — judge call cost logging
--
-- harness.eval_judge_outputs never captured token/latency data, unlike
-- eval_segment_transcripts/eval_meta_segments which do — the only way to see
-- real judge spend was a one-off diagnostic call reading response.usage
-- directly (see progress.md's Score & Curate cost section). Adding this
-- before automated judging goes live (see automated_judging_build_plan.md
-- Milestone 1) so real per-brief judge cost is knowable from day one, not
-- discovered after the fact.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.eval_judge_outputs ADD COLUMN IF NOT EXISTS input_tokens int NULL;
ALTER TABLE harness.eval_judge_outputs ADD COLUMN IF NOT EXISTS output_tokens int NULL;
ALTER TABLE harness.eval_judge_outputs ADD COLUMN IF NOT EXISTS latency_ms int NULL;
