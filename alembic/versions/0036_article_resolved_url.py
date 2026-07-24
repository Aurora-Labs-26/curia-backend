"""articles.resolved_url — decoded publisher URL alongside the Google News
redirect token (harness.articles.url stays the identity/dedup key).

Written by enrichment when the redirect decode succeeds; read for display
(real source links in the brief) and to skip re-decoding on re-enrichment.

Revision ID: 0036_article_resolved_url
Revises: 0035_brief_schema
"""

from alembic import op
import sqlalchemy as sa

revision = "0036_article_resolved_url"
down_revision = "0035_brief_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "articles",
        sa.Column("resolved_url", sa.Text(), nullable=True),
        schema="harness",
    )


def downgrade() -> None:
    op.drop_column("articles", "resolved_url", schema="harness")
