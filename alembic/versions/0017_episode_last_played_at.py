"""add last_played_at to episode

Revision ID: 0017_episode_last_played_at
Revises: 0016_episode_playback_progress
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0017_episode_last_played_at"
down_revision = ("0016_episode_playback_progress", "0016_job_priority")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode", sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("episode", "last_played_at")
