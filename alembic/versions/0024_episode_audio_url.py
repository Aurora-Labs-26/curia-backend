"""episode audio_url column for R2/S3 storage

Revision ID: 0024_episode_audio_url
Revises: 0023_episode_source_ids
Create Date: 2026-05-22

Stub migration — column was applied directly; file recreated to restore chain.
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_episode_audio_url"
down_revision = "0023_episode_source_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE episode ADD COLUMN IF NOT EXISTS audio_url TEXT
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS audio_url")
