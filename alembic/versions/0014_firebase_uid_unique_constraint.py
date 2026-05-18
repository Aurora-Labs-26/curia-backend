"""add unique constraint on firebase_uid for ON CONFLICT support

Revision ID: 0014_firebase_uid_unique_constraint
Revises: 0013_source_similarity
Create Date: 2026-05-18

The existing partial index (WHERE firebase_uid IS NOT NULL) cannot be used
as an ON CONFLICT target. Replace it with a full unique constraint so the
upsert in api/auth.py works correctly.
"""

from alembic import op

revision = "0014_fb_uid_constraint"
down_revision = "0013_source_similarity"
branch_labels = None
depends_on = None


def upgrade():
    # Drop the partial index added in 0009
    op.execute("DROP INDEX IF EXISTS ix_users_firebase_uid")
    # Add a proper unique constraint (nulls are still allowed — each null is distinct)
    op.create_unique_constraint("uq_users_firebase_uid", "users", ["firebase_uid"])


def downgrade():
    op.drop_constraint("uq_users_firebase_uid", "users", type_="unique")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_firebase_uid "
        "ON users (firebase_uid) WHERE firebase_uid IS NOT NULL"
    )
