-- Curia Daily Brief — direct human labels for the order-RANKING judge
--
-- db/009_order_correctness_labels.sql validates only the #1 pick against the
-- whole pool. Nothing validates whether the relative order of ranks 2
-- through DEFAULT_NON_LOCAL_CANDIDATES/DEFAULT_LOCAL_CANDIDATES (the same
-- window already used for relevance hit/miss labeling — see
-- eval_gold_service.py) is sensible. These two columns are a second,
-- independent flag per pool, same NULL-means-agree convention as
-- non_local_top_correct/local_top_correct.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.eval_gold_runs
    ADD COLUMN IF NOT EXISTS non_local_order_correct boolean NULL,
    ADD COLUMN IF NOT EXISTS local_order_correct boolean NULL;
