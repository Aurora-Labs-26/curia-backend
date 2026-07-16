"""
Global app settings — the automated-judging on/off toggle
(harness.eval_settings, db/015_judging_settings.sql) plus the faithfulness
regen-on-flag toggle (db/016_faithfulness_regen.sql). A tiny separate module
rather than folding this into eval_judges_service.py or cache_service.py: the
whole point of these toggles (see automated_judging_build_plan.md's "Hard
constraints" section, and faithfulness-judge-plan.md section 5) is that each
feature is one self-contained block with a single kill switch, not something
woven into existing services — this module is that switch, and nothing else
reads or writes harness.eval_settings.

Single-row table (id forced to `true` by a CHECK constraint) rather than an
in-memory flag, so the setting survives a server restart.
"""

from __future__ import annotations

from typing import Any, Dict

from app.services import harness_db


async def is_automated_judging_enabled() -> bool:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        "SELECT automated_judging_enabled FROM harness.eval_settings WHERE id = true"
    )
    # Row always exists (seeded by the migration itself) — True is a safe
    # fallback if it's ever somehow missing, since "on" is the shipped default.
    return bool(row["automated_judging_enabled"]) if row else True


async def set_automated_judging_enabled(enabled: bool) -> Dict[str, Any]:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        UPDATE harness.eval_settings
        SET automated_judging_enabled = $1, updated_at = now()
        WHERE id = true
        RETURNING automated_judging_enabled, updated_at
        """,
        enabled,
    )
    return dict(row)


async def is_faithfulness_regen_enabled() -> bool:
    """Governs only the regenerate-on-flag behavior (faithfulness_regen_service.py)
    — faithfulness_article judging itself always runs synchronously on the
    live Generate Brief path regardless of this toggle; turning this off just
    means a flagged critical/moderate claim gets logged and left as-is
    instead of triggering up to 2 rewrite attempts."""
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        "SELECT faithfulness_regen_enabled FROM harness.eval_settings WHERE id = true"
    )
    return bool(row["faithfulness_regen_enabled"]) if row else True


async def set_faithfulness_regen_enabled(enabled: bool) -> Dict[str, Any]:
    pool = await harness_db.get_pool()
    row = await pool.fetchrow(
        """
        UPDATE harness.eval_settings
        SET faithfulness_regen_enabled = $1, updated_at = now()
        WHERE id = true
        RETURNING faithfulness_regen_enabled, updated_at
        """,
        enabled,
    )
    return dict(row)
