"""Add notification_log table for dedup of scheduled push notifications

Revision ID: 0027_notification_log
Revises: 0026_speaker_pair
Create Date: 2026-06-08
"""

from alembic import op

revision = "0027_notification_log"
down_revision = "0026_speaker_pair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS notification_log (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            type        TEXT NOT NULL,
            sent_date   DATE NOT NULL DEFAULT CURRENT_DATE,
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (user_id, type, sent_date)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS notification_log_user_idx ON notification_log (user_id, type, sent_date)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS notification_log")
