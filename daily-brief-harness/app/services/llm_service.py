import os
import time
import json
import logging
from typing import Dict, Any, Optional
from anthropic import AsyncAnthropic
from app.config import settings

logger = logging.getLogger(__name__)

# Single model used for every real (non-simulated) step. Exposed as a module
# constant (rather than a local variable inside call_llm) so eval-logging
# code can record which model generated a given transcript/meta-segment.
ACTIVE_MODEL = "claude-haiku-4-5-20251001"

# M3 judge steps use a different (stronger) model than the generator, per
# daily-brief-eval-buildplan.md's explicit self-preference-bias mitigation:
# "use a stronger/different Claude tier to judge than to generate". Judges
# never generate user-facing content, so the extra cost only applies to the
# one-off offline validation runs against the 6 frozen M2 gold profiles, not
# to any live traffic.
JUDGE_MODEL = "claude-sonnet-5"

# step_id -> what runs on the stronger tier and why. 8=relevance,
# 9=order_correctness, 10=faithfulness_article, 11=faithfulness_bookend,
# 12=tone_flow_pairwise, 13=brief_coherence — all judges, upgraded for the
# self-preference-bias reason above. (14=overview_fidelity was removed
# 2026-07-14 — see eval_judges_service.py's module docstring.)
# 3=score_curate is NOT a judge (it's live-traffic generation, so this one
# does carry a real per-brief cost) — upgraded 2026-07-13 because it's the
# hardest reasoning task in the pipeline (cluster up to 150 raw articles
# into real-world events, gate each cluster for validity, then produce a
# full significance ranking, all in one pass) and Haiku was confirmed
# failing it live: 7 separate wire-service pickups of one breaking event
# (Iran strikes moving oil/equity markets) all survived as separate valid
# clusters instead of collapsing to one representative. A same-prompt,
# same-data replay against JUDGE_MODEL correctly collapsed all 7 to a
# single representative — isolating this as a model-capability gap, not a
# prompt-wording gap.
# All of the above get JSON parsing and JUDGE_MODEL instead of ACTIVE_MODEL
# — see the branches in call_llm below. No explicit temperature is sent for
# these (JUDGE_MODEL rejects the kwarg outright as deprecated), unlike
# ACTIVE_MODEL's calls.
STRONG_MODEL_STEP_IDS = {3, 8, 9, 10, 11, 12, 13}

# Standard mock responses to run the harness when no Anthropic API key is provided
# NOTE: Step 3 combines scoring + curation in a single call with purely
# comparative (non-numeric) editorial judgment, returning a full ranked_order
# + a full local_ranked_order (pipeline.run_score_curate_step bridges each
# list's winning slice into `selections` for everything downstream, while the
# full lists themselves reach the dashboard as-is).
MOCK_SCORE_CURATE_RESPONSE = {
    "event_clusters": [
        {"representative_id": 0, "member_ids": [0], "valid": True},
        {"representative_id": 1, "member_ids": [1], "valid": True},
        {"representative_id": 2, "member_ids": [2], "valid": True},
        {"representative_id": 3, "member_ids": [3], "valid": True},
        {"representative_id": 4, "member_ids": [4], "valid": True},
        {"representative_id": 5, "member_ids": [5], "valid": True}
    ],
    "ranked_order": [
        {
            "rank": 1,
            "id": 0,
            "title": "Anthropic updates tool-use API to support native stateful conversations",
            "reason": "Top scoring article. Represents a massive workflow unlock for stateful agent builders, directly resonant with the user's active tool-building focus."
        },
        {
            "rank": 2,
            "id": 1,
            "title": "Google announces $4 billion AI data center cluster in Hyderabad",
            "reason": "Huge physical and educational infrastructure investment in India, intersecting with the user's technology and regional interest chips."
        },
        {
            "rank": 3,
            "id": 2,
            "title": "India widens semiconductor subsidy to cover packaging (ATMP) facilities",
            "reason": "Pragmatic legislative update that complements Google's hardware infrastructure investment and aligns with user's saved chip design roadmap."
        },
        {
            "rank": 4,
            "id": 3,
            "title": "Zepto closes $350M funding round at $6B valuation, eyes private labels",
            "reason": "High-impact quick-commerce market signal showcasing massive valuations and business strategy redirection."
        },
        {
            "rank": 5,
            "id": 4,
            "title": "EU regulators open new inquiry into cloud pricing practices"
        },
        {
            "rank": 6,
            "id": 5,
            "title": "Startup raises $40M seed round for satellite-based farm monitoring"
        }
    ],
    "local_ranked_order": [
        {
            "rank": 1,
            "id": 6,
            "title": "Namma Metro purple line extension to Bangalore airport ahead of schedule",
            "reason": "Selected for the local slot — concrete, near-term update relevant to the user's home location."
        },
        {
            "rank": 2,
            "id": 7,
            "title": "Bangalore civic body announces pothole repair drive ahead of monsoon"
        }
    ]
}

# Mock responses for Step 6 (per-article segment transcript — see
# pipeline.run_article_segment_step / app/templates/prompts/article_transcript_{lead,standard,local}.py)
# and Step 7 (intro+outro — see pipeline.run_intro_outro_step /
# app/templates/prompts/intro_outro.py). Both are cacheable-segment
# concepts specific to the two-phase Pre-Opt + Per-User Brief Cache pipeline.
MOCK_SEGMENT_LEAD = """So here's the headline everyone's talking about today. Anthropic just rolled out a major update to its tool-use API, and it's exactly the kind of thing that quietly changes how agents get built. Up until now, if you were building anything that called tools in a loop, you had to manually stitch the conversation history back together every single time — pass the whole thing back, every call, just to keep the model's memory intact. That's a lot of glue code for something that should just work. Now the API handles it natively. It remembers what happened across a whole chain of tool calls, without you shipping the entire history back and forth every time. The result is less boilerplate, less networking overhead, and noticeably lower latency once you're a few steps deep into an agent loop. For anyone actually shipping agents right now, this is the kind of unglamorous infrastructure win that saves a real amount of engineering time."""

MOCK_SEGMENT_STANDARD = """On the business side, there's a nice example of infrastructure meeting workforce development. Google just committed four billion dollars to build a new AI data center cluster in Hyderabad — and what's interesting is they're not just dropping server racks and walking away. They're also setting up a curriculum program with local engineering schools, so software talent gets trained right alongside the hardware getting built. It's a pattern worth watching: pairing a physical infrastructure bet with an actual talent pipeline, instead of assuming the workforce shows up later on its own."""

MOCK_SEGMENT_LOCAL = """Quick local note before we wrap up: the Namma Metro purple line extension out to the airport is reportedly running ahead of schedule, with transit officials now pointing to an August opening instead of the original fall target. If you've been dreading the drive out to the airport, that's genuinely good news — a few extra months of construction dust now for a much shorter commute later."""

MOCK_INTRO_OUTRO_RESPONSE = {
    "intro": "Hey there, hope your day's off to a good start! Good to have you back for another rundown of what's going on today. Coming up: a big tool-use API upgrade out of Anthropic, Google's massive new AI data center bet in Hyderabad, a semiconductor packaging policy shift, a quick-commerce funding round that's turning heads, and a local transit update to close things out. Let's get into it.",
    "outro": "That's everything for today. Go get into your day, I'll catch you back here tomorrow with whatever happens next."
}

# Mock responses for M3's judge steps (8-13, see STRONG_MODEL_STEP_IDS above) — only
# ever exercised in simulated mode (no ANTHROPIC_API_KEY configured). Judges
# are meaningless against fake data, but the shapes below at least let the
# orchestrator/storage/UI code path be exercised end-to-end without a key.
MOCK_RELEVANCE_JUDGE_RESPONSE = {
    "labels": [
        {"id": 0, "label": "hit", "reason": "Directly matches the listener's stated tech interest."},
        {"id": 1, "label": "miss", "reason": "Not connected to any stated interest or the listener's location."},
    ]
}

MOCK_ORDER_CORRECTNESS_JUDGE_RESPONSE = {
    "agree": True,
    "alternative_id": None,
    "reasoning": "The chosen story has the broadest real-world impact in the pool.",
    "order_ranking_agree": True,
    "order_ranking_reasoning": "Ranks 2 through the cutoff are in a defensible significance order.",
}

MOCK_FAITHFULNESS_JUDGE_RESPONSE = {
    "claims": [],
    "worst_severity": "none",
}

# Step 11 (faithfulness_bookend) got its own combined-call shape
# (2026-07-14, see eval_judges_service.judge_faithfulness_bookend) — nested
# per-script judgments instead of MOCK_FAITHFULNESS_JUDGE_RESPONSE's flat
# one, so it needs its own mock.
MOCK_FAITHFULNESS_BOOKEND_JUDGE_RESPONSE = {
    "intro": {"claims": [], "worst_severity": "none"},
    "outro": {"claims": [], "worst_severity": "none"},
}

MOCK_TONE_FLOW_PAIRWISE_JUDGE_RESPONSE = {
    "winner": "tie",
    "reasoning": "Both scripts read naturally with comparable pacing.",
}

MOCK_BRIEF_COHERENCE_JUDGE_RESPONSE = {
    "coherence_score": 4,
    "weakest_transition": None,
    "reasoning": "Segments flow into each other without jarring transitions.",
}


class LLMService:
    def __init__(self):
        # Instantiate the Async client if the key is available
        self.api_key = settings.ANTHROPIC_API_KEY
        self.client = AsyncAnthropic(api_key=self.api_key) if self.api_key else None

    def is_api_available(self, custom_key: Optional[str] = None) -> bool:
        return bool(custom_key or self.client)

    async def call_llm(
        self,
        system_prompt: str,
        user_content: str,
        step_id: int,
        prompt_override: Optional[str] = None,
        segment_type: Optional[str] = None,
        api_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Executes a call to Anthropic API using either the passed custom key or default server key.
        If no key is configured on either level, falls back to simulated response generator.
        """
        start_time = time.time()
        
        # Determine prompt content
        active_system = system_prompt
        active_user = user_content
        
        if prompt_override:
            # If the user edited the prompt, use it
            active_system = prompt_override

        # Determine client to use: dynamic custom key takes priority
        active_client = None
        key_to_use = api_key or self.api_key
        
        if key_to_use:
            try:
                active_client = AsyncAnthropic(api_key=key_to_use)
            except Exception as e:
                logger.error(f"Failed to initialize AsyncAnthropic with custom key: {e}")

        # If no client is available, simulate a realistic response
        if not active_client:
            logger.warning(f"No ANTHROPIC_API_KEY available for Step {step_id}. Simulating response.")
            # Yield control so it acts async
            import asyncio
            await asyncio.sleep(1.2)
            
            latency = int((time.time() - start_time) * 1000)
            
            # Select proper mock output based on the pipeline step
            if step_id == 3:
                mock_out = MOCK_SCORE_CURATE_RESPONSE
            elif step_id == 6:
                if segment_type == "lead":
                    mock_out = {"transcript": MOCK_SEGMENT_LEAD}
                elif segment_type == "local":
                    mock_out = {"transcript": MOCK_SEGMENT_LOCAL}
                else:
                    mock_out = {"transcript": MOCK_SEGMENT_STANDARD}
            elif step_id == 7:
                mock_out = MOCK_INTRO_OUTRO_RESPONSE
            elif step_id == 8:
                mock_out = MOCK_RELEVANCE_JUDGE_RESPONSE
            elif step_id == 9:
                mock_out = MOCK_ORDER_CORRECTNESS_JUDGE_RESPONSE
            elif step_id == 10:
                mock_out = MOCK_FAITHFULNESS_JUDGE_RESPONSE
            elif step_id == 11:
                mock_out = MOCK_FAITHFULNESS_BOOKEND_JUDGE_RESPONSE
            elif step_id == 12:
                mock_out = MOCK_TONE_FLOW_PAIRWISE_JUDGE_RESPONSE
            elif step_id == 13:
                mock_out = MOCK_BRIEF_COHERENCE_JUDGE_RESPONSE
            else:
                mock_out = {"status": "success", "message": f"Simulated execution of Step {step_id}"}

            return {
                "text": mock_out["transcript"] if step_id == 6 else json.dumps(mock_out, indent=2),
                "parsed": mock_out,
                "latency_ms": latency,
                "input_tokens": 1240,
                "output_tokens": 850,
                "simulated": True,
                "model": "simulated"
            }

        try:
            # Determine standard model — judge steps (plus score_curate, see
            # STRONG_MODEL_STEP_IDS above) use a stronger/different tier than
            # the rest of the generator.
            model = JUDGE_MODEL if step_id in STRONG_MODEL_STEP_IDS else ACTIVE_MODEL

            # Step 3 emits a full ranked_order + local_ranked_order (plus,
            # now that it's on JUDGE_MODEL, event_clusters' own grouping
            # reasoning) for the whole pool in one response — needs a much
            # larger budget than the other steps, scaling with pool size, to
            # avoid truncation. Step 8 (relevance judge) similarly labels
            # every article in a pool, not just a handful, and JUDGE_MODEL
            # burns a large, non-optional chunk of max_tokens on its own
            # extended thinking before any output text — confirmed live
            # against a real 54-article non-local pool: 6000 was entirely
            # consumed by thinking (5391 thinking tokens), leaving no room for
            # output and truncating the JSON to nothing usable, which silently
            # produced zero relevance rows for that pool. 16000 was verified to
            # complete cleanly (end_turn, ~7.4k total output tokens) on that
            # same pool with headroom to spare for larger ones. Step 3's own
            # pool can be up to MAX_ARTICLES_FOR_SCORING (150, pipeline.py) —
            # nearly 3x step 8's 54-article case — and a same-prompt test
            # against just 35 articles already hit max_tokens at 12000, so
            # 12000 is not viable now that step 3 is on JUDGE_MODEL. Can't
            # just raise this arbitrarily high, though: the Anthropic SDK
            # hard-rejects non-streaming calls above ~21333 (its own
            # 10-minute-timeout estimate, anthropic/_base_client.py's
            # _calculate_nonstreaming_timeout — confirmed live, 32000 raised
            # ValueError before the request was even sent). 20000 is the
            # largest safe margin under that ceiling; call_llm has no
            # streaming support, so this genuinely is the ceiling until that's
            # added, not a number tuned for pool size.
            if step_id == 3:
                max_tokens = 20000
            elif step_id == 8:
                max_tokens = 16000
            elif step_id == 9:
                # Step 9 (order-correctness judge) now answers two questions
                # per call instead of one (added an order-ranking verdict
                # alongside the top-pick verdict) — bumped preemptively given
                # step 8's already-demonstrated pattern of JUDGE_MODEL burning
                # most of a similarly-modest budget on thinking tokens alone.
                max_tokens = 8000
            elif step_id == 10:
                # Step 10 (faithfulness_article) got a web_search tool
                # (2026-07-14, see faithfulness-judge-plan.md) — the response
                # now carries thinking + multiple search rounds' worth of
                # tool-result content + reasoning before the final claims
                # JSON, on top of the same thinking-eats-the-budget pattern
                # already confirmed for step 8. The generic 4000 default is
                # not enough headroom for a 4-8 claim segment with search on.
                max_tokens = 12000
            elif step_id == 11:
                # Step 11 (faithfulness_bookend) was combined into one call
                # judging intro+outro together (2026-07-14, was two separate
                # 4000-budget calls) — the response now carries two claims
                # lists + two explanations instead of one, roughly doubling
                # expected output size, on top of the usual thinking-budget
                # risk. Bumped for headroom, not maxed like step 10 since
                # bookend scripts are short and there's no web search here.
                max_tokens = 6000
            else:
                max_tokens = 4000

            # JUDGE_MODEL rejects an explicit temperature outright ("`temperature`
            # is deprecated for this model" — a real 400 from the Anthropic API,
            # not a range/validation issue), so the kwarg is only ever included
            # for ACTIVE_MODEL calls, never omitted-but-defaulted for the
            # strong-tier steps.
            create_kwargs = dict(
                model=model,
                max_tokens=max_tokens,
                system=active_system,
                messages=[{"role": "user", "content": active_user}],
            )
            if step_id not in STRONG_MODEL_STEP_IDS:
                create_kwargs["temperature"] = 0.7

            if step_id == 10:
                # Faithfulness-article judge only (see faithfulness-judge-plan.md)
                # — server-executed web search, no client-side tool loop or
                # beta header needed. JUDGE_MODEL (claude-sonnet-5) supports
                # the dynamic-filtering variant.
                create_kwargs["tools"] = [{"type": "web_search_20260209", "name": "web_search"}]

            logger.info(f"Invoking Claude via Anthropic API (Step {step_id})")
            response = await active_client.messages.create(**create_kwargs)

            latency = int((time.time() - start_time) * 1000)
            # JUDGE_MODEL can return a leading ThinkingBlock before the actual
            # TextBlock (extended-thinking content), so content[0] isn't
            # reliably the text. With web search on (step 10), the response
            # can also interleave server_tool_use/web_search_tool_result
            # blocks with several text blocks (reasoning before/after each
            # search) — none of those have a `.text` attribute except the
            # text blocks themselves, and the final answer is always the
            # *last* text block, not the first.
            text_blocks = [b for b in response.content if hasattr(b, "text")]
            output_text = text_blocks[-1].text if text_blocks else ""

            if response.stop_reason == "max_tokens":
                logger.warning(f"Step {step_id} response was truncated at max_tokens={max_tokens} — JSON parsing will likely fail.")
            elif response.stop_reason == "pause_turn":
                # Server-side tool loop (web search) hit its iteration cap
                # mid-turn. call_llm has no multi-turn resume support, so
                # this call's output is likely incomplete — logged so it's
                # distinguishable from an ordinary parse failure if it shows
                # up in practice, without adding a resume loop for what
                # should be a rare edge case on a handful of search rounds.
                logger.warning(f"Step {step_id} response paused mid-turn (stop_reason=pause_turn) — server-side tool loop hit its cap; output may be incomplete.")

            # Try to parse as JSON if it's not a plain-transcript step
            # (6 returns plain spoken text; 3, 7, and every judge step return JSON).
            parsed_data = None
            if step_id in [3, 7] or step_id in STRONG_MODEL_STEP_IDS:
                try:
                    # Strip any markdown backticks if Claude returns it in a codeblock
                    cleaned = output_text.strip()
                    if cleaned.startswith("```json"):
                        cleaned = cleaned[7:]
                    if cleaned.startswith("```"):
                        cleaned = cleaned[3:]
                    if cleaned.endswith("```"):
                        cleaned = cleaned[:-3]
                    parsed_data = json.loads(cleaned.strip())
                except Exception as e:
                    logger.error(f"Failed to parse JSON response for Step {step_id}: {e}")
            
            return {
                "text": output_text,
                "parsed": parsed_data or output_text,
                "latency_ms": latency,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "simulated": False,
                "model": model
            }
            
        except Exception as e:
            logger.error(f"Anthropic API call failed for Step {step_id}: {e}")
            raise e

llm_service = LLMService()
