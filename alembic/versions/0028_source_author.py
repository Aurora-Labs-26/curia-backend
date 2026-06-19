"""add author column to source

Revision ID: 0028_source_author
Revises: 0027_notification_log
Create Date: 2026-06-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0028_source_author"
down_revision = "0027_notification_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source", sa.Column("author", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "author")
