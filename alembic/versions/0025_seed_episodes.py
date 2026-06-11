"""0025_seed_episodes

Add is_seed flag to source and episode tables, and a seed_url registry table.

Revision ID: 0025
Revises: 0024
"""

from alembic import op
import sqlalchemy as sa

revision = "0025_seed_episodes"
down_revision = "0024_episode_audio_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Flag on source — seed rows are shared, not per-user-generated
    op.execute("ALTER TABLE source ADD COLUMN IF NOT EXISTS is_seed BOOLEAN NOT NULL DEFAULT FALSE")
    # Flag on episode — seed episodes are pre-baked and served to new users
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS is_seed BOOLEAN NOT NULL DEFAULT FALSE")
    # Registry of known seed URLs — used for short-circuit detection at ingest time
    op.execute("""
        CREATE TABLE IF NOT EXISTS seed_url (
            url         TEXT PRIMARY KEY,
            source_id   UUID REFERENCES source(id) ON DELETE SET NULL,
            episode_id  UUID REFERENCES episode(id) ON DELETE SET NULL,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS seed_url")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS is_seed")
    op.execute("ALTER TABLE source DROP COLUMN IF EXISTS is_seed")
