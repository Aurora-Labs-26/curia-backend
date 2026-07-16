-- Curia Daily Brief — eval-pipeline instrumentation schema (M0)
--
-- Pure logging substrate for the LLM-eval harness (see
-- daily-brief-eval-buildplan.md). Lives in the same `harness` schema as the
-- live cache tables (001_schema.sql) — additive only, never read by the live
-- Pre-Opt / Generate-Brief pipeline itself. Written by
-- app/services/eval_logging_service.py, which is the only code that touches
-- these tables.
--
-- Idempotent: safe to re-run via scripts/bootstrap_db.py, same convention as
-- 001_schema.sql.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE harness.eval_run_kind AS ENUM ('preopt', 'generate_brief', 'regenerate_bookends');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE harness.eval_run_status AS ENUM ('running', 'completed', 'failed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE harness.eval_check_type AS ENUM ('deterministic', 'llm_judge');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE harness.eval_result_status AS ENUM ('pass', 'fail', 'flag');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- EVAL_RUNS — one row per pipeline invocation (a Pre-Opt call for one topic,
-- a Generate-Brief call for one user/date, or a bookends-only regenerate).
-- `topic_id`/`user_id`/`brief_id` are each nullable because which one
-- applies depends on `kind` — enforced in application code, not a DB CHECK
-- (same pattern as harness.user_topics' type/is_system invariant).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    kind            harness.eval_run_kind NOT NULL,
    topic_id        uuid NULL REFERENCES harness.topics (id) ON DELETE SET NULL,
    user_id         uuid NULL REFERENCES harness.users (id) ON DELETE SET NULL,
    brief_id        uuid NULL REFERENCES harness.daily_briefs (id) ON DELETE SET NULL,
    status          harness.eval_run_status NOT NULL DEFAULT 'running',
    error           text NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz NULL
);

CREATE INDEX IF NOT EXISTS ix_eval_runs_kind ON harness.eval_runs (kind);

-- ---------------------------------------------------------------------------
-- EVAL_FETCHED_ARTICLES — immutable-per-run snapshot of every raw article a
-- run saw, before ranking/curation. `full_text`/`content_fetched`/
-- `used_alternate_source` start NULL and get backfilled via
-- UPDATE ... WHERE (run_id, url) once the content-fetch stage runs for the
-- curated subset — this is the ground truth for later faithfulness checks.
-- `source_kind` distinguishes a fresh RSS fetch from a pool entry reused
-- from Pre-Opt's cache (Generate-Brief's chosen-topic reuse path).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_fetched_articles (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    url                     text NOT NULL,
    title                   text NOT NULL,
    description             text NULL,
    source                  text NULL,
    published_date          text NULL,
    topic_tag               text NULL,
    source_kind             text NOT NULL,
    full_text               text NULL,
    content_fetched         boolean NULL,
    used_alternate_source   boolean NULL,
    fetched_at              timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, url)
);

-- ---------------------------------------------------------------------------
-- EVAL_RANKING_OUTPUT — the local ranker's full scored breakdown for every
-- article it considered (included and excluded, local and non-local) — this
-- is computed by scoring_service.rank_articles on every run today and
-- currently discarded entirely by both preopt_runner.py and
-- user_brief_runner.py. Keyed by (run_id, url) rather than article_id since
-- most ranked articles never get an article_id (only the curated top 4-5
-- ever go through cache_service.upsert_article).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_ranking_output (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    url             text NOT NULL,
    title           text NOT NULL,
    is_local        boolean NOT NULL DEFAULT false,
    included        boolean NOT NULL,
    rank            int NULL,
    composite_score double precision NULL,
    scores_json     jsonb NULL,
    cluster_size    int NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_ranking_output_run_id ON harness.eval_ranking_output (run_id);

-- ---------------------------------------------------------------------------
-- EVAL_SEGMENT_TRANSCRIPTS — append-only log of every segment transcript
-- produced or reused in a run, distinct from harness.article_segment_cache
-- (which is a mutable upsert-by-key cache, not a per-run history). Logged
-- for cache hits too (cache_hit=true rows just echo the cached text, with
-- no latency/token/model data).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_segment_transcripts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    article_id      uuid NULL REFERENCES harness.articles (id) ON DELETE SET NULL,
    segment_type    harness.segment_type NOT NULL,
    text            text NOT NULL,
    word_count      int NULL,
    cache_hit       boolean NOT NULL,
    model           text NULL,
    input_tokens    int NULL,
    output_tokens   int NULL,
    latency_ms      int NULL,
    simulated       boolean NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_segment_transcripts_run_id ON harness.eval_segment_transcripts (run_id);

-- ---------------------------------------------------------------------------
-- EVAL_META_SEGMENTS — one row per run for Intro/Outro (both
-- always come from a single LLM call sharing one set of inputs, so one row
-- avoids duplicating `inputs_used` twice). `inputs_used` snapshots
-- display_name/location_name/weather/local_time/ranked-order at call time.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_meta_segments (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    intro_text      text NOT NULL,
    outro_text      text NOT NULL,
    inputs_used     jsonb NOT NULL,
    model           text NULL,
    input_tokens    int NULL,
    output_tokens   int NULL,
    latency_ms      int NULL,
    simulated       boolean NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_meta_segments_run_id ON harness.eval_meta_segments (run_id);

-- ---------------------------------------------------------------------------
-- EVAL_RESULTS — schema only in M0; no code writes to this table yet. M1's
-- deterministic validators and M3's LLM judges will insert here per check.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.eval_results (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          uuid NOT NULL REFERENCES harness.eval_runs (id) ON DELETE CASCADE,
    check_name      text NOT NULL,
    check_type      harness.eval_check_type NOT NULL,
    result_status   harness.eval_result_status NULL,
    result_score    double precision NULL,
    detail          text NULL,
    judge_model     text NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_eval_results_run_id ON harness.eval_results (run_id);
