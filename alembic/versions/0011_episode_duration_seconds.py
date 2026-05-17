"""add duration_seconds to episode table

Revision ID: 0011_episode_duration_seconds
Revises: 0010_embedding_1536
Create Date: 2026-05-17

Stores the actual rendered audio length in seconds on the episode row so the
frontend can display an accurate progress bar without waiting for the audio
file to load and self-report its duration.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_episode_duration_seconds"
down_revision = "0010_embedding_1536"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("episode", sa.Column("duration_seconds", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("episode", "duration_seconds")
