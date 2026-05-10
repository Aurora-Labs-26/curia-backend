"""initial schema — ported from SurrealDB to Postgres+pgvector

Revision ID: 0001_init
Revises:
Create Date: 2026-05-09

What this contains:
  - pgcrypto + vector extensions
  - 7 tables mirroring the previous Surreal schema
    (source, source_insight, source_embedding, source_primitive_embedding,
     show_idea, episode, covered_topic, host_memory)
  - HNSW indexes on the two embedding tables for cosine similarity
  - vector dimension = 1024 (Voyage voyage-3 default)

Notes vs old schema:
  - Surreal record-IDs ("source:abc") replaced with proper UUID PRIMARY KEYs
  - SCHEMALESS tables replaced with explicit columns plus a `data JSONB` for evolution
  - `time::now()` replaced with `now()`
  - `user_id` kept as TEXT (not UUID) for v1 to avoid breaking the hardcoded "default" user
    — promote to UUID FK when the `users` table arrives.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── extensions ─────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ── source ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE source (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL DEFAULT 'default',
            url         TEXT,
            title       TEXT,
            full_text   TEXT,
            pool        TEXT DEFAULT 'user',
            data        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX source_user_idx ON source (user_id, created_at DESC)")
    op.execute("CREATE INDEX source_url_idx  ON source (url)")

    # ── source_insight (one row per (source, insight_type)) ────────────────
    op.execute("""
        CREATE TABLE source_insight (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id       UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            insight_type    TEXT NOT NULL,
            content         TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX source_insight_source_idx ON source_insight (source_id, insight_type)")

    # ── source_embedding (chunk-level, 1024-dim Voyage) ────────────────────
    op.execute("""
        CREATE TABLE source_embedding (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            chunk_text  TEXT NOT NULL,
            embedding   vector(1024),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX source_embedding_hnsw_idx
            ON source_embedding USING hnsw (embedding vector_cosine_ops)
    """)
    op.execute("CREATE INDEX source_embedding_source_idx ON source_embedding (source_id, chunk_index)")

    # ── source_primitive_embedding (one row per source) ────────────────────
    op.execute("""
        CREATE TABLE source_primitive_embedding (
            source_id   UUID PRIMARY KEY REFERENCES source(id) ON DELETE CASCADE,
            embedding   vector(1024),
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX source_primitive_embedding_hnsw_idx
            ON source_primitive_embedding USING hnsw (embedding vector_cosine_ops)
    """)

    # ── show_idea ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE show_idea (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             TEXT NOT NULL DEFAULT 'default',
            angle               TEXT NOT NULL,
            idea_type           TEXT NOT NULL,
            format              TEXT NOT NULL,
            source_ids          UUID[] NOT NULL DEFAULT '{}',
            generated           BOOLEAN NOT NULL DEFAULT FALSE,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX show_idea_user_idx ON show_idea (user_id, created_at DESC)")

    # ── episode ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE episode (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id             TEXT NOT NULL DEFAULT 'default',
            show_name           TEXT,
            title               TEXT,
            transcript          JSONB,
            outline             JSONB,
            audio_path          TEXT,
            source_ids          UUID[] NOT NULL DEFAULT '{}',
            editorial_direction TEXT,
            data                JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX episode_user_idx ON episode (user_id, created_at DESC)")

    # ── covered_topic ──────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE covered_topic (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL DEFAULT 'default',
            show_name   TEXT,
            episode_id  UUID REFERENCES episode(id) ON DELETE SET NULL,
            topics      TEXT,
            source_ids  UUID[] NOT NULL DEFAULT '{}',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX covered_topic_user_idx ON covered_topic (user_id, created_at DESC)")

    # ── host_memory ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE host_memory (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL DEFAULT 'default',
            host        TEXT NOT NULL,
            memory      JSONB NOT NULL DEFAULT '{}'::jsonb,
            updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, host)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS host_memory CASCADE")
    op.execute("DROP TABLE IF EXISTS covered_topic CASCADE")
    op.execute("DROP TABLE IF EXISTS episode CASCADE")
    op.execute("DROP TABLE IF EXISTS show_idea CASCADE")
    op.execute("DROP TABLE IF EXISTS source_primitive_embedding CASCADE")
    op.execute("DROP TABLE IF EXISTS source_embedding CASCADE")
    op.execute("DROP TABLE IF EXISTS source_insight CASCADE")
    op.execute("DROP TABLE IF EXISTS source CASCADE")
