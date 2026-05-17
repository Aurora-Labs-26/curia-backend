"""add 1536-dim embedding columns for OpenAI/Cohere providers

Revision ID: 0010_embedding_1536
Revises: 0009_firebase_uid
Create Date: 2026-05-16

Adds embedding_1536 vector(1536) columns to source_embedding and
source_primitive_embedding so providers with 1536-dim outputs
(OpenAI text-embedding-3-small, Cohere embed-v4) can be used
alongside the existing 1024-dim columns (Voyage, Mistral).

The active column is determined at runtime by the configured
embedder's dimension in config/models.yaml.
"""

from alembic import op

revision = "0010_embedding_1536"
down_revision = "0009_firebase_uid"
branch_labels = None
depends_on = None


def upgrade():
    # source_embedding: add 1536-dim column + HNSW index
    op.execute("ALTER TABLE source_embedding ADD COLUMN IF NOT EXISTS embedding_1536 vector(1536)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS source_embedding_hnsw_1536_idx
            ON source_embedding USING hnsw (embedding_1536 vector_cosine_ops)
    """)

    # source_primitive_embedding: add 1536-dim column + HNSW index
    op.execute("ALTER TABLE source_primitive_embedding ADD COLUMN IF NOT EXISTS embedding_1536 vector(1536)")
    op.execute("""
        CREATE INDEX IF NOT EXISTS source_primitive_embedding_hnsw_1536_idx
            ON source_primitive_embedding USING hnsw (embedding_1536 vector_cosine_ops)
    """)


def downgrade():
    op.execute("DROP INDEX IF EXISTS source_embedding_hnsw_1536_idx")
    op.execute("ALTER TABLE source_embedding DROP COLUMN IF EXISTS embedding_1536")
    op.execute("DROP INDEX IF EXISTS source_primitive_embedding_hnsw_1536_idx")
    op.execute("ALTER TABLE source_primitive_embedding DROP COLUMN IF EXISTS embedding_1536")
