"""add source_image table for scraped images stored in blob storage

Revision ID: 0023_source_images
Revises: 0022_user_fcm_token
Create Date: 2026-05-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0023_source_images"
down_revision = "0022_user_fcm_token"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS source_image (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            source_id   UUID NOT NULL REFERENCES source(id) ON DELETE CASCADE,
            original_url TEXT NOT NULL,
            blob_url    TEXT NOT NULL,
            alt_text    TEXT DEFAULT '',
            content_type TEXT DEFAULT 'image/jpeg',
            size_bytes  INTEGER DEFAULT 0,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_source_image_source_id
        ON source_image(source_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS source_image")
