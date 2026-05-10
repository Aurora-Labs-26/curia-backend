"""source URL uniqueness per user

Revision ID: 0004_source_url_unique
Revises: 0003_episode_quality
Create Date: 2026-05-10

Adds UNIQUE(user_id, url) on source so re-saving the same URL is idempotent.
The application code (api + ingest_url) checks for an existing row first and
returns that id instead of inserting a duplicate; this constraint backstops it
against race conditions.

NULL urls (legacy / placeholder rows) don't conflict — Postgres considers NULL
distinct from NULL in unique indexes by default.
"""

from alembic import op

revision = "0004_source_url_unique"
down_revision = "0003_episode_quality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop dupes if any pre-existed (we keep the oldest row per (user_id, url)).
    op.execute("""
        DELETE FROM source s
        USING source s2
        WHERE s.user_id = s2.user_id
          AND s.url = s2.url
          AND s.url IS NOT NULL
          AND s.created_at > s2.created_at
    """)
    op.execute("""
        CREATE UNIQUE INDEX source_user_url_uidx
        ON source (user_id, url)
        WHERE url IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS source_user_url_uidx")
