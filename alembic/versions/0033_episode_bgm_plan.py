"""add bgm_plan to episode

Revision ID: 0033_episode_bgm_plan
Revises: 0032_source_clean_text
Create Date: 2026-07-13

(Renumbered from 0029_episode_bgm_plan on feat/bgm-sfx — that ID collided
with 0029_source_og_image when the branch was integrated into v3.)

Stores the per-segment vibe assignments and transition timestamps used by
core/audio/vibe_mix.py to build the BGM/SFX layer, e.g.
{"segment_vibes": {"-1": "intro", "1": "curious", ...},
 "transitions": [{"at_ms": ..., "from_segment": ..., "to_segment": ...}, ...]}
QA/debugging only — not read by the generation pipeline itself.
"""

from alembic import op

revision = "0033_episode_bgm_plan"
down_revision = "0032_source_clean_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE episode ADD COLUMN IF NOT EXISTS bgm_plan JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE episode DROP COLUMN IF EXISTS bgm_plan")
