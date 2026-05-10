"""drop unused host_memory table

Revision ID: 0007_drop_host_memory
Revises: 0006_optimization_tables
Create Date: 2026-05-10

The host_memory table was defined in the initial Surreal schema port but never
read or written by the active pipeline. Per-user state lives in users.user_kb
(KB) and (when shipped) companion_state. Dropping it to reduce confusion.

If you need it later: re-add via a new migration, no data is lost since nothing
was ever written.
"""

from alembic import op

revision = "0007_drop_host_memory"
down_revision = "0006_optimization_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS host_memory")


def downgrade() -> None:
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
