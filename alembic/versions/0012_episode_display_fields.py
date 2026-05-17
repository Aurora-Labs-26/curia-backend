"""add description, chapters, play_progress, listened to episode

Revision ID: 0012_episode_display_fields
Revises: 0011_episode_duration_seconds
Create Date: 2026-05-17

description  — the outline's 'thread' sentence, surfaced as episode tagline
chapters     — JSONB array of {id, title, start_minute} derived from outline segments
play_progress — 0.0–1.0, updated by the client during playback
listened     — true once the user has finished or explicitly marked as listened
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012_episode_display_fields"
down_revision = "0011_episode_duration_seconds"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("episode", sa.Column("description",    sa.Text(),    nullable=True))
    op.add_column("episode", sa.Column("chapters",       JSONB(),      nullable=True))
    op.add_column("episode", sa.Column("play_progress",  sa.Float(),   nullable=True))
    op.add_column("episode", sa.Column("listened",       sa.Boolean(), nullable=True, server_default="false"))


def downgrade():
    op.drop_column("episode", "description")
    op.drop_column("episode", "chapters")
    op.drop_column("episode", "play_progress")
    op.drop_column("episode", "listened")
