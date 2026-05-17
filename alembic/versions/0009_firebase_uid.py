"""add firebase_uid column to users table

Revision ID: 0009_firebase_uid
Revises: 0008_episode_overrides
Create Date: 2026-05-16

Adds a firebase_uid column to users for Firebase Auth integration.
Partial unique index ensures uniqueness only for non-null values.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009_firebase_uid"
down_revision = "0008_episode_overrides"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("firebase_uid", sa.Text(), nullable=True))
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_firebase_uid "
        "ON users (firebase_uid) WHERE firebase_uid IS NOT NULL"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_users_firebase_uid")
    op.drop_column("users", "firebase_uid")
