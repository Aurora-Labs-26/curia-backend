"""add hidden flag to source (soft-delete for cleared failed sources)

Revision ID: 0020_source_hidden
Revises: 0019_episode_audio_fields
Create Date: 2026-05-20

Failed sources are soft-hidden (not deleted) once cleared, so the pile stays
clean while the rows remain in the DB for debugging/analytics. GET /sources
filters `hidden = false`. Uses IF NOT EXISTS for safety on drifted DBs.
"""

from alembic import op

revision = "0020_source_hidden"
down_revision = "0019_episode_audio_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE source ADD COLUMN IF NOT EXISTS hidden BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    op.execute("ALTER TABLE source DROP COLUMN IF EXISTS hidden")
