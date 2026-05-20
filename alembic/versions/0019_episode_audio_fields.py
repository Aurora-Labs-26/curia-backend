"""add duration_seconds, description, chapters to episode (schema drift fix)

Revision ID: 0019_episode_audio_display_fields
Revises: 0018_create_source_similarity
Create Date: 2026-05-20

The pulled v2.2 generator's final UPDATE writes duration_seconds, description,
and chapters to the episode row, but no migration added these columns to this
database. Episodes synthesized audio successfully but failed on the final write
with `column "duration_seconds" of relation "episode" does not exist`.
Uses IF NOT EXISTS so it is safe on databases where the columns already exist.
"""

from alembic import op

revision = "0019_episode_audio_fields"
down_revision = "0018_create_source_similarity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS duration_seconds INTEGER")
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS description TEXT")
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS chapters JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS chapters")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS description")
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS duration_seconds")
