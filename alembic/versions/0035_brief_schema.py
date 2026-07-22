"""daily-brief core schema (harness schema, 8 tables) — ported from daily-brief-harness

Revision ID: 0035_brief_schema
Revises: 0034_tension_registry
Create Date: 2026-07-22

Squashes daily-brief-harness/db/001_schema.sql + 002_seed_topics.sql +
006 (glimpse column never created) + 014 (faithfulness memo columns, kept for
the parked eval phase) into one migration. The 12 eval/gold tables are
deliberately NOT migrated — the eval harness is parked as future analytics
("dailybrief analysis v1.md" §8, Option C as amended).

Port adaptations:
  - harness.users.id is TEXT (was uuid): it now IS the Curia user id
    (api.auth current_user_id — text, e.g. 'claude-e2e-test' or a uuid string),
    so brief prefs join Curia identity without a mapping table.
  - email column dropped (Curia's users table owns identity/email);
    display_name added (the brief greets by name).
"""
from alembic import op

revision = "0035_brief_schema"
down_revision = "0034_tension_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS harness")
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE harness.user_topic_type AS ENUM ('chosen', 'custom');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE harness.segment_type AS ENUM ('lead', 'standard', 'local');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE harness.brief_status AS ENUM ('generating', 'ready', 'failed');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.users (
            id              text PRIMARY KEY,
            display_name    text NOT NULL DEFAULT '',
            location        point NULL,
            location_name   text NOT NULL,
            scheduled_time  time NOT NULL DEFAULT '07:00',
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.topics (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name        text NOT NULL,
            beat        text NULL,
            geo_gl      text NULL,
            geo_hl      text NULL,
            is_system   boolean NOT NULL DEFAULT false
        )
    """)
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_topics_name_system
            ON harness.topics (lower(name), is_system)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.user_topics (
            id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     text NOT NULL REFERENCES harness.users (id) ON DELETE CASCADE,
            topic_id    uuid NOT NULL REFERENCES harness.topics (id) ON DELETE CASCADE,
            type        harness.user_topic_type NOT NULL,
            is_active   boolean NOT NULL DEFAULT true,
            UNIQUE (user_id, topic_id)
        )
    """)
    op.execute("""
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
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_topic_id ON harness.articles (topic_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_articles_fetched_at ON harness.articles (fetched_at)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.article_segment_cache (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            article_id      uuid NOT NULL REFERENCES harness.articles (id) ON DELETE CASCADE,
            segment_type    harness.segment_type NOT NULL,
            is_local        boolean NOT NULL DEFAULT false,
            transcript_json jsonb NOT NULL,
            mp3_url         text NOT NULL,
            duration_s      double precision NOT NULL,
            faithfulness_severity text NULL,
            faithfulness_detail   jsonb NULL,
            generated_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (article_id, segment_type, is_local),
            CONSTRAINT chk_is_local_matches_segment_type CHECK (is_local = (segment_type = 'local'))
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_article_segment_cache_local
            ON harness.article_segment_cache (is_local) WHERE is_local
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.daily_briefs (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             text NOT NULL REFERENCES harness.users (id) ON DELETE CASCADE,
            date                date NOT NULL,
            status              harness.brief_status NOT NULL DEFAULT 'generating',
            intro_mp3_url       text NULL,
            outro_mp3_url       text NULL,
            stitched_mp3_url    text NULL,
            created_at          timestamptz NOT NULL DEFAULT now(),
            UNIQUE (user_id, date)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.daily_brief_articles (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            brief_id        uuid NOT NULL REFERENCES harness.daily_briefs (id) ON DELETE CASCADE,
            article_id      uuid NOT NULL REFERENCES harness.articles (id) ON DELETE RESTRICT,
            cache_id        uuid NULL REFERENCES harness.article_segment_cache (id) ON DELETE SET NULL,
            segment_type    harness.segment_type NOT NULL,
            rank            int NOT NULL,
            cache_hit       boolean NOT NULL,
            reason          text NULL,
            UNIQUE (brief_id, segment_type, rank)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS harness.transcript_records (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         text NULL REFERENCES harness.users (id) ON DELETE SET NULL,
            title           text NOT NULL,
            display_name    text NOT NULL,
            topics_used     jsonb NOT NULL,
            segments        jsonb NOT NULL,
            note            text NULL,
            created_at      timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_transcript_records_created_at
            ON harness.transcript_records (created_at DESC)
    """)
    # seed the 7 system Beats (from 002_seed_topics.sql)
    op.execute("""
        INSERT INTO harness.topics (name, beat, geo_gl, geo_hl, is_system) VALUES
            ('Tech',             'Tech',             'US', 'en-US', true),
            ('Business',         'Business',         'IN', 'en-IN', true),
            ('World',            'World',            'US', 'en-US', true),
            ('Science & Health', 'Science & Health', 'US', 'en-US', true),
            ('Culture',          'Culture',          'IN', 'en-IN', true),
            ('Lifestyle',        'Lifestyle',        'IN', 'en-IN', true),
            ('Sports',           'Sports',           'US', 'en-US', true)
        ON CONFLICT DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS harness CASCADE")
