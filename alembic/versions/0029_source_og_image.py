"""add og_image column to source

Revision ID: 0029_source_og_image
Revises: 0028_source_author
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa

revision = "0029_source_og_image"
down_revision = "0028_source_author"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source", sa.Column("og_image", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "og_image")
