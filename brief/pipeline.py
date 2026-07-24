import json
import time
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from brief.llm import call_llm


async def _call(system_prompt: str, user_content: str, step: str, json_response: bool = True) -> dict:
    """Compat shim: preserves the harness's rich result-dict shape at the three
    call sites while routing through brief.llm (core/llm_config bindings)."""
    t0 = time.time()
    out = await call_llm(system_prompt, user_content, step=step, json_response=json_response)
    return {
        "parsed": out if json_response else None,
        "text": out if not json_response else "",
        "latency_ms": int((time.time() - t0) * 1000),
        "simulated": False, "model": None,
        "input_tokens": None, "output_tokens": None,
    }
from brief import scoring as scoring_service
from brief import enrichment as enrichment_service
from brief import prompts

from loguru import logger

# Safety-net cap on how many raw articles get sent into the combined Score &
# Curate LLM call — a guard against pathologically large fetches (e.g. many
# active interests at once, or Step 2b's pre-filter toggled off) blowing up
# input tokens/cost and output length, not a truncation workaround.
MAX_ARTICLES_FOR_SCORING = 150

# SYSTEM_SCORE_CURATE_PROMPT returns a full `ranked_order` of every
# deduplicated non-local story; only its first 4 entries are used here,
# matching the 4 global segment slots one-to-one. `local_ranked_order`'s
# first entry (if any) maps to the 5th and final "Local Pulse" slot.
GLOBAL_SLOT_NAMES = ["Main Story", "Supporting Story 1", "Supporting Story 2", "Supporting Story 3"]

# ---------------------------------------------------------
# PYDANTIC SCHEMAS FOR API INPUTS/OUTPUTS
# ---------------------------------------------------------
class SaveItem(BaseModel):
    title: str
    url: str
    summary: Optional[str] = None

class UserProfile(BaseModel):
    name: str = "Sora"
    interests: List[str] = Field(default_factory=list)
    custom_topics: List[str] = Field(default_factory=list)  # Freeform topic strings, parallel to Beats — each its own independent fetch query
    location: str = ""
    country: str = ""  # ISO-3166-1 alpha-2, e.g. "IN" — see news_service.BEAT_GEO_MODE
    saves: List[SaveItem] = Field(default_factory=list)
    days_since_last_brief: int = 1
    unlistened_queue: List[str] = Field(default_factory=list)
    structure: str = "Structure 1"  # "Structure 1" or "Structure 2"
    voice: str = "Voice C"          # "Voice A" or "Voice C"

    def all_topics(self) -> List[str]:
        """Beats + Custom Topics combined, for display/context in LLM prompts —
        not for news_service, which takes `interests`/`custom_topics` separately."""
        return self.interests + self.custom_topics

class RawArticle(BaseModel):
    title: str
    description: str
    source: Optional[str] = None
    url: str
    published_date: str
    topic: str
    # Other outlets covering the same event (see scoring_service's
    # cluster_alternates) — only populated when Step 2b's clustering ran.
    # Used by Step 3b as same-story fallback candidates; never shown to the LLM.
    cluster_alternates: List[Dict[str, str]] = Field(default_factory=list)

# Request Schemas for individual steps
class RankRequest(BaseModel):
    user_profile: UserProfile
    articles: List[RawArticle]
    keep_fraction: float = scoring_service.DEFAULT_KEEP_FRACTION

class ScoreRequest(BaseModel):
    user_profile: UserProfile
    articles: List[RawArticle]
    prompt_override: Optional[str] = None

# class CurateRequest(BaseModel):  # DISABLED — Step 4 (curate) merged into Step 3 (score_curate)
#     user_profile: UserProfile
#     scored_articles: List[Dict[str, Any]]
#     prompt_override: Optional[str] = None

class ContentFetchRequest(BaseModel):
    selections: List[Dict[str, Any]]

# Word ranges for Step 6's per-article segment transcripts (see
# run_article_segment_step / prompts.SYSTEM_ARTICLE_TRANSCRIPT_*_PROMPT).
SEGMENT_WORD_RANGES = {
    "lead": (130, 180),
    "standard": (90, 130),
    "local": (70, 100),
}

class ArticleSegmentRequest(BaseModel):
    title: str
    source: Optional[str] = None
    full_text: str
    segment_type: str  # "lead" | "standard" | "local"
    content_fetched: bool = True  # False when full_text is really just a headline/description — see run_article_segment_step
    # Set together, only on a regeneration retry (see faithfulness_regen_service.py
    # and faithfulness-judge-plan.md section 5) — prior_text is the flagged
    # draft, prior_feedback is that draft's flagged claims, pre-formatted as
    # a bullet list by the caller. Both None on a first attempt.
    prior_text: Optional[str] = None
    prior_feedback: Optional[str] = None

class IntroOutroRequest(BaseModel):
    display_name: str
    location_name: str
    local_time: str = ""  # e.g. "2026-07-06 14:32" — see weather_service.get_weather_and_local_time
    weather: str = ""     # e.g. "Sunny, 28.0°C"
    selections: List[Dict[str, Any]]  # [{"title", "reason"}], in broadcast order (lead, standards, local)

# Pipeline Executor Class
class PipelineManager:
    async def run_rank_step(self, req: RankRequest) -> Dict[str, Any]:
        """Step 2b: No-LLM pre-filter ranking (see scoring_service.rank_articles).

        Runs entirely locally (embeddings + heuristics, no Claude call, plus an
        optional scraped-article enrichment pass — see enrichment_service.py)
        to cut the raw fetch pool down before it's sent to the Score & Curate
        LLM step — a recall-safe cut, not a precision-final ranking, so callers
        should send the `included` subset onward and treat `ranked` as
        display-only detail.
        """
        t0 = time.time()
        articles = [a.model_dump() for a in req.articles]
        result = await scoring_service.rank_articles(
            articles=articles,
            interests=req.user_profile.interests,
            keep_fraction=req.keep_fraction,
        )
        result["latency_ms"] = int((time.time() - t0) * 1000)
        return result

    async def run_score_curate_step(self, req: ScoreRequest, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Step 3: Score & curate raw articles into editorial slots in a single combined LLM call."""
        articles = [a.model_dump() for a in req.articles]
        if len(articles) > MAX_ARTICLES_FOR_SCORING:
            logger.warning(
                f"Article pool ({len(articles)}) exceeds MAX_ARTICLES_FOR_SCORING "
                f"({MAX_ARTICLES_FOR_SCORING}); truncating to avoid JSON truncation on output."
            )
            articles = articles[:MAX_ARTICLES_FOR_SCORING]

        # Tag each article with a stable numeric id before it goes into the
        # prompt, so the model can reference "which article" reliably by
        # copying back a bare integer instead of reproducing a long,
        # punctuation-heavy title verbatim (see _to_selection below — titles
        # are for display only, id is the only thing ever used to look an
        # article back up).
        for i, a in enumerate(articles):
            a["id"] = i

        # cluster_alternates is plumbing for Step 3b's fallback fetch, not
        # editorial content — keep it out of what the model actually sees.
        articles_for_prompt = [
            {k: v for k, v in a.items() if k != "cluster_alternates"} for a in articles
        ]
        user_content = prompts.USER_SCORE_CURATE_PROMPT.format(
            interests=", ".join(req.user_profile.all_topics()),
            location=req.user_profile.location,
            articles=json.dumps(articles_for_prompt, indent=2)
        )

        result = await _call(
            system_prompt=prompts.SYSTEM_SCORE_CURATE_PROMPT,
            user_content=user_content,
            step="score_curate",
        )

        # Bridge the model's ranked_order/local_ranked_order output into the
        # selections shape that the Outline step, history storage, and the
        # harness UI expect, so nothing downstream needs to know about the
        # Step 3 output schema. The full rankings themselves are left in
        # `parsed` untouched — the dashboard renders them directly (see
        # renderRankedOrderTable in app.js) — only their winning slice feeds
        # `selections`.
        #
        # Step 3b (run_content_fetch_step below) needs each selection's real
        # url to fetch its full article body. Recover it here via the `id`
        # every item is required to echo back (see the `id` tagging above) —
        # a reliable integer lookup, not a guess based on whether the model
        # reproduced a long title byte-for-byte. A missing/invalid id (the
        # model should never omit it, but nothing here assumes it's perfect)
        # just leaves that selection without a url — Step 3b treats it like
        # any other fetch failure.
        articles_by_id = {a["id"]: a for a in articles}

        def _to_selection(item: Dict[str, Any], slot_name: str) -> Dict[str, Any]:
            source_article = articles_by_id.get(item.get("id"), {})
            return {
                "title": item.get("title", ""),
                "slot": slot_name,
                "reason": item.get("reason", ""),
                "url": source_article.get("url", ""),
                "source": source_article.get("source", ""),
                "description": source_article.get("description", ""),
                "cluster_alternates": source_article.get("cluster_alternates", []),
            }

        parsed = result.get("parsed")
        if isinstance(parsed, dict) and "ranked_order" in parsed:
            ranked_order = parsed.get("ranked_order") or []
            local_ranked_order = parsed.get("local_ranked_order") or []

            # Resolve each item's real url via its "id" (see the tagging
            # above) so eval logging can key rows by (run_id, url) like the
            # rest of the schema — additive field, existing consumers (the
            # dashboard's renderRankedOrderTable) only read known keys.
            for item in ranked_order:
                item["url"] = articles_by_id.get(item.get("id"), {}).get("url", "")
            for item in local_ranked_order:
                item["url"] = articles_by_id.get(item.get("id"), {}).get("url", "")

            top_4 = ranked_order[:4]
            local_pick = local_ranked_order[0] if local_ranked_order else None

            selections = [
                _to_selection(item, slot_name)
                for slot_name, item in zip(GLOBAL_SLOT_NAMES, top_4)
            ]
            if local_pick and local_pick.get("title"):
                selections.append(_to_selection(local_pick, "Local Pulse"))

            parsed["selections"] = selections
            parsed["cuts"] = []

        return result

    async def run_content_fetch_step(self, req: ContentFetchRequest) -> Dict[str, Any]:
        """Step 3b: fetch real article body text for the articles Step 3 selected
        (see enrichment_service.fetch_full_texts). Each selection's own url is
        tried first; if that fails, same-story alternates from Step 2b's
        clustering (`cluster_alternates`) are tried before giving up. Only if
        the url AND every alternate fail does that one selection fall back to
        its existing `description` — never blocks the others."""
        selections = [dict(s) for s in req.selections]

        results = await enrichment_service.fetch_full_texts(selections)

        fetched_count = 0
        for s, result in zip(selections, results):
            s.pop("cluster_alternates", None)  # internal-only; don't leak into Outline/Transcript
            if result:
                s["full_text"] = result["text"]
                s["content_fetched"] = True
                s["resolved_url"] = result.get("resolved_url")
                s["used_alternate_source"] = result["url"] != s.get("url")
                if s["used_alternate_source"] and result.get("source"):
                    s["source"] = result["source"]
                fetched_count += 1
            else:
                s["full_text"] = s.get("description", "")
                s["content_fetched"] = False
                s["used_alternate_source"] = False

        return {
            "selections": selections,
            "fetched_count": fetched_count,
            "total_count": len(selections),
        }

    async def run_article_segment_step(self, req: ArticleSegmentRequest, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Step 6: Write a single self-contained segment transcript for one
        article (lead/standard/local). Unlike Step 5, this has no outline and
        no awareness of neighboring segments — the output is cached by
        (article_id, segment_type) and reused verbatim across different
        users'/days' briefs (see cache_service, preopt_runner, user_brief_runner)."""
        system_prompt = {
            "lead": prompts.SYSTEM_ARTICLE_TRANSCRIPT_LEAD_PROMPT,
            "standard": prompts.SYSTEM_ARTICLE_TRANSCRIPT_STANDARD_PROMPT,
            "local": prompts.SYSTEM_ARTICLE_TRANSCRIPT_LOCAL_PROMPT,
        }.get(req.segment_type, prompts.SYSTEM_ARTICLE_TRANSCRIPT_STANDARD_PROMPT)

        # content_fetched=False means full_text is really just the RSS
        # headline/description (real fetching failed) — the model isn't told
        # this by full_text alone, so it invents specific facts/quotes/
        # numbers freely. Traced to 11/12 "critical" faithfulness-judge
        # flags in the M3 judging analysis (2026-07-12). This note is the
        # fix: an explicit signal + instruction, not silent full_text reuse.
        source_note = (
            ""
            if req.content_fetched
            else (
                "\n\nNOTE: only a headline/short description was available for this "
                "article — no real article body was successfully fetched. Do NOT invent "
                "specific facts, figures, quotes, or details beyond what's given above. "
                "Keep the segment general and grounded only in the headline/description "
                "provided, rather than fabricating specifics you don't actually have."
            )
        )
        regen_block = (
            prompts.USER_ARTICLE_SEGMENT_REGEN_BLOCK.format(
                flagged_claims=req.prior_feedback, prior_text=req.prior_text,
            )
            if req.prior_text
            else ""
        )
        user_content = prompts.USER_ARTICLE_SEGMENT_PROMPT.format(
            title=req.title,
            source=req.source or "Unknown",
            full_text=req.full_text,
            source_note=source_note,
            regen_block=regen_block,
        )

        result = await _call(
            system_prompt=system_prompt,
            user_content=user_content,
            step="segment",
            json_response=False,
        )

        text = result.get("text", "").strip()
        word_count = len(text.split())

        # Unlike Step 5, no corrective retry — this is a much shorter,
        # lower-stakes single-segment write; just log so a systematically
        # over/under-shooting prompt is visible without extra LLM spend.
        low, high = SEGMENT_WORD_RANGES.get(req.segment_type, SEGMENT_WORD_RANGES["standard"])
        if not result.get("simulated") and not (low <= word_count <= high):
            logger.warning(
                f"Segment transcript for '{req.title}' ({req.segment_type}) is {word_count} words, "
                f"outside the expected [{low}, {high}] range — not retried, logged for visibility only."
            )

        return {
            "text": text,
            "word_count": word_count,
            "latency_ms": result.get("latency_ms", 0),
            "simulated": result.get("simulated", False),
            "model": result.get("model"),
            "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"),
        }

    async def run_intro_outro_step(self, req: IntroOutroRequest, api_key: Optional[str] = None) -> Dict[str, Any]:
        """Step 7: Per-user, per-day intro+outro. Unlike Step 6's
        per-article segments, this is NEVER cached — it's inherently specific
        to one listener's brief on one day (see prompts.SYSTEM_INTRO_OUTRO_PROMPT).
        "intro" also carries the story-preview content that used to be a
        separate "glimpse" field — merged into one continuous opener."""
        selections_str = "\n".join(
            f"- {s.get('title', '')} — {s.get('reason', '')}" for s in req.selections
        )
        user_content = prompts.USER_INTRO_OUTRO_PROMPT.format(
            display_name=req.display_name,
            location_name=req.location_name,
            local_time=req.local_time or "unknown",
            weather=req.weather or "pleasant",
            selections=selections_str or "(none)",
        )

        result = await _call(
            system_prompt=prompts.SYSTEM_INTRO_OUTRO_PROMPT,
            user_content=user_content,
            step="bookends",
        )

        parsed = result.get("parsed")
        if not isinstance(parsed, dict):
            parsed = {}
        return {
            "intro": parsed.get("intro", ""),
            "outro": parsed.get("outro", ""),
            "latency_ms": result.get("latency_ms", 0),
            "simulated": result.get("simulated", False),
            "model": result.get("model"),
            "input_tokens": result.get("input_tokens"),
            "output_tokens": result.get("output_tokens"),
        }

pipeline_manager = PipelineManager()
