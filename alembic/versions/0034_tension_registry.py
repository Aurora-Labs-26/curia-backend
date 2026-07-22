"""tension registry + source links + episode selection_plan (Connect)

Revision ID: 0034_tension_registry
Revises: 0033_episode_bgm_plan
Create Date: 2026-07-22

The Connect feature ("companion selection research v1.md", frozen v3 design):
- tension        — canonical, domain-free contested questions ("X vs Y"), one row
                   per distinct question; both embedding columns mirror the
                   source_embedding 1024/1536 dual-column scheme (migration 0010)
- source_tension — which sources participate in which tension, with the AUTHOR'S
                   polarity (side_a | side_b | neutral) and extraction confidence
- episode.selection_plan — the Connect cast + roles + angle (QA artifact, same
                   pattern as bgm_plan)

Companion selection is then pure SQL: antagonist = same tension_id + opposite
polarity; wildcard = same tension_id + different topic bucket.
"""
from alembic import op

revision = "0034_tension_registry"
down_revision = "0033_episode_bgm_plan"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE tension (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            canonical    TEXT NOT NULL,
            embedding    vector(1024),
            embedding_1536 vector(1536),
            source_count INTEGER NOT NULL DEFAULT 0,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX tension_embedding_1536_hnsw
            ON tension USING hnsw (embedding_1536 vector_cosine_ops)
    """)
    op.execute("""
        CREATE TABLE source_tension (
            source_id   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            tension_id  UUID NOT NULL REFERENCES tension(id) ON DELETE CASCADE,
            polarity    TEXT NOT NULL DEFAULT 'neutral',
            confidence  TEXT NOT NULL DEFAULT 'medium',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (source_id, tension_id)
        )
    """)
    op.execute("CREATE INDEX source_tension_tension_idx ON source_tension (tension_id)")
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS selection_plan JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS selection_plan")
    op.execute("DROP TABLE IF EXISTS source_tension")
    op.execute("DROP TABLE IF EXISTS tension")
