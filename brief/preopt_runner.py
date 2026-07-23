"""
Pre-Opt batch: for every system topic ("Beat"), fetch 50 articles (last 30h)
-> heuristic rank (Step 2b) -> LLM score & curate (Step 3, top-4 non-local)
-> full-text enrich (Step 3b) -> per-article segment transcript (Step 6,
cache-checked) -> cache in ARTICLE_SEGMENT_CACHE.

Runs independent of any user, ahead of any user's scheduled brief-generation
time (see user_brief_runner.py, which reuses these cached candidates for a
user's chosen system topics instead of re-fetching/re-ranking them).

Triggered manually via POST /api/preopt/run — no cron/scheduler this
iteration (see the plan's explicit non-goals).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from brief import store as cache_service
from brief import parked as eval_checks_service
from brief import parked as eval_logging_service
from brief.news import news_service as ns, BEAT_ARTICLE_CAP
from brief.pipeline import (
    pipeline_manager,
    UserProfile,
    RankRequest,
    ScoreRequest,
    ContentFetchRequest,
    ArticleSegmentRequest,
)

from loguru import logger

# Bounds the per-topic segment-generation fan-out (up to 4 articles/topic) —
# mirrors legacy/brief_runner.py's Semaphore(5) precedent for LLM/TTS calls.
_SEGMENT_SEMAPHORE = asyncio.Semaphore(5)


async def _cache_one_segment(
    article_id: str,
    segment_type: str,
    title: str,
    source: Optional[str],
    full_text: str,
    api_key: Optional[str],
    run_id: Optional[str] = None,
    content_fetched: bool = True,
) -> Dict[str, Any]:
    """Cache-checked segment generation for one non-local article. Shared
    logic between preopt_runner and user_brief_runner would ordinarily live
    in one place, but preopt_runner is always is_local=False (pre-opt never
    handles the Local segment — see module docstring), so this stays local
    and simple here; user_brief_runner has its own version that also
    branches on is_local for the 5th (local) selection."""
    cached = await cache_service.get_cached_segment(article_id, segment_type, is_local=False)
    if cached:
        text = cached["transcript_json"].get("text", "")
        await eval_logging_service.log_segment_transcript(
            run_id,
            article_id=article_id,
            segment_type=segment_type,
            text=text,
            cache_hit=True,
            word_count=cached["transcript_json"].get("word_count"),
        )
        return {
            "article_id": article_id,
            "segment_type": segment_type,
            "cache_hit": True,
            "mp3_url": cached["mp3_url"],
            "duration_s": cached["duration_s"],
            "text": text,
        }

    async with _SEGMENT_SEMAPHORE:
        from brief import faithfulness
        result = await faithfulness.generate_verified_segment(
            ArticleSegmentRequest(
                title=title, source=source, full_text=full_text, segment_type=segment_type,
                content_fetched=content_fetched,
            ),
            api_key=api_key, segment_type=segment_type, is_local=False,
        )

    duration_s = cache_service.estimate_duration_s(result["word_count"])
    mp3_url = cache_service.placeholder_mp3_url("segment", f"{article_id}-{segment_type}-False")
    await cache_service.put_cached_segment(
        article_id=article_id,
        segment_type=segment_type,
        is_local=False,
        transcript_json={"text": result["text"], "word_count": result["word_count"]},
        mp3_url=mp3_url,
        duration_s=duration_s,
    )
    fv = result.get("faithfulness")
    if fv and fv.get("severity") not in (None, "unknown"):
        await cache_service.set_cached_segment_faithfulness(
            str(article_id), segment_type, False, fv["severity"], {"claims": fv["claims"]},
        )
    await eval_logging_service.log_segment_transcript(
        run_id,
        article_id=article_id,
        segment_type=segment_type,
        text=result["text"],
        cache_hit=False,
        word_count=result["word_count"],
        model=result.get("model"),
        input_tokens=result.get("input_tokens"),
        output_tokens=result.get("output_tokens"),
        latency_ms=result.get("latency_ms"),
        simulated=result.get("simulated"),
    )
    return {
        "article_id": article_id,
        "segment_type": segment_type,
        "cache_hit": False,
        "mp3_url": mp3_url,
        "duration_s": duration_s,
        "text": result["text"],
    }


async def _run_preopt_for_topic(topic: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    beat = topic["beat"]
    topic_id = str(topic["id"])

    run_id = await eval_logging_service.start_eval_run("preopt", topic_id=topic_id)
    try:
        # fetch_articles_for_beat is a plain synchronous/blocking network call
        # (gnews RSS fetch) — offloaded to a worker thread so it doesn't freeze
        # the event loop and serialize every other concurrently-running topic.
        raw_articles = await asyncio.to_thread(
            ns.fetch_articles_for_beat, beat, country="", max_results=BEAT_ARTICLE_CAP
        )
        await eval_logging_service.log_fetched_articles(run_id, raw_articles, source_kind="preopt_fetch")

        user_profile = UserProfile(interests=[beat], location="")

        rank_result = await pipeline_manager.run_rank_step(
            RankRequest(user_profile=user_profile, articles=raw_articles)
        )
        await eval_logging_service.log_ranking_output(run_id, rank_result["ranked"])
        included = [a for a in rank_result["ranked"] if a.get("included")]

        score_result = await pipeline_manager.run_score_curate_step(
            ScoreRequest(user_profile=user_profile, articles=included), api_key=api_key
        )
        score_parsed = score_result.get("parsed") or {}
        await eval_logging_service.log_llm_ranking_output(
            run_id, score_parsed.get("ranked_order") or [], score_parsed.get("local_ranked_order") or []
        )
        selections = score_parsed.get("selections") or []
        # Pre-opt's pool has no "Local:"-tagged articles by construction, so
        # local_pick is always empty — this filter is a defensive no-op, not
        # load-bearing.
        non_local_selections = [s for s in selections if s.get("slot") != "Local Pulse"][:4]

        for sel in non_local_selections:
            article_id = await cache_service.upsert_article(
                url=sel.get("url", ""), title=sel.get("title", ""), topic_id=topic_id,
            )
            sel["_article_id"] = article_id

        # Enforce "only the current top-4 stay cached" — a re-fetch REPLACES
        # whichever of the topic's previously-cached articles didn't make this
        # run's selection, instead of accumulating alongside them (see
        # cache_service.prune_topic_segment_cache).
        new_article_ids = [sel["_article_id"] for sel in non_local_selections]
        pruned_stale = await cache_service.prune_topic_segment_cache(topic_id, new_article_ids)

        fetch_content_result = await pipeline_manager.run_content_fetch_step(
            ContentFetchRequest(selections=non_local_selections)
        )
        enriched = fetch_content_result["selections"]  # same order/length as non_local_selections
        await eval_logging_service.update_fetched_article_content(run_id, enriched)

        results: List[Dict[str, Any]] = []
        for i, sel in enumerate(enriched):
            segment_type = "lead" if i == 0 else "standard"
            seg_result = await _cache_one_segment(
                article_id=sel["_article_id"],
                segment_type=segment_type,
                title=sel.get("title", ""),
                source=sel.get("source"),
                full_text=sel.get("full_text", ""),
                api_key=api_key,
                run_id=run_id,
                content_fetched=sel.get("content_fetched", True),
            )
            results.append({**seg_result, "title": sel.get("title", ""), "source": sel.get("source")})

        lead = next((r for r in results if r["segment_type"] == "lead"), None)
        standard = [r for r in results if r["segment_type"] == "standard"]

        await eval_logging_service.finish_eval_run(run_id, "completed")
        await eval_checks_service.run_checks_for_run(run_id)
        return {
            "topic": topic["name"],
            "topic_id": topic_id,
            "raw_fetched": len(raw_articles),
            "kept_after_rank": len(included),
            "lead": lead,
            "standard": standard,
            "cache_hits": sum(1 for r in results if r["cache_hit"]),
            "cache_misses": sum(1 for r in results if not r["cache_hit"]),
            "pruned_stale": pruned_stale,
        }
    except Exception as e:
        await eval_logging_service.finish_eval_run(run_id, "failed", error=str(e))
        await eval_checks_service.run_checks_for_run(run_id)
        raise


async def _run_topic_isolated(topic: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """One topic's full pipeline, with its own failure isolated from the
    other 6 (mirrors legacy/brief_runner.py's run_batch per-item isolation) —
    one bad RSS fetch or malformed LLM response doesn't abort the rest."""
    try:
        result = await _run_preopt_for_topic(topic, api_key)
        logger.info(
            f"[preopt] topic={topic['name']} raw={result['raw_fetched']} "
            f"kept={result['kept_after_rank']} cache_hits={result['cache_hits']} "
            f"cache_misses={result['cache_misses']}"
        )
        return result
    except Exception as e:
        logger.error(f"[preopt] topic={topic['name']} failed: {e}")
        return {"topic": topic["name"], "topic_id": str(topic["id"]), "error": str(e)}


async def run_preopt_for_topic_id(topic_id: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Single-topic entry point for POST /api/preopt/run/{topic_id} — lets the
    harness UI trigger one topic's pipeline per request instead of all 7 at
    once (see run_preopt), so each request comfortably finishes within a
    hosted reverse-proxy's gateway timeout even with full article enrichment
    enabled. Unlike _run_topic_isolated (used by the bulk path to keep one
    topic's failure from aborting the other 6), this lets exceptions
    propagate — there's nothing else to protect in a single-topic request."""
    topics = await cache_service.list_system_topics()
    topic = next((t for t in topics if str(t["id"]) == topic_id), None)
    if topic is None:
        raise ValueError(f"Unknown topic_id: {topic_id}")
    return await _run_preopt_for_topic(topic, api_key)


async def run_preopt(api_key: Optional[str] = None) -> Dict[str, Any]:
    """Runs all 7 system topics CONCURRENTLY (each isolated via
    _run_topic_isolated) rather than one after another — sequentially, 7
    topics x several chained LLM/network calls each comfortably exceeds a
    minute in total and trips a reverse-proxy gateway timeout (502) on any
    hosted deployment before the request ever completes. Running them
    concurrently bounds total latency to roughly the slowest single topic
    instead of the sum of all seven."""
    topics = await cache_service.list_system_topics()
    topic_results: List[Dict[str, Any]] = await asyncio.gather(
        *[_run_topic_isolated(topic, api_key) for topic in topics]
    )

    totals = {
        "topics_processed": len(topic_results),
        "cache_hits": sum(r.get("cache_hits", 0) for r in topic_results),
        "cache_misses": sum(r.get("cache_misses", 0) for r in topic_results),
        "pruned_stale": sum(r.get("pruned_stale", 0) for r in topic_results),
        "failed_topics": sum(1 for r in topic_results if "error" in r),
    }
    return {"topics": topic_results, "totals": totals}
