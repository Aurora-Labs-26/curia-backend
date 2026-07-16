-- Curia Daily Brief — direct human labels for the order-correctness judge
--
-- compute_order_correctness_agreement() previously derived "gold" from the
-- pipeline's own historical rank (falling back to it whenever no human
-- significance_rank existed) — close to circular, since it mostly measured
-- whether the judge agreed with the pipeline's past self, not an
-- independent human call. These two columns are the direct replacement:
-- one explicit yes/no per pool, per run. NULL means "unmarked" and is
-- treated as agree, matching the existing significance_rank convention
-- ("the places I haven't marked are the places I think the ranking is
-- already good enough").
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

ALTER TABLE harness.eval_gold_runs
    ADD COLUMN IF NOT EXISTS non_local_top_correct boolean NULL,
    ADD COLUMN IF NOT EXISTS local_top_correct boolean NULL;
