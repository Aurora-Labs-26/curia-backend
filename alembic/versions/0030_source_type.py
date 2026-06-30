"""add source_type column to source

Revision ID: 0030_source_type
Revises: 0029_source_og_image
Create Date: 2026-07-01
"""
from alembic import op
import sqlalchemy as sa

revision = "0030_source_type"
down_revision = "0029_source_og_image"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source",
        sa.Column("source_type", sa.Text(), nullable=False, server_default="article"),
    )


def downgrade() -> None:
    op.drop_column("source", "source_type")
