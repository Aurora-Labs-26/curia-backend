"""Add fcm_token to users table

Revision ID: 0022_user_fcm_token
Revises: 0021_episode_feedback
Create Date: 2026-05-20
"""

from alembic import op

revision = "0022_user_fcm_token"
down_revision = "0021_episode_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS fcm_token TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS fcm_token")
