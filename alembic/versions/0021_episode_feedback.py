"""Add feedback column to episode

Revision ID: 0021_episode_feedback
Revises: 0020_source_hidden
Create Date: 2026-05-20
"""

from alembic import op

revision = "0021_episode_feedback"
down_revision = "0020_source_hidden"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS feedback JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS feedback")
