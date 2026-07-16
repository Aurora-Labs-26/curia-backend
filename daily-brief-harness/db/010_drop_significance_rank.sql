-- Curia Daily Brief — drop eval_gold_labels.significance_rank
--
-- Only ever consumed by the old compute_order_correctness_agreement, which
-- derived "gold" from significance_rank (falling back to the pipeline's own
-- rank) — fully replaced by direct non_local_top_correct/local_top_correct
-- labels on eval_gold_runs (see db/009_order_correctness_labels.sql).
-- Nothing reads this column anymore.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.eval_gold_labels DROP COLUMN IF EXISTS significance_rank;
