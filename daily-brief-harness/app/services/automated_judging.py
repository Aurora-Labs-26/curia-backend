"""
The entire "automated judging" feature's single entry point (see
automated_judging_build_plan.md). This module is the bolt-on block the
build plan's "Hard constraints" section describes: user_brief_runner.py
calls exactly one function here, at the very end of a successful Generate
Brief / regenerate-bookends call, and nothing else about the live pipeline
knows this feature exists.

Two responsibilities live here specifically because they must NOT live in
eval_judges_service.py (which stays a pure judge-implementation module,
shared by both the manual gold path and this one):
1. The on/off toggle check (settings_service.is_automated_judging_enabled) —
   when off, this module does nothing at all, not even fire the background
   task.
2. Error containment. eval_judges_service.run_automated_judges deliberately
   does NOT catch its own exceptions (same convention as the manual gold
   path) — this module's job is to make sure that never matters: a judge
   failure must never surface to, block, or affect a live brief generation
   that already succeeded and already returned its response to the caller.

Task lifecycle: asyncio.create_task's returned Task object is held in
_INFLIGHT_TASKS until it finishes, then discarded via a done-callback — a
bare fire-and-forget create_task() with nothing holding the reference can be
garbage-collected mid-flight, a known asyncio footgun.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Set

from app.services import eval_judges_service, settings_service

logger = logging.getLogger(__name__)

_INFLIGHT_TASKS: Set[asyncio.Task] = set()


async def _run_and_log(run_id: str, kind: str) -> None:
    try:
        result = await eval_judges_service.run_automated_judges(run_id, kind)
        logger.info(
            f"[automated_judging] run_id={run_id} kind={kind} completed: "
            f"{len(result.get('judge_outputs', []))} judge output(s)"
        )
    except Exception as e:
        # Must never propagate — this runs in a background task with no
        # caller left to hand an exception to. finish_eval_run/
        # mark_daily_brief_failed etc. have already run and returned by the
        # time this fires; a failure here is purely a missed judging pass,
        # never a broken brief.
        logger.warning(f"[automated_judging] run_id={run_id} kind={kind} failed: {e}")


def maybe_run_automated_judging(run_id: str, kind: str) -> None:
    """Call this, and only this, from user_brief_runner.py — at the end of
    the success path, after the brief's own response is already fully built
    (fire-and-forget: does not await, so the caller's response is never
    delayed). No-ops entirely (doesn't even check the DB) if run_id is
    falsy, matching eval_logging_service's guarded-no-op convention for a
    broken logging path degrading gracefully."""
    if not run_id:
        return

    async def _gated() -> None:
        try:
            if not await settings_service.is_automated_judging_enabled():
                return
        except Exception as e:
            # The toggle check itself must be as fail-safe as the judging
            # pass it guards — e.g. a deploy landing slightly before its own
            # migration (db/015) means harness.eval_settings briefly doesn't
            # exist yet. That must degrade to "skip this once", not an
            # unhandled exception in a fire-and-forget task.
            logger.warning(f"[automated_judging] run_id={run_id} kind={kind} toggle check failed, skipping: {e}")
            return
        await _run_and_log(run_id, kind)

    task = asyncio.create_task(_gated())
    _INFLIGHT_TASKS.add(task)
    task.add_done_callback(_INFLIGHT_TASKS.discard)
