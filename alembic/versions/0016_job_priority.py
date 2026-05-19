"""Add priority column to jobs table for job type ordering.

Revision ID: 0016_job_priority
Revises: 0015_episode_tts_timings
Create Date: 2026-05-19
"""

from alembic import op
import sqlalchemy as sa

revision = "0016_job_priority"
down_revision = "0015_episode_tts_timings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Lower number = higher priority. Default 10 (normal). Ingest gets 1 (urgent).
    op.add_column("jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="10"))
    op.create_index("jobs_dispatch_priority_idx", "jobs", ["priority", "created_at"],
                    postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_index("jobs_dispatch_priority_idx", table_name="jobs")
    op.drop_column("jobs", "priority")
