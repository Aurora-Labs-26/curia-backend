"""
worker/handlers/brief.py
Worker handlers for the daily-brief pipeline (brief/ package).

  preopt_brief    { "topic_id": "<uuid>"? }        background lane
  generate_brief  { "user_id": "<id>", "date": "YYYY-MM-DD" }
                  interactive lane when POST /brief/generate triggers it (a
                  user is watching); background lane when worker/main.py's
                  brief-dispatch cron triggers it (no one's watching yet —
                  the brief_ready push is what tells them)

Moving these into worker lanes is the fix for the harness's worst structural
flaw (long LLM chains inside HTTP requests); queue idempotency also covers the
harness's (user, date) double-run race — generate_brief_for_user short-circuits
on an already-ready brief.
"""

from datetime import date

from loguru import logger

from brief.preopt_runner import run_preopt, run_preopt_for_topic_id
from brief.user_brief_runner import generate_brief_for_user


async def handle_preopt_brief(payload: dict) -> None:
    topic_id = payload.get("topic_id")
    if topic_id:
        logger.info(f"[handle_preopt_brief] topic_id={topic_id}")
        await run_preopt_for_topic_id(topic_id)
    else:
        logger.info("[handle_preopt_brief] all beats")
        await run_preopt()


async def handle_generate_brief(payload: dict) -> None:
    user_id = payload.get("user_id")
    brief_date = payload.get("date") or date.today().isoformat()
    if not user_id:
        raise ValueError("generate_brief payload missing user_id")
    logger.info(f"[handle_generate_brief] user={user_id} date={brief_date}")
    result = await generate_brief_for_user(user_id, brief_date)
    status = (result or {}).get("status", "?")
    logger.info(f"[handle_generate_brief] done user={user_id} status={status}")

    # Audio pass (brief/audio.py) — synthesize + stitch + upload; never blocks
    # the text manifest from being ready.
    try:
        from brief.audio import render_brief_audio
        brief_id = (result or {}).get("brief_id")
        if brief_id:
            await render_brief_audio(brief_id)
    except Exception as e:
        logger.warning(f"[handle_generate_brief] audio render skipped: {e}")

    # Notify after the audio attempt (success or failure) rather than right
    # after status flips to "ready" — a user tapping the push a few seconds
    # earlier would sometimes hit a brief with no playable audio yet.
    if status == "ready":
        try:
            from core.notifications import send_brief_ready
            await send_brief_ready(user_id, brief_id=str(brief_id or ""), brief_date=brief_date)
        except Exception as e:
            logger.warning(f"[handle_generate_brief] brief_ready push skipped: {e}")
