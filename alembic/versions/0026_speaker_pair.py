"""0026_speaker_pair

Add speaker_pair column to episode table for two-host overrides.

Revision ID: 0026
Revises: 0025
"""

from alembic import op

revision = "0026_speaker_pair"
down_revision = "0025_seed_episodes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE episode ADD COLUMN IF NOT EXISTS speaker_pair TEXT[] NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS speaker_pair")
