"""add topics JSONB column to source + domain_pins table

Revision ID: 0031_source_topics
Revises: 0030_source_type
Create Date: 2026-07-13

topics — per-source topic-bucket envelope (see "topics v1.md"):
  {"version": 1, "tags": [{"tier1": "...", "tier2": ["..."], "src": "domain|section|learned|llm"}]}
  NULL = not yet classified; {"tags": []} = classified, fits no bucket.

domain_pins — learned deterministic pins (hostname or "yt:<channel>" → Tier-1),
promoted from LLM consensus by scripts/promote_pins.py (Phase 3). Seed pins live
in code (core/taxonomy/buckets.py); this table holds only learned/ops-added rows.
"""
from alembic import op
import sqlalchemy as sa

revision = "0031_source_topics"
down_revision = "0030_source_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source",
        sa.Column("topics", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    # GIN index so sources can be filtered by tag via @> containment:
    #   WHERE topics -> 'tags' @> '[{"tier1": "Sports"}]'
    op.execute("CREATE INDEX source_topics_gin ON source USING gin (topics)")

    op.execute("""
        CREATE TABLE domain_pins (
            hostname      TEXT PRIMARY KEY,
            tier1         TEXT NOT NULL,
            tier2         TEXT,
            origin        TEXT NOT NULL DEFAULT 'learned',
            sample_count  INTEGER,
            agreement     REAL,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS domain_pins")
    op.execute("DROP INDEX IF EXISTS source_topics_gin")
    op.drop_column("source", "topics")
