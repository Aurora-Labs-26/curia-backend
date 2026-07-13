"""add clean_text column to source

Revision ID: 0032_source_clean_text
Revises: 0031_source_topics
Create Date: 2026-07-13

clean_text — rule-based prettified version of full_text (core/scraper/prettify.py):
encoding fixes, boilerplate stripped, duplicate paragraphs removed. This is what
every LLM consumer reads (transformations, embeddings, topics text-signal);
full_text stays raw for audit. NULL = not yet prettified (computed on next
process_source run).
"""
from alembic import op
import sqlalchemy as sa

revision = "0032_source_clean_text"
down_revision = "0031_source_topics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source", sa.Column("clean_text", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "clean_text")
