"""add tts_timings JSONB column to episode for transcript sync

Revision ID: 0015_episode_tts_timings
Revises: 0014_fb_uid_constraint
Create Date: 2026-05-18

Adds tts_timings JSONB to episode. Stores per-line absolute timestamps
derived from TTS synthesis so the frontend can sync transcript display
to playback position (Option B / Spotify-standard approach).

Shape:
  [
    {
      "line_index": 0,
      "start_ms": 0,
      "end_ms": 2340,
      "speaker": "kenji",
      "text": "..."
    },
    ...
  ]

NULL when not yet synthesized or for episodes generated before this migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0015_episode_tts_timings"
down_revision = "0014_fb_uid_constraint"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("episode", sa.Column("tts_timings", JSONB(), nullable=True))


def downgrade():
    op.drop_column("episode", "tts_timings")
