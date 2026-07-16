-- Curia Daily Brief — harness cache schema
--
-- Lives in its own `harness` schema, deliberately NOT `public`, so it can
-- never collide by bare table name with the existing, unrelated
-- `public.daily_briefs` table that app/services/db_service.py (production
-- batch runner path) already queries in whatever database DATABASE_URL
-- points to. Nothing in this file touches db_service.py's table.
--
-- Idempotent: safe to re-run against an already-initialized database (via
-- scripts/bootstrap_db.py), not just relying on the postgres image's
-- run-once-on-empty-volume docker-entrypoint-initdb.d behavior.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS harness;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

DO $$ BEGIN
    CREATE TYPE harness.user_topic_type AS ENUM ('chosen', 'custom');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE harness.segment_type AS ENUM ('lead', 'standard', 'local');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    CREATE TYPE harness.brief_status AS ENUM ('generating', 'ready', 'failed');
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

-- ---------------------------------------------------------------------------
-- USERS
-- ---------------------------------------------------------------------------
-- `location_name` is a deviation from the original ERD (which only had
-- `location point`) — the news-fetch pipeline needs a queryable place NAME
-- (e.g. "Mumbai"), not lat/long coordinates. `location` (point) is kept for
-- future precise use (e.g. weather lookups) but is nullable and not read by
-- any pipeline code this iteration.
CREATE TABLE IF NOT EXISTS harness.users (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           text NOT NULL,
    location        point NULL,
    location_name   text NOT NULL,
    scheduled_time  time NOT NULL DEFAULT '07:00',
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (email)
);

-- ---------------------------------------------------------------------------
-- TOPICS
-- ---------------------------------------------------------------------------
-- `is_system=true` rows are the 7 seeded Beats (see db/002_seed_topics.sql,
-- matching app/services/news_service.py's BEATS dict). `is_system=false` rows
-- are user-typed custom topics, inserted on demand by cache_service.
-- `beat` mirrors the exact news_service.BEATS key for system topics.
-- `geo_gl`/`geo_hl` are informational snapshots only — the actual fetch
-- always re-derives live geo params from news_service.BEATS/BEAT_GEO_MODE by
-- `beat` name (a "mixed" mode beat needs TWO geo editions; a single column
-- pair here can't represent that).
CREATE TABLE IF NOT EXISTS harness.topics (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    beat        text NULL,
    geo_gl      text NULL,
    geo_hl      text NULL,
    is_system   boolean NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_topics_name_system
    ON harness.topics (lower(name), is_system);

-- ---------------------------------------------------------------------------
-- USER_TOPICS (junction)
-- ---------------------------------------------------------------------------
-- Invariant enforced in application code (cache_service), not the DB (a CHECK
-- constraint can't reference another table): type='chosen' implies its topic
-- has is_system=true; type='custom' implies is_system=false.
CREATE TABLE IF NOT EXISTS harness.user_topics (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES harness.users (id) ON DELETE CASCADE,
    topic_id    uuid NOT NULL REFERENCES harness.topics (id) ON DELETE CASCADE,
    type        harness.user_topic_type NOT NULL,
    is_active   boolean NOT NULL DEFAULT true,
    UNIQUE (user_id, topic_id)
);

-- ---------------------------------------------------------------------------
-- ARTICLES
-- ---------------------------------------------------------------------------
-- `normalized_url` is computed in Python (cache_service.normalize_url) before
-- insert, not a SQL generated column — keeps normalization logic in one
-- testable place. The UNIQUE constraint on it is what lets the SAME
-- real-world article, independently discovered by Pre-Opt's topic fetch and
-- by a per-user custom/local fetch, resolve to the SAME row — the entire
-- mechanism cross-context cache hits depend on.
-- `topic_id` is nullable — a deviation from the ERD: local articles don't
-- belong to one of the 7 topical Beats, and forcing a fake "Local" topic row
-- would conflate subject (topic) with locality (already captured by
-- segment_type/is_local on article_segment_cache).
-- `simhash` is a placeholder (cheap hash of the normalized title), not a real
-- simhash implementation — not used for any dedup/cache decision this
-- iteration; dedup is by normalized_url only.
-- `sig_score`/`topic_sim_score`/`composite_score` are a denormalized snapshot
-- of the LAST ranking run that touched this article (overwritten on every
-- upsert) — informational/debug only, never read for cache-hit decisions.
CREATE TABLE IF NOT EXISTS harness.articles (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    url                 text NOT NULL,
    normalized_url      text NOT NULL,
    title               text NOT NULL,
    topic_id            uuid NULL REFERENCES harness.topics (id) ON DELETE SET NULL,
    simhash             bigint NULL,
    sig_score           double precision NULL,
    topic_sim_score     double precision NULL,
    composite_score     double precision NULL,
    fetched_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (normalized_url)
);

CREATE INDEX IF NOT EXISTS ix_articles_topic_id ON harness.articles (topic_id);
CREATE INDEX IF NOT EXISTS ix_articles_fetched_at ON harness.articles (fetched_at);

-- ---------------------------------------------------------------------------
-- ARTICLE_SEGMENT_CACHE — the key cache table
-- ---------------------------------------------------------------------------
-- UNIQUE(article_id, segment_type, is_local) is the optimization cache key.
-- `is_local` is CONFIRMED to be a redundant flag mirroring
-- segment_type='local' (kept only for fast filtering/indexing), enforced by
-- the CHECK constraint below — not an independent dimension.
-- `mp3_url`/`duration_s` are placeholders this iteration (TTS is never
-- actually called) — see cache_service.placeholder_mp3_url /
-- estimate_duration_s.
CREATE TABLE IF NOT EXISTS harness.article_segment_cache (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    article_id      uuid NOT NULL REFERENCES harness.articles (id) ON DELETE CASCADE,
    segment_type    harness.segment_type NOT NULL,
    is_local        boolean NOT NULL DEFAULT false,
    transcript_json jsonb NOT NULL,
    mp3_url         text NOT NULL,
    duration_s      double precision NOT NULL,
    generated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (article_id, segment_type, is_local),
    CONSTRAINT chk_is_local_matches_segment_type CHECK (is_local = (segment_type = 'local'))
);

CREATE INDEX IF NOT EXISTS ix_article_segment_cache_local
    ON harness.article_segment_cache (is_local)
    WHERE is_local;

-- ---------------------------------------------------------------------------
-- DAILY_BRIEFS
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS harness.daily_briefs (
    id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             uuid NOT NULL REFERENCES harness.users (id) ON DELETE CASCADE,
    date                date NOT NULL,
    status              harness.brief_status NOT NULL DEFAULT 'generating',
    intro_mp3_url       text NULL,
    outro_mp3_url       text NULL,
    stitched_mp3_url    text NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, date)
);

-- ---------------------------------------------------------------------------
-- DAILY_BRIEF_ARTICLES (junction)
-- ---------------------------------------------------------------------------
-- `cache_id` is nullable: back-filled after a cache MISS resolves (matches
-- the original ERD note verbatim). `rank` disambiguates standard-1/2/3 since
-- article_segment_cache only tracks segment_type generically, not position —
-- position is purely a per-brief stitching-order concept.
CREATE TABLE IF NOT EXISTS harness.daily_brief_articles (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    brief_id        uuid NOT NULL REFERENCES harness.daily_briefs (id) ON DELETE CASCADE,
    article_id      uuid NOT NULL REFERENCES harness.articles (id) ON DELETE RESTRICT,
    cache_id        uuid NULL REFERENCES harness.article_segment_cache (id) ON DELETE SET NULL,
    segment_type    harness.segment_type NOT NULL,
    rank            int NOT NULL,
    cache_hit       boolean NOT NULL,
    UNIQUE (brief_id, segment_type, rank)
);

-- `reason` is the Step 3 score/curate LLM's editorial justification for
-- picking this article, persisted so a later regenerate-only Step 7 call
-- (see user_brief_runner.regenerate_bookends) can rebuild the real
-- `title, reason` bookend input without re-running the ranking step.
ALTER TABLE harness.daily_brief_articles ADD COLUMN IF NOT EXISTS reason text NULL;

-- ---------------------------------------------------------------------------
-- TRANSCRIPT_RECORDS — immutable open-coding archive
-- ---------------------------------------------------------------------------
-- One row per completed end-to-end "Generate Brief" call (see
-- user_brief_runner.generate_brief_for_user / _regenerate_bookends_for_brief),
-- backing the harness UI's "Open Coding" tab. Deliberately NOT keyed by
-- (user_id, date) the way daily_briefs is — every generation (including a
-- bookends-only regenerate, which always produces fresh intro/outro
-- text) is its own distinct research artifact worth its own notes, so
-- repeated runs for the same user/date insert new rows rather than
-- overwriting.
--
-- display_name/topics_used/segments are denormalized snapshots captured at
-- generation time, not FK-joined live — this is a research archive, so a
-- later "Clear All Users" or a topic re-link must never silently alter or
-- blank out an already-recorded transcript. user_id is kept for optional
-- traceability only (ON DELETE SET NULL — deleting a user must not delete
-- their past transcript records).
CREATE TABLE IF NOT EXISTS harness.transcript_records (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NULL REFERENCES harness.users (id) ON DELETE SET NULL,
    title           text NOT NULL,
    display_name    text NOT NULL,
    topics_used     jsonb NOT NULL,
    segments        jsonb NOT NULL,
    note            text NULL,
    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_transcript_records_created_at
    ON harness.transcript_records (created_at DESC);
