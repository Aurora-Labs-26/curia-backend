"""Add brief preferences to users table.

revision: 0025_user_brief_preferences
down_revision: 0024_episode_audio_url
"""

from alembic import op

revision = "0025_user_brief_preferences"
down_revision = "0024_episode_audio_url"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE users
            ADD COLUMN IF NOT EXISTS interests TEXT[] DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS location_city TEXT,
            ADD COLUMN IF NOT EXISTS brief_notify_time TIME DEFAULT '09:00',
            ADD COLUMN IF NOT EXISTS brief_enabled BOOLEAN DEFAULT TRUE
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE users
            DROP COLUMN IF EXISTS interests,
            DROP COLUMN IF EXISTS location_city,
            DROP COLUMN IF EXISTS brief_notify_time,
            DROP COLUMN IF EXISTS brief_enabled
    """)
