"""add per-episode length_minutes and speaker_override columns

Revision ID: 0008_episode_overrides
Revises: 0007_drop_host_memory
Create Date: 2026-05-11

Adds two optional override columns to the episode table:
  - length_minutes  INTEGER  — caller-specified runtime target (3–30 min)
  - speaker_override TEXT    — caller-specified speaker name (kenji/arjun/emeka)

Both are nullable; NULL means "use the show's default".
"""

from alembic import op
import sqlalchemy as sa

revision = "0008_episode_overrides"
down_revision = "0007_drop_host_memory"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("episode", sa.Column("length_minutes", sa.Integer(), nullable=True))
    op.add_column("episode", sa.Column("speaker_override", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("episode", "speaker_override")
    op.drop_column("episode", "length_minutes")
