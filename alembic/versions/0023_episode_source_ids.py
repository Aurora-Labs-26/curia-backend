"""episode source_ids array column

Revision ID: 0023_episode_source_ids
Revises: 0022_user_fcm_token
Create Date: 2026-05-22

Stub migration — column was applied directly; file recreated to restore chain.
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_episode_source_ids"
down_revision = "0022_user_fcm_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE episode ADD COLUMN IF NOT EXISTS source_ids UUID[] DEFAULT '{}'
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS source_ids")
