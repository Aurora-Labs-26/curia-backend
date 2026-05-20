"""create source_similarity table (idempotent — 0013 was a no-op stub)

Revision ID: 0018_create_source_similarity
Revises: 0017_episode_last_played_at
Create Date: 2026-05-20

The 0013_source_similarity migration is a no-op stub (the table was applied
directly on the original dev DB but never created elsewhere). The pulled
idea_generator reads/writes a `source_similarity` cache table, so this
migration actually creates it. Uses IF NOT EXISTS so it is safe on databases
where the table already exists.
"""

from alembic import op

revision = "0018_create_source_similarity"
down_revision = "0017_episode_last_played_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS source_similarity (
            source_a UUID NOT NULL,
            source_b UUID NOT NULL,
            score DOUBLE PRECISION NOT NULL,
            PRIMARY KEY (source_a, source_b)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_similarity")
