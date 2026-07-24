"""
Per-user "Generate Brief" trigger: looks up one user's chosen system topics +
custom topics + location, builds an article pool (reusing Pre-Opt's cached
candidates for chosen topics instead of re-fetching them — see
cache_service.get_preopt_candidates), runs the same heuristic+LLM ranking
(Steps 2b/3) over that pool, resolves the winning 5 articles against
ARTICLE_SEGMENT_CACHE (hit/miss), generates only what's missing, then
generates a never-cached intro+outro and assembles the ordered
6-clip manifest.

If a brief already exists for (user, date), the 5-article selection is
reused as-is (zero re-ranking/re-fetch/re-generation) but intro/outro
are always regenerated fresh — that step is inherently per-day/never cached,
and this lets repeated calls validate SYSTEM_INTRO_OUTRO_PROMPT
against a fixed, controlled article selection instead of a new one each time.
Note this internal reuse check is a harness/dashboard convenience only — the
production scheduler's own "has this user's brief for today already been
generated" gate lives entirely outside this function, so this isn't (and
doesn't need to be) a true no-op.

Triggered manually via POST /api/users/{user_id}/generate-brief — no cron/
scheduler this iteration (see the plan's explicit non-goals).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from brief import parked as automated_judging
from brief import store as cache_service
from brief import parked as eval_checks_service
from brief import parked as eval_logging_service
from brief import faithfulness as faithfulness_regen_service
from brief import weather as weather_service
from brief.news import news_service as ns, GLOBAL_GEO
from brief.pipeline import (
    pipeline_manager,
    UserProfile,
    RankRequest,
    ScoreRequest,
    ContentFetchRequest,
    ArticleSegmentRequest,
    IntroOutroRequest,
)

from loguru import logger

LOCAL_POOL_SIZE = 20
CUSTOM_TOPIC_POOL_SIZE = 10

# Bounds concurrent segment-generation LLM calls for one user's (at most 5)
# missing articles — mirrors preopt_runner.py's own Semaphore(5) precedent.
# Generating these one at a time in sequence (the previous behavior) adds
# unnecessary latency per miss, compounding the risk of tripping a hosted
# reverse-proxy's gateway timeout.
_SEGMENT_SEMAPHORE = asyncio.Semaphore(5)


def _clip(kind: str, text: str, mp3_url: str) -> Dict[str, Any]:
    """One manifest entry for a bookend (intro/outro) clip. No real
    audio bytes exist anywhere (TTS is never called this iteration), so
    duration is estimated from word count rather than measured."""
    word_count = len(text.split())
    return {
        "kind": kind, "text": text, "word_count": word_count,
        "duration_s": cache_service.estimate_duration_s(word_count), "mp3_url": mp3_url,
    }


async def _regenerate_bookends_for_brief(
    brief_id: str, user_id: str, brief_date: str, api_key: Optional[str] = None
) -> Dict[str, Any]:
    """Reuses an already-`ready` brief's persisted 5-article selection and
    cached segment text untouched — zero ranking/content-fetch/segment LLM
    calls — but always calls Step 7 fresh for intro/outro. Does not
    touch daily_briefs.status, daily_brief_articles, or article_segment_cache;
    a failure here must not affect the already-valid persisted brief, so this
    is deliberately NOT wrapped by generate_brief_for_user's
    mark_daily_brief_failed except block."""
    run_id = await eval_logging_service.start_eval_run("regenerate_bookends", user_id=user_id, brief_id=brief_id)
    try:
        detail = await cache_service.get_daily_brief_detail(brief_id)
        articles = detail["articles"]  # already ordered lead -> standard (rank asc) -> local

        user = await cache_service.get_user(user_id)
        if not user:
            raise ValueError(f"Unknown user_id: {user_id}")
        location_name = user["location_name"]
        display_name = cache_service.display_name_for(user)
        weather, local_time = await weather_service.get_weather_and_local_time(location_name)

        io_selections = [{"title": a["title"], "reason": a.get("reason") or ""} for a in articles]
        io_result = await pipeline_manager.run_intro_outro_step(
            IntroOutroRequest(
                display_name=display_name,
                location_name=location_name,
                local_time=local_time,
                weather=weather,
                selections=io_selections,
            ),
            api_key=api_key,
        )
        await eval_logging_service.log_meta_segments(
            run_id,
            intro=io_result["intro"],
            outro=io_result["outro"],
            inputs_used={
                "display_name": display_name,
                "location_name": location_name,
                "weather": weather,
                "local_time": local_time,
                "ranked_order": io_selections,
            },
            model=io_result.get("model"),
            input_tokens=io_result.get("input_tokens"),
            output_tokens=io_result.get("output_tokens"),
            latency_ms=io_result.get("latency_ms"),
            simulated=io_result.get("simulated"),
        )

        intro_mp3_url = cache_service.placeholder_mp3_url("intro", brief_id)
        outro_mp3_url = cache_service.placeholder_mp3_url("outro", brief_id)

        manifest: List[Dict[str, Any]] = [
            _clip("intro", io_result["intro"], intro_mp3_url),
        ]
        for a in articles:
            transcript = a.get("transcript_json") or {}
            manifest.append({
                "kind": a["segment_type"],
                "article_id": str(a["article_id"]),
                "title": a["title"],
                "cache_hit": True,
                "mp3_url": a.get("mp3_url"),
                "duration_s": a.get("duration_s"),
                "text": transcript.get("text", ""),
            })
        manifest.append(_clip("outro", io_result["outro"], outro_mp3_url))

        total_duration_s = round(sum(m["duration_s"] for m in manifest if m.get("duration_s") is not None), 1)

        user_topics = await cache_service.get_user_topics(user_id)
        topics_used = [t["name"] for t in user_topics["chosen"]] + [t["name"] for t in user_topics["custom"]]
        await cache_service.save_transcript_record(
            user_id=user_id, display_name=display_name, brief_date=brief_date,
            topics_used=topics_used, segments=manifest,
        )

        await eval_logging_service.finish_eval_run(run_id, "completed")
        await eval_checks_service.run_checks_for_run(run_id)
        # Fire-and-forget, after the response below is already fully built —
        # see automated_judging.py. Only faithfulness_bookend runs on this
        # path (AUTOMATED_JUDGE_SETS["regenerate_bookends"]): the 5 articles/
        # segments are reused untouched, so only intro/outro are genuinely
        # fresh here.
        automated_judging.maybe_run_automated_judging(run_id, "regenerate_bookends")
        return {
            "brief_id": brief_id,
            "user_id": user_id,
            "date": brief_date,
            "status": "ready",
            "display_name": display_name,
            "location_name": location_name,
            "segments": manifest,
            "total_duration_s": total_duration_s,
            "stitched_mp3_url": detail["brief"].get("stitched_mp3_url"),
            "cache_summary": {"hits": len(articles), "misses": 0},
            "bookends_regenerated": True,
        }
    except Exception as e:
        await eval_logging_service.finish_eval_run(run_id, "failed", error=str(e))
        await eval_checks_service.run_checks_for_run(run_id)
        raise


async def generate_brief_for_user(user_id: str, brief_date: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    brief_info = await cache_service.get_or_create_daily_brief(user_id, brief_date)
    brief_id = brief_info["id"]

    if not brief_info["created"] and brief_info["status"] == "ready":
        return await _regenerate_bookends_for_brief(brief_id, user_id, brief_date, api_key)

    run_id = await eval_logging_service.start_eval_run("generate_brief", user_id=user_id, brief_id=brief_id)
    try:
        user = await cache_service.get_user(user_id)
        if not user:
            raise ValueError(f"Unknown user_id: {user_id}")
        location_name = user["location_name"]
        display_name = cache_service.display_name_for(user)
        weather, local_time = await weather_service.get_weather_and_local_time(location_name)

        user_topics = await cache_service.get_user_topics(user_id)
        chosen_topics = user_topics["chosen"]
        custom_topics = user_topics["custom"]
        chosen_beats = [t["beat"] for t in chosen_topics if t.get("beat")]
        custom_names = [t["name"] for t in custom_topics]

        # --- Build the pool ---
        pool: List[Dict[str, Any]] = []

        # 1. Chosen system topics: reuse Pre-Opt's already-cached candidates —
        # no re-fetch. This is the entire point of the two-phase design.
        chosen_topic_ids = [str(t["id"]) for t in chosen_topics]
        topic_beat_by_id = {str(t["id"]): t.get("beat", "") for t in chosen_topics}
        preopt_candidates = await cache_service.get_preopt_candidates(chosen_topic_ids)
        preopt_reuse_articles = [
            {
                "title": c["title"],
                "description": c["title"],  # approximated — see plan §0.4
                "source": "",
                "url": c["url"],
                "published_date": "",
                "topic": topic_beat_by_id.get(str(c["topic_id"]), ""),
            }
            for c in preopt_candidates
        ]
        pool.extend(preopt_reuse_articles)
        await eval_logging_service.log_fetched_articles(run_id, preopt_reuse_articles, source_kind="preopt_reuse")

        # 2. Custom topics: fresh fetch, never pre-opt'd. fetch_articles_for_topic
        # is a plain synchronous/blocking network call (gnews RSS fetch) —
        # offloaded to a worker thread so it doesn't freeze the event loop and
        # serialize every other concurrently-running request.
        custom_fetch_articles: List[Dict[str, Any]] = []
        for name in custom_names:
            v_gl, v_hl, v_ceid = GLOBAL_GEO
            custom_fetch_articles.extend(await asyncio.to_thread(
                ns.fetch_articles_for_topic, f'"{name}"', v_gl, v_hl, v_ceid, CUSTOM_TOPIC_POOL_SIZE, f"Custom: {name}"
            ))
        pool.extend(custom_fetch_articles)
        await eval_logging_service.log_fetched_articles(run_id, custom_fetch_articles, source_kind="custom_fetch")

        # 3. Local: fresh fetch, never pre-opt'd. Combined with the user's own
        # topics (beats + custom) via an OR-group alongside the mandatory
        # location term — same query syntax already used for beats with no
        # Google topic-code (see fetch_articles_for_beat's `" OR ".join`
        # base_query). A bare location-only query used to be the right match
        # for score_curate.py Step 4 Phase A's old OR gate (topic OR a major
        # local event, regardless of topic) — you wanted generic significant
        # local news to have a chance of passing. Now that Phase A is a
        # strict AND (2026-07-13b: topic relevance required, no
        # significance-based bypass), a topic-agnostic fetch mostly just
        # wastes the pool on articles the gate will reject regardless of how
        # significant they are — confirmed live across 5 cities (Mumbai,
        # Bangalore, Delhi, Hyderabad, Bhopal), where a bare-location fetch
        # surfaced at most 1-2 topically-relevant articles per city out of
        # 20. Falls back to a bare location query only if the user has
        # stated no topics at all, matching Phase A's own "if the user has
        # any stated" carve-out.
        loc_gl, loc_hl, loc_ceid = ns.resolve_local_geo(location_name)
        all_topics = chosen_beats + custom_names
        local_query = (
            f'"{location_name}" (' + " OR ".join(f'"{t}"' for t in all_topics) + ")"
            if all_topics
            else f'"{location_name}"'
        )
        local_fetch_articles = await asyncio.to_thread(
            ns.fetch_articles_for_topic, local_query, loc_gl, loc_hl, loc_ceid, LOCAL_POOL_SIZE, f"Local: {location_name}"
        )
        pool.extend(local_fetch_articles)
        await eval_logging_service.log_fetched_articles(run_id, local_fetch_articles, source_kind="local_fetch")

        # --- Rank (2b) + score & curate (3) across the FULL combined pool ---
        user_profile = UserProfile(interests=chosen_beats, custom_topics=custom_names, location=location_name)
        rank_result = await pipeline_manager.run_rank_step(RankRequest(user_profile=user_profile, articles=pool))
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

        non_local = [s for s in selections if s.get("slot") != "Local Pulse"][:4]
        local_sel = next((s for s in selections if s.get("slot") == "Local Pulse"), None)
        ordered_selections = list(non_local) + ([local_sel] if local_sel else [])

        # --- Resolve real article rows + segment types, cache lookup ---
        resolved: List[Dict[str, Any]] = []
        for i, sel in enumerate(ordered_selections):
            is_local_sel = sel.get("slot") == "Local Pulse"
            segment_type = "local" if is_local_sel else ("lead" if i == 0 else "standard")
            article_id = await cache_service.upsert_article(url=sel.get("url", ""), title=sel.get("title", ""))
            cached = await cache_service.get_cached_segment(article_id, segment_type, is_local_sel)
            resolved.append({
                "selection": sel,
                "article_id": article_id,
                "segment_type": segment_type,
                "is_local": is_local_sel,
                "cached": cached,
            })

        # --- Full-text fetch (Step 3b) only for the cache-miss subset — a
        # cache hit never needs full body text since it's never fed to an
        # LLM call. Strictly less work than fetching all 5, same result. ---
        miss_entries = [r for r in resolved if not r["cached"]]
        if miss_entries:
            fetch_content_result = await pipeline_manager.run_content_fetch_step(
                ContentFetchRequest(selections=[r["selection"] for r in miss_entries])
            )
            await eval_logging_service.update_fetched_article_content(run_id, fetch_content_result["selections"])
            for r, enriched_sel in zip(miss_entries, fetch_content_result["selections"]):
                r["full_text"] = enriched_sel.get("full_text", "")
                r["source"] = enriched_sel.get("source", r["selection"].get("source", ""))
                r["content_fetched"] = enriched_sel.get("content_fetched", False)
                if enriched_sel.get("resolved_url"):
                    await cache_service.set_article_resolved_url(
                        str(r["article_id"]), enriched_sel["resolved_url"])

        # url -> raw fetched published_date, for threading into faithfulness
        # judging below (see faithfulness_regen_service.py). `pool` still has
        # every raw fetched article at this point (Score & Curate only reads
        # it, doesn't mutate/strip it), so this is a cheap local lookup
        # rather than a second fetch.
        published_date_by_url = {a.get("url", ""): a.get("published_date") for a in pool}

        # --- Generate missing segments (concurrently, semaphore-bounded),
        # reuse cached ones. Previously a sequential for-loop, which serialized
        # up to 5 LLM calls one after another — needless added latency that,
        # combined with the rest of this pipeline, risks tripping a hosted
        # reverse-proxy's gateway timeout. asyncio.gather preserves `resolved`'s
        # order, which the rank-counting persistence step below depends on. ---
        async def _resolve_one(r: Dict[str, Any]) -> Dict[str, Any]:
            sel_url = r["selection"].get("url", "")
            if r["cached"]:
                text = r["cached"]["transcript_json"].get("text", "")
                await eval_logging_service.log_segment_transcript(
                    run_id,
                    article_id=r["article_id"],
                    segment_type=r["segment_type"],
                    text=text,
                    cache_hit=True,
                    word_count=r["cached"]["transcript_json"].get("word_count"),
                )
                # Faithfulness_article judging is now always synchronous and
                # inline (see faithfulness_regen_service.py) — a cache hit
                # just reuses the memoized verdict (or judges once, if this
                # cached row predates the feature) and logs it, same as the
                # old post-hoc automated pass used to, just moved earlier.
                # No regeneration here: this text is shared/cached, possibly
                # already served to other users, so rewriting it wouldn't be
                # visible to whoever already has it.
                await faithfulness_regen_service.verify_cached_segment(
                    run_id=run_id, article_id=r["article_id"], url=sel_url,
                    segment_type=r["segment_type"], is_local=r["is_local"],
                    text=text, title=r["selection"].get("title", ""),
                    source_text=r["selection"].get("description", ""), source_available=True,
                    published_date=published_date_by_url.get(sel_url),
                )
                return {
                    "article_id": r["article_id"],
                    "segment_type": r["segment_type"],
                    "is_local": r["is_local"],
                    "cache_hit": True,
                    "mp3_url": r["cached"]["mp3_url"],
                    "duration_s": r["cached"]["duration_s"],
                    "title": r["selection"].get("title", ""),
                    "text": text,
                    "reason": r["selection"].get("reason", ""),
                }

            async with _SEGMENT_SEMAPHORE:
                # Generate -> judge -> regenerate on a critical/moderate flag
                # (up to 2 retries) -> judge again, synchronously, so a
                # flagged claim never reaches a brief marked "ready" (see
                # faithfulness_regen_service.py and faithfulness-judge-plan.md
                # section 5). Same return shape as run_article_segment_step,
                # so nothing below this call needs to change.
                seg_result = await faithfulness_regen_service.generate_verified_segment(
                    ArticleSegmentRequest(
                        title=r["selection"].get("title", ""),
                        source=r.get("source"),
                        full_text=r.get("full_text", ""),
                        segment_type=r["segment_type"],
                        content_fetched=r.get("content_fetched", True),
                    ),
                    api_key=api_key, run_id=run_id,
                    article_id=r["article_id"], url=sel_url,
                    segment_type=r["segment_type"], is_local=r["is_local"],
                    source_available=r.get("content_fetched", True),
                    published_date=published_date_by_url.get(sel_url),
                )
            duration_s = cache_service.estimate_duration_s(seg_result["word_count"])
            mp3_url = cache_service.placeholder_mp3_url(
                "segment", f"{r['article_id']}-{r['segment_type']}-{r['is_local']}"
            )
            await cache_service.put_cached_segment(
                article_id=r["article_id"],
                segment_type=r["segment_type"],
                is_local=r["is_local"],
                transcript_json={"text": seg_result["text"], "word_count": seg_result["word_count"]},
                mp3_url=mp3_url,
                duration_s=duration_s,
            )
            # Memoize AFTER put (put nulls the memo columns) so the shared
            # cache row carries the shipped attempt's verdict.
            fv = seg_result.get("faithfulness")
            if fv and fv.get("severity") not in (None, "unknown"):
                await cache_service.set_cached_segment_faithfulness(
                    str(r["article_id"]), r["segment_type"], r["is_local"],
                    fv["severity"], {"claims": fv["claims"]},
                )
            await eval_logging_service.log_segment_transcript(
                run_id,
                article_id=r["article_id"],
                segment_type=r["segment_type"],
                text=seg_result["text"],
                cache_hit=False,
                word_count=seg_result["word_count"],
                model=seg_result.get("model"),
                input_tokens=seg_result.get("input_tokens"),
                output_tokens=seg_result.get("output_tokens"),
                latency_ms=seg_result.get("latency_ms"),
                simulated=seg_result.get("simulated"),
            )
            return {
                "article_id": r["article_id"],
                "segment_type": r["segment_type"],
                "is_local": r["is_local"],
                "cache_hit": False,
                "mp3_url": mp3_url,
                "duration_s": duration_s,
                "title": r["selection"].get("title", ""),
                "text": seg_result["text"],
                "reason": r["selection"].get("reason", ""),
            }

        segment_results: List[Dict[str, Any]] = await asyncio.gather(*[_resolve_one(r) for r in resolved])

        # --- Persist daily_brief_articles (rank disambiguates standard 1/2/3) ---
        rank_counters = {"lead": 0, "standard": 0, "local": 0}
        for sr in segment_results:
            rank_counters[sr["segment_type"]] += 1
            cached_row = await cache_service.get_cached_segment(sr["article_id"], sr["segment_type"], sr["is_local"])
            await cache_service.add_daily_brief_article(
                brief_id=brief_id,
                article_id=sr["article_id"],
                cache_id=cached_row["id"] if cached_row else None,
                segment_type=sr["segment_type"],
                rank=rank_counters[sr["segment_type"]],
                cache_hit=sr["cache_hit"],
                reason=sr["reason"],
            )

        # --- Intro + outro — per-user, per-day, never cached ---
        io_selections = [
            {"title": r["selection"].get("title", ""), "reason": r["selection"].get("reason", "")}
            for r in resolved
        ]
        io_result = await pipeline_manager.run_intro_outro_step(
            IntroOutroRequest(
                display_name=display_name,
                location_name=location_name,
                local_time=local_time,
                weather=weather,
                selections=io_selections,
            ),
            api_key=api_key,
        )
        await eval_logging_service.log_meta_segments(
            run_id,
            intro=io_result["intro"],
            outro=io_result["outro"],
            inputs_used={
                "display_name": display_name,
                "location_name": location_name,
                "weather": weather,
                "local_time": local_time,
                "ranked_order": io_selections,
            },
            model=io_result.get("model"),
            input_tokens=io_result.get("input_tokens"),
            output_tokens=io_result.get("output_tokens"),
            latency_ms=io_result.get("latency_ms"),
            simulated=io_result.get("simulated"),
        )

        intro_mp3_url = cache_service.placeholder_mp3_url("intro", brief_id)
        outro_mp3_url = cache_service.placeholder_mp3_url("outro", brief_id)
        stitched_mp3_url = cache_service.placeholder_mp3_url("stitched", brief_id)

        await cache_service.set_daily_brief_urls(
            brief_id=brief_id,
            intro_mp3_url=intro_mp3_url,
            outro_mp3_url=outro_mp3_url,
            stitched_mp3_url=stitched_mp3_url,
            status="ready",
        )

        # --- "Stitching" = building the ordered manifest + summed duration —
        # no real audio bytes exist anywhere (TTS is never called this
        # iteration), so there's nothing to literally concatenate. ---
        manifest: List[Dict[str, Any]] = [
            _clip("intro", io_result["intro"], intro_mp3_url),
        ]
        for sr in segment_results:
            manifest.append({
                "kind": sr["segment_type"],
                "article_id": sr["article_id"],
                "title": sr["title"],
                "cache_hit": sr["cache_hit"],
                "mp3_url": sr["mp3_url"],
                "duration_s": sr["duration_s"],
                "text": sr["text"],
            })
        manifest.append(_clip("outro", io_result["outro"], outro_mp3_url))

        total_duration_s = round(sum(m["duration_s"] for m in manifest), 1)

        topics_used = [t["name"] for t in chosen_topics] + custom_names
        await cache_service.save_transcript_record(
            user_id=user_id, display_name=display_name, brief_date=brief_date,
            topics_used=topics_used, segments=manifest,
        )

        await eval_logging_service.finish_eval_run(run_id, "completed")
        await eval_checks_service.run_checks_for_run(run_id)
        # Fire-and-forget, after the response below is already fully built —
        # see automated_judging.py. Covers relevance/order/bookend judging
        # (AUTOMATED_JUDGE_SETS["generate_brief"]) — faithfulness_article is
        # NOT in that set: it already ran synchronously above, per segment,
        # inside _resolve_one (see faithfulness_regen_service.py), so the
        # brief below is already the faithfulness-checked version.
        automated_judging.maybe_run_automated_judging(run_id, "generate_brief")
        return {
            "brief_id": brief_id,
            "user_id": user_id,
            "date": brief_date,
            "status": "ready",
            "display_name": display_name,
            "location_name": location_name,
            "segments": manifest,
            "total_duration_s": total_duration_s,
            "stitched_mp3_url": stitched_mp3_url,
            "cache_summary": {
                "hits": sum(1 for sr in segment_results if sr["cache_hit"]),
                "misses": sum(1 for sr in segment_results if not sr["cache_hit"]),
            },
        }
    except Exception as e:
        logger.error(f"[user_brief] user_id={user_id} date={brief_date} failed: {e}")
        await cache_service.mark_daily_brief_failed(brief_id)
        await eval_logging_service.finish_eval_run(run_id, "failed", error=str(e))
        await eval_checks_service.run_checks_for_run(run_id)
        raise
