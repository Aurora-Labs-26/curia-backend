"""Create daily_briefs table.

revision: 0026_daily_briefs
down_revision: 0025_user_brief_preferences
"""

from alembic import op

revision = "0026_daily_briefs"
down_revision = "0025_user_brief_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_briefs (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            date DATE NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            transcript TEXT,
            audio_url TEXT,
            audio_duration_seconds FLOAT,
            articles_json JSONB,
            outline_json JSONB,
            error_text TEXT,
            pn_sent BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            UNIQUE (user_id, date)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_daily_briefs_user_date
            ON daily_briefs (user_id, date DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS daily_briefs")
