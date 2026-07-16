-- Curia Daily Brief — collapse eval_gold_labels.label to a pure binary (hit/miss)
--
-- Was a 3-way direct_hit/tangential/miss enum. Collapsing direct_hit and
-- tangential into one "hit" value:
-- 1. Matches the pipeline's own relevance gate in score_curate.py (Step 3
--    Phase A / Step 4 Phase A) — already a binary IN/OUT decision, not a
--    3-way weighting. The label should validate what the gate actually
--    does, not a finer grain the gate doesn't have.
-- 2. Nearly all of this session's kappa disagreement analysis traced back
--    to the direct_hit-vs-tangential boundary specifically (the home-
--    location tautology, redundant-coverage cases), not to whether
--    something was a genuine miss — removing that boundary removes the
--    noisiest source of disagreement.
--
-- Existing direct_hit/tangential rows (including superseded history, kept
-- for the record) are migrated to "hit", never dropped — this has to be
-- safe to run against a database with real existing labels (this ships to
-- Railway eventually, not just applied to an empty fresh install).
--
-- Postgres can't ALTER TYPE ... DROP VALUE, so this widens the column to
-- text, rewrites the data, then re-narrows to a freshly-defined 2-value
-- enum. Idempotent: skips entirely once the enum is already binary.

DO $$
DECLARE
    current_values text[];
BEGIN
    SELECT array_agg(enumlabel ORDER BY enumsortorder) INTO current_values
    FROM pg_enum WHERE enumtypid = 'harness.eval_relevance_label'::regtype;

    IF current_values = ARRAY['hit', 'miss'] THEN
        RETURN;
    END IF;

    ALTER TABLE harness.eval_gold_labels ALTER COLUMN label TYPE text;
    UPDATE harness.eval_gold_labels SET label = 'hit' WHERE label IN ('direct_hit', 'tangential');

    DROP TYPE harness.eval_relevance_label;
    CREATE TYPE harness.eval_relevance_label AS ENUM ('hit', 'miss');

    ALTER TABLE harness.eval_gold_labels
        ALTER COLUMN label TYPE harness.eval_relevance_label USING label::harness.eval_relevance_label;
END $$;
