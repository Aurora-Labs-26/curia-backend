"""users.location_country — ISO country code from the geocoder.

Stored at prefs-save alongside the canonical location_name so local-news
edition resolution uses a real country code instead of news.py's keyword
sniffing of the city string (which substring-matched "in" — "Berlin"
resolved to the India edition). Nullable: rows saved before the picker, or
users with no city, simply fall back to the legacy keyword path.

Revision ID: 0038_user_location_country
Revises: 0037_brief_tz_and_progress
"""

from alembic import op
import sqlalchemy as sa

revision = "0038_user_location_country"
down_revision = "0037_brief_tz_and_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("location_country", sa.Text(), nullable=True),
        schema="harness",
    )


def downgrade() -> None:
    op.drop_column("users", "location_country", schema="harness")
