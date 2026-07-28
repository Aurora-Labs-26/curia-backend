"""users.timezone + daily_briefs playback progress

harness.users.timezone — IANA name (e.g. "Asia/Kolkata") sent by the client at
prefs-save. Required for scheduling: users.scheduled_time is a bare `time` and
the worker's APScheduler runs UTC, so "9:00" is undefined without it. Also
defines the user-local midnight that rolls a brief out of the app's Today slot.
Defaults to UTC so existing rows and any future insert always schedule somewhere.

harness.daily_briefs play_progress / listened / last_played_at — mirrors the
episode table's columns (0016, 0017) so the client can reuse its existing
best-effort progress-reporting path for brief playback resume.

Revision ID: 0037_brief_tz_and_progress
Revises: 0036_article_resolved_url
"""

from alembic import op
import sqlalchemy as sa

revision = "0037_brief_tz_and_progress"
down_revision = "0036_article_resolved_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("timezone", sa.Text(), nullable=False, server_default="UTC"),
        schema="harness",
    )
    op.add_column(
        "daily_briefs",
        sa.Column("play_progress", sa.Float(), nullable=True, server_default=None),
        schema="harness",
    )
    op.add_column(
        "daily_briefs",
        sa.Column("listened", sa.Boolean(), nullable=False, server_default="false"),
        schema="harness",
    )
    op.add_column(
        "daily_briefs",
        sa.Column("last_played_at", sa.DateTime(timezone=True), nullable=True),
        schema="harness",
    )


def downgrade() -> None:
    op.drop_column("daily_briefs", "last_played_at", schema="harness")
    op.drop_column("daily_briefs", "listened", schema="harness")
    op.drop_column("daily_briefs", "play_progress", schema="harness")
    op.drop_column("users", "timezone", schema="harness")
