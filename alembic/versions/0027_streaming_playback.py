"""0027_streaming_playback

Streaming playback support (STREAMING_PLAN.md):
- duration_estimate_seconds: script-derived estimate, set at script_ready
- failed_stage: 'script' | 'audio' — which pipeline stage failed
- play_position_seconds: resume position in seconds (replaces fraction-based
  play_progress, which breaks when duration changes from estimate to exact)
- stream_chunks: ordered list of published HLS chunks [{key, duration_ms}]
- stream_state: NULL (no stream) | 'live' (growing) | 'ended' (ENDLIST)

Revision ID: 0027
Revises: 0026
"""

from alembic import op

revision = "0027_streaming_playback"
down_revision = "0026_speaker_pair"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE episode
            ADD COLUMN IF NOT EXISTS duration_estimate_seconds INTEGER,
            ADD COLUMN IF NOT EXISTS failed_stage TEXT,
            ADD COLUMN IF NOT EXISTS play_position_seconds REAL,
            ADD COLUMN IF NOT EXISTS stream_chunks JSONB,
            ADD COLUMN IF NOT EXISTS stream_state TEXT
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE episode
            DROP COLUMN IF EXISTS duration_estimate_seconds,
            DROP COLUMN IF EXISTS failed_stage,
            DROP COLUMN IF EXISTS play_position_seconds,
            DROP COLUMN IF EXISTS stream_chunks,
            DROP COLUMN IF EXISTS stream_state
        """
    )
