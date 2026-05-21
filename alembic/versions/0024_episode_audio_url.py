"""add audio_url column to episode for cloud storage (R2/S3)

Revision ID: 0024_episode_audio_url
Revises: 0023_source_images
Create Date: 2026-05-21
"""

from alembic import op
import sqlalchemy as sa

revision = "0024_episode_audio_url"
down_revision = "0023_source_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode", sa.Column("audio_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("episode", "audio_url")
