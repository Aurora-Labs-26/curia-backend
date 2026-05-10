"""users + jobs + status/error columns

Revision ID: 0002_users_jobs_and_status
Revises: 0001_init
Create Date: 2026-05-09

Adds the substrate for the user-reactive web service:
  - users table (auth: api_token + user_kb JSONB)
  - jobs table (Postgres-backed queue, SELECT FOR UPDATE SKIP LOCKED)
  - source.status / source.error
  - episode.status / episode.error
  - generation_run helper view (a "queued" episode IS a generation_run; status='ready' = it's a finished episode)

Why user_id stays TEXT (not UUID FK):
  We seed a 'default' user with id='default' (literal string) so existing rows
  with user_id='default' continue to resolve. Real new users get UUIDs as TEXT.
  Promote to UUID FK in a later migration once 'default' is fully retired.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002_users_jobs_and_status"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ──────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE users (
            id          TEXT PRIMARY KEY,
            email       TEXT UNIQUE,
            name        TEXT,
            api_token   TEXT UNIQUE,
            user_kb     JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Seed the 'default' user so existing rows (user_id='default') stay valid.
    # The api_token here is replaced via scripts/create_user.py for any real account.
    op.execute("""
        INSERT INTO users (id, name, api_token)
        VALUES ('default', 'default', 'demo-replace-me')
        ON CONFLICT (id) DO NOTHING
    """)
    op.execute("CREATE INDEX users_api_token_idx ON users (api_token)")

    # ── jobs (Postgres-backed queue) ───────────────────────────────────────
    op.execute("""
        CREATE TABLE jobs (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            type            TEXT NOT NULL,
            payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
            status          TEXT NOT NULL DEFAULT 'queued',
            attempts        INT  NOT NULL DEFAULT 0,
            max_attempts    INT  NOT NULL DEFAULT 3,
            last_error      TEXT,
            locked_at       TIMESTAMPTZ,
            locked_by       TEXT,
            user_id         TEXT,
            correlation_id  UUID,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # Partial index: only need to scan queued+running rows for dispatch / cleanup
    op.execute("""
        CREATE INDEX jobs_dispatch_idx ON jobs (status, created_at)
        WHERE status IN ('queued', 'running')
    """)
    op.execute("CREATE INDEX jobs_user_idx ON jobs (user_id, created_at DESC)")

    # ── source: status + error ─────────────────────────────────────────────
    op.execute("""
        ALTER TABLE source
            ADD COLUMN status TEXT NOT NULL DEFAULT 'ready',
            ADD COLUMN error  TEXT
    """)
    # `ready` is the right backfill default — pre-existing rows finished ingest under the
    # old synchronous model; new rows inserted by the API start at 'queued'.
    op.execute("CREATE INDEX source_status_idx ON source (user_id, status, created_at DESC)")

    # ── episode: status + error ───────────────────────────────────────────
    # `episode` table doubles as the generation_run record:
    #   queued | selecting | outlining | transcribing | synthesizing | ready | failed
    op.execute("""
        ALTER TABLE episode
            ADD COLUMN status      TEXT NOT NULL DEFAULT 'ready',
            ADD COLUMN error       TEXT,
            ADD COLUMN show_idea_id UUID REFERENCES show_idea(id) ON DELETE SET NULL,
            ADD COLUMN dedup_key   TEXT
    """)
    op.execute("CREATE INDEX episode_status_idx ON episode (user_id, status, created_at DESC)")
    op.execute("CREATE UNIQUE INDEX episode_dedup_idx ON episode (dedup_key) WHERE dedup_key IS NOT NULL")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS episode_dedup_idx")
    op.execute("DROP INDEX IF EXISTS episode_status_idx")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS dedup_key")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS show_idea_id")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS error")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS status")

    op.execute("DROP INDEX IF EXISTS source_status_idx")
    op.execute("ALTER TABLE source DROP COLUMN IF EXISTS error")
    op.execute("ALTER TABLE source DROP COLUMN IF EXISTS status")

    op.execute("DROP INDEX IF EXISTS jobs_user_idx")
    op.execute("DROP INDEX IF EXISTS jobs_dispatch_idx")
    op.execute("DROP TABLE IF EXISTS jobs")

    op.execute("DROP INDEX IF EXISTS users_api_token_idx")
    op.execute("DROP TABLE IF EXISTS users")
