"""source similarity — stub migration (applied directly, file was missing)

Revision ID: 0013_source_similarity
Revises: 0010_embedding_1536
Create Date: 2026-05-18

This migration was applied to the database directly but the file was
missing from the repo, causing Alembic to error on chain resolution.
This stub re-establishes the chain so Alembic tools work again.
The upgrade/downgrade are no-ops because the schema changes were
already applied.
"""

from alembic import op

revision = "0013_source_similarity"
down_revision = "0012_episode_display_fields"
branch_labels = None
depends_on = None


def upgrade():
    pass  # already applied directly


def downgrade():
    pass
