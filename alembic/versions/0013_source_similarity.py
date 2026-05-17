"""add source_similarity cache table

Revision ID: 0013_source_similarity
Revises: 0012_episode_display_fields
Create Date: 2026-05-18
"""

from alembic import op

revision = "0013_source_similarity"
down_revision = "0012_episode_display_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE source_similarity (
            source_a   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            source_b   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            score      FLOAT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_a, source_b)
        )
    """)
    op.execute("""
        CREATE INDEX source_similarity_b_idx ON source_similarity (source_b, source_a)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS source_similarity_b_idx")
    op.execute("DROP TABLE IF EXISTS source_similarity")
