"""add play_progress and listened to episode

Revision ID: 0016_episode_playback_progress
Revises: 0015_episode_tts_timings
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_episode_playback_progress"
down_revision = "0015_episode_tts_timings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("episode", sa.Column("play_progress", sa.Float(), nullable=True, server_default=None))
    op.add_column("episode", sa.Column("listened", sa.Boolean(), nullable=False, server_default="false"))


def downgrade() -> None:
    op.drop_column("episode", "listened")
    op.drop_column("episode", "play_progress")
