-- Curia Daily Brief — M2: gold-labeled test set schema
--
-- Human-authored ground truth for validating M3's future LLM judges,
-- layered on top of M0's eval_* logging tables. Nothing here duplicates
-- M0 data — a gold run just PINS an existing eval_runs.id as frozen; the
-- article/ranking/transcript data it points to stays exactly where M0
-- already put it.
--
-- Lives in the same `harness` schema, additive only, never read by the
-- live Pre-Opt / Generate-Brief pipeline itself.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE harness.eval_relevance_label AS ENUM ('direct_hit', 'tangential', 'miss');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- EVAL_GOLD_RUNS — the freeze marker. One row = one eval_runs.id is
-- protected (see the trigger below) and treated as a canonical gold
-- snapshot for `profile_label`. `is_active = false` lifts protection
-- (an "unfreeze", e.g. the wrong run got pinned, or a fresher snapshot
-- supersedes it) without ever touching the underlying M0 rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_gold_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL UNIQUE REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    profile_label   text NOT NULL,
    is_active       boolean NOT NULL DEFAULT true,
    frozen_at       timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- EVAL_GOLD_LABELS — human relevance call + optional significance rank per
-- (run, article). `significance_rank` is scoped within whichever pool the
-- article belongs to (local vs non-local rank independently — see
-- score_curate.py's own Step 1/3/4 split; local and non-local articles
-- never compete against each other for a slot, so their ranks aren't on
-- the same scale). Append-only: editing inserts a new row and supersedes
-- the old one rather than overwriting it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_gold_labels (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id              uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    url                 text NOT NULL,
    label               harness.eval_relevance_label NOT NULL,
    significance_rank   int NULL,
    notes               text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    superseded_at       timestamptz NULL
);

CREATE INDEX IF NOT EXISTS ix_eval_gold_labels_run_id ON harness.eval_gold_labels (run_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_gold_labels_active
    ON harness.eval_gold_labels (run_id, url)
    WHERE superseded_at IS NULL;

-- ---------------------------------------------------------------------------
-- EVAL_GOLD_SCRIPTS — hand-authored ideal transcript per (run, segment).
-- `article_url` is set for lead/standard/local (ties to a specific
-- article) and NULL for the intro/outro meta-segments (one each
-- per run — see eval_meta_segments). Same append-only pattern as labels.
-- `segment_type` is plain text, NOT harness.segment_type — that enum only
-- has ('lead','standard','local'); golden scripts also need to cover
-- intro/outro, which aren't in it.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_gold_scripts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    segment_type    text NOT NULL,
    article_url     text NULL,
    script_text     text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    superseded_at   timestamptz NULL
);

CREATE INDEX IF NOT EXISTS ix_eval_gold_scripts_run_id ON harness.eval_gold_scripts (run_id);

-- COALESCE so intro/outro (article_url IS NULL) still get a real
-- one-active-row-per-segment guarantee — a plain UNIQUE index treats NULLs
-- as distinct from each other, which would silently defeat this for the
-- two meta-segment types otherwise.
CREATE UNIQUE INDEX IF NOT EXISTS ux_eval_gold_scripts_active
    ON harness.eval_gold_scripts (run_id, segment_type, COALESCE(article_url, ''))
    WHERE superseded_at IS NULL;

-- ---------------------------------------------------------------------------
-- FREEZE ENFORCEMENT — a trigger, not just application code, so the
-- guarantee holds even against a future bug or a manual psql session, not
-- just the one app code path (eval_logging_service.update_fetched_article_
-- content) that would otherwise be relied on to check this itself. Every
-- function in eval_logging_service.py already catches all exceptions
-- internally, so a trigger-raised exception during a real pipeline call
-- degrades to a logged warning, never a broken brief generation.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION harness.eval_block_frozen_mutation() RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM harness.eval_gold_runs
        WHERE run_id = OLD.run_id AND is_active
    ) THEN
        RAISE EXCEPTION 'eval run % is frozen as gold data — % on % blocked',
            OLD.run_id, TG_OP, TG_TABLE_NAME;
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    ELSE
        RETURN NEW;
    END IF;
END;
$$ LANGUAGE plpgsql;

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
        EXECUTE format(
            'CREATE TRIGGER trg_block_frozen_mutation BEFORE UPDATE OR DELETE ON harness.%I
             FOR EACH ROW EXECUTE FUNCTION harness.eval_block_frozen_mutation()', t
        );
    END LOOP;
END $$;
