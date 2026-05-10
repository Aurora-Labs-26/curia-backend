"""episode quality fields

Revision ID: 0003_episode_quality
Revises: 0002_users_jobs_and_status
Create Date: 2026-05-10

Adds the rubric judge's output to the episode row so the API can surface it
and the user can see why an episode scored what it did.
"""

from alembic import op

revision = "0003_episode_quality"
down_revision = "0002_users_jobs_and_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE episode
            ADD COLUMN quality_score    REAL,
            ADD COLUMN quality_feedback TEXT,
            ADD COLUMN quality_violations TEXT[] NOT NULL DEFAULT '{}',
            ADD COLUMN regenerated      BOOLEAN NOT NULL DEFAULT FALSE
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE episode
            DROP COLUMN IF EXISTS regenerated,
            DROP COLUMN IF EXISTS quality_violations,
            DROP COLUMN IF EXISTS quality_feedback,
            DROP COLUMN IF EXISTS quality_score
    """)
