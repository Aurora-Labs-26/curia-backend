"""0028_show_idea_outline_cache

ShowIdeas flow (ShowIdeas+Streaming.md): the outline is pre-generated per idea
and cached on show_idea, so the idea detail sheet can show title/duration/chapters
before any episode exists, and episode generation can skip the outlining stage.

- title / description / duration_estimate_seconds / chapters: display fields for
  the idea detail sheet, derived from the cached outline
- outline: full outline JSON (title, thread, segments)
- superseded: set by the invalidation rule when a re-cluster retires this idea.
  Superseded ideas are hidden from GET /ideas but remain generatable (a user
  viewing one when it was retired can still generate from it).

Revision ID: 0028
Revises: 0027
"""

from alembic import op

revision = "0028_show_idea_outline_cache"
down_revision = "0027_streaming_playback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE show_idea
            ADD COLUMN IF NOT EXISTS title TEXT,
            ADD COLUMN IF NOT EXISTS description TEXT,
            ADD COLUMN IF NOT EXISTS duration_estimate_seconds INTEGER,
            ADD COLUMN IF NOT EXISTS outline JSONB,
            ADD COLUMN IF NOT EXISTS chapters JSONB,
            ADD COLUMN IF NOT EXISTS superseded BOOLEAN NOT NULL DEFAULT FALSE
        """
    )
    # The ideas tab query: user's live ideas, newest first
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS show_idea_live_idx
        ON show_idea (user_id, created_at DESC)
        WHERE generated = false AND superseded = false
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS show_idea_live_idx")
    op.execute(
        """
        ALTER TABLE show_idea
            DROP COLUMN IF EXISTS title,
            DROP COLUMN IF EXISTS description,
            DROP COLUMN IF EXISTS duration_estimate_seconds,
            DROP COLUMN IF EXISTS outline,
            DROP COLUMN IF EXISTS chapters,
            DROP COLUMN IF EXISTS superseded
        """
    )
