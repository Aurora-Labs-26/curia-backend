"""
brief_runner.py — End-to-end brief generation for a single user.

Orchestrates: user context → news fetch → score → curate → outline → transcript → TTS → R2 upload → DB write → FCM push.

ARCHIVED (see legacy/README.md) — this module will NOT import successfully
as-is: `news_service.fetch_articles_for_profile`, `pipeline.OutlineRequest`,
`pipeline.TranscriptRequest`, `pipeline.run_outline_step`,
`pipeline.run_transcript_step`, and `UserProfile.subtopics` were all
permanently deleted (not archived) when System B (the two-phase Pre-Opt +
Per-User Brief Cache pipeline) became the only Daily Brief pipeline. Kept
here as reference for whenever System B needs real TTS/storage/push wired
in for production scheduling — not a working module in its current form.
"""

from __future__ import annotations

import asyncio
import logging
import os
import wave
from datetime import date
from typing import Any

import httpx

from legacy import db_service, push_service, storage_service, tts_service
from app.services.news_service import news_service
from app.services.pipeline import PipelineManager, UserProfile, RawArticle, RankRequest, ScoreRequest, OutlineRequest, TranscriptRequest
from app.services.llm_service import llm_service

logger = logging.getLogger(__name__)

pipeline = PipelineManager()


def _wav_duration(wav_bytes: bytes) -> float:
    import io
    with wave.open(io.BytesIO(wav_bytes), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        return frames / rate


async def generate_brief_for_user(user: dict, brief_date: str) -> None:
    """
    Run full pipeline for one user. Writes to daily_briefs table.
    Caller must handle exceptions.
    """
    user_id = user["user_id"]
    api_key = os.environ.get("ANTHROPIC_API_KEY")

    inserted = await db_service.brief_set_generating(user_id, brief_date)
    if not inserted:
        logger.info(f"[brief] user_id={user_id} already has a brief for {brief_date}, skipping")
        return

    logger.info(f"[brief] generating for user_id={user_id}")

    profile = UserProfile(
        name=user.get("name") or "there",
        interests=user.get("interests") or [],
        subtopics=user.get("subtopics") or {},
        custom_topics=user.get("custom_topics") or [],
        location=user.get("location_city") or "",
        country=user.get("location_country") or "",
        saves=[
            {"title": s.get("title", ""), "url": s.get("url", ""), "summary": s.get("description")}
            for s in (user.get("recent_saves") or [])
        ],
        days_since_last_brief=1,
        unlistened_queue=[],
        structure="Structure 1",
        voice="Voice A",
    )

    # Step 2: fetch news
    articles_raw = news_service.fetch_articles_for_profile(
        interests=profile.interests,
        location=profile.location,
        subtopics=profile.subtopics,
        country=profile.country,
        custom_topics=profile.custom_topics,
    )
    articles = [RawArticle(**a) for a in articles_raw]

    if not articles:
        raise RuntimeError("news_service returned 0 articles")

    # Step 2b: no-LLM pre-filter rank — cuts the raw pool roughly in half
    # before it reaches the LLM (see scoring_service.rank_articles). Local:
    # articles are always kept regardless of rank.
    rank_result = await pipeline.run_rank_step(RankRequest(user_profile=profile, articles=articles))
    included_urls = {e["url"] for e in rank_result["ranked"] if e["included"]}
    articles = [a for a in articles if a.url in included_urls]
    logger.info(
        f"[brief] user_id={user_id} rank step: {rank_result['total_count']} -> "
        f"{len(articles)} articles kept for LLM scoring"
    )

    # Step 3: score & curate (combined LLM call)
    score_curate_result = await pipeline.run_score_curate_step(
        ScoreRequest(user_profile=profile, articles=articles),
        api_key=api_key,
    )
    score_curate_parsed = score_curate_result.get("parsed") or {}
    selections = score_curate_parsed.get("selections") or []
    if not selections:
        raise RuntimeError("score & curate step returned no selections")

    # Step 4: outline
    outline_result = await pipeline.run_outline_step(
        OutlineRequest(user_profile=profile, selections=selections),
        api_key=api_key,
    )
    outline = (outline_result.get("parsed") or {}).get("segments") or []

    # Step 5: transcript
    transcript_result = await pipeline.run_transcript_step(
        TranscriptRequest(
            user_profile=profile,
            outline=outline,
            selections=selections,
        ),
        api_key=api_key,
    )
    transcript_text = transcript_result.get("text", "")
    parsed_segments = transcript_result.get("parsed_segments", [])

    # TTS — single request, full text, add_wav_header=True (matches curia-backend)
    audio_url = None
    duration = None
    try:
        audio_bytes = await tts_service.synthesize_text(
            text=transcript_text,
            voice_id=os.environ.get("BRIEF_VOICE_ID", "emily"),
        )
        duration = _wav_duration(audio_bytes)
        key = f"briefs/{user_id}/{brief_date}.wav"
        audio_url = await storage_service.upload_audio(audio_bytes, key)
        logger.info(f"[brief] audio ready duration={duration:.1f}s url={audio_url}")
    except Exception as tts_exc:
        logger.warning(f"[brief] TTS failed, saving transcript-only: {tts_exc}")

    # Write to DB
    await db_service.brief_set_ready(
        user_id=user_id,
        brief_date=brief_date,
        transcript=transcript_text,
        audio_url=audio_url,
        audio_duration_seconds=duration,
        articles=selections,
        outline=outline,
    )

    # FCM push
    fcm_token = user.get("fcm_token")
    if fcm_token:
        await push_service.send_brief_ready(fcm_token, brief_date)
        await db_service.brief_mark_pn_sent(user_id, brief_date)

    logger.info(f"[brief] done user_id={user_id} duration={duration}s audio_url={audio_url}")


async def run_batch(users: list[dict], brief_date: str) -> dict:
    """Run brief generation for all users with concurrency cap."""
    semaphore = asyncio.Semaphore(5)
    processed = 0
    failed = 0

    async def _one(user: dict) -> None:
        nonlocal processed, failed
        async with semaphore:
            try:
                await asyncio.wait_for(
                    generate_brief_for_user(user, brief_date),
                    timeout=600.0,
                )
                processed += 1
            except Exception as exc:
                import traceback as _tb
                failed += 1
                logger.error(f"[brief] FAILED user_id={user.get('user_id')}: {type(exc).__name__}: {exc}\n{_tb.format_exc()}")
                try:
                    await db_service.brief_set_failed(
                        user_id=user["user_id"],
                        brief_date=brief_date,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                except Exception as db_exc:
                    logger.error(f"[brief] failed to write error to DB: {db_exc}")

    await asyncio.gather(*[_one(u) for u in users])
    return {"processed": processed, "failed": failed, "total": len(users)}
