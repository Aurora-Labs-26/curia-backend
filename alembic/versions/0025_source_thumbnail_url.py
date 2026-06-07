"""Add thumbnail_url to source table

Revision ID: 0025_source_thumbnail_url
Revises: 0024_episode_audio_url
Create Date: 2026-06-07
"""

from alembic import op

revision = "0025_source_thumbnail_url"
down_revision = "0024_episode_audio_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source ADD COLUMN IF NOT EXISTS thumbnail_url TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE source DROP COLUMN IF EXISTS thumbnail_url")
