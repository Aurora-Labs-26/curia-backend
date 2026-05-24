"""create waitlist table

Revision ID: 0026
Revises: 0025
Create Date: 2026-05-24
"""

from alembic import op
import sqlalchemy as sa

revision = "0026_waitlist"
down_revision = "0025_source_thumbnail_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "waitlist",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.Text(), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("waitlist")
