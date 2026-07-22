"""
brief/parked.py
No-op stand-ins for the daily-brief eval harness (judges, eval logging,
checks, automated judging, faithfulness gate) — PARKED as future analytics
per the integration decision ("dailybrief analysis v1.md", Option C amended).

Runners import this module under the original service names, so their code
shape is unchanged; every eval call disappears here. The one exception with
real behavior: generate_verified_segment, which in the harness wrapped
segment generation in a judge+regen loop — parked, it generates the segment
directly (same return shape, no judging).
"""
from loguru import logger


def maybe_run_automated_judging(*args, **kwargs) -> None:   # sync in original
    return None


async def generate_verified_segment(req, api_key=None, run_id=None, article_id=None,
                                    url=None, segment_type=None, is_local=None, **kw):
    from brief.pipeline import pipeline_manager
    return await pipeline_manager.run_article_segment_step(req)


async def verify_cached_segment(*args, **kwargs):
    return None


def __getattr__(name):
    async def _noop(*args, **kwargs):
        return None
    logger.trace(f"[brief.parked] eval call ignored: {name}")
    return _noop
