"""
intelligence/brief_generator.py
Generate a daily brief transcript for a user.

Pipeline:
  1. Fetch user context (KB, recent saves, recent listens, unlistened episodes)
  2. Fetch news broadly via Google News RSS — up to 8 articles per topic, no cap
  3. LLM: Filter articles to those relevant to user interests (12-18 articles)
  4. LLM: Curate shortlist to 4-6 stories that fit 5-8 minute budget
  5. LLM: Outline segment structure and order
  6. LLM: Generate full transcript from outline
"""

from __future__ import annotations

import json
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Literal

import anthropic
import httpx
from loguru import logger

from core.db.connection import db_fetchrow, db_query
from core.kb import UserKB


SegmentType = Literal[
    "intro_morning", "intro_afternoon", "intro_evening", "intro_neutral",
    "activity", "headline", "outro",
]


@dataclass
class Segment:
    type: str
    title: str
    text: str
    source_url: str | None = None


@dataclass
class BriefResult:
    user_name: str | None
    topics: list[str]
    save_count_7d: int
    segments: list[Segment]
    news_items: list[dict]


def _make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _strip_fences(raw: str) -> str:
    """Strip markdown code fences. Handles ```json, ```, and mismatched fences."""
    raw = raw.strip()
    if not raw:
        return raw
    if raw.startswith("```"):
        lines = raw.splitlines()
        # find closing fence (search from end)
        end = len(lines) - 1
        while end > 0 and not lines[end].strip().startswith("```"):
            end -= 1
        return "\n".join(lines[1:end]).strip()
    return raw


def _extract_json(raw: str) -> str:
    """Extract JSON object from response that may have surrounding text."""
    raw = _strip_fences(raw)
    if not raw:
        return raw
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _llm_call(client: anthropic.Anthropic, prompt: str, max_tokens: int) -> tuple[str, object]:
    """Make an LLM call. Returns (parsed_json_str, usage)."""
    msg = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = _extract_json(msg.content[0].text.strip())
    if not raw:
        raise ValueError(f"LLM returned empty or unparseable response. Full text: {msg.content[0].text!r}")
    return raw, msg.usage


async def _fetch_user_context(user_id: str) -> dict:
    user_row = await db_fetchrow(
        "SELECT user_kb, email FROM users WHERE id = $uid",
        {"uid": user_id},
    )
    if not user_row:
        raise ValueError(f"User {user_id!r} not found")

    kb = UserKB.model_validate(user_row["user_kb"] or {})

    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    saves = await db_query(
        """SELECT url, title, created_at FROM source
           WHERE user_id = $uid AND hidden = false AND created_at > $since
           ORDER BY created_at DESC LIMIT 20""",
        {"uid": user_id, "since": seven_days_ago},
    )

    recent_episodes = await db_query(
        """SELECT title, play_progress, listened, last_played_at FROM episode
           WHERE user_id = $uid AND last_played_at IS NOT NULL
           ORDER BY last_played_at DESC LIMIT 5""",
        {"uid": user_id},
    )

    unlistened = await db_query(
        """SELECT id, title FROM episode
           WHERE user_id = $uid AND listened = false AND status = 'complete'
           ORDER BY created_at DESC LIMIT 10""",
        {"uid": user_id},
    )

    return {
        "kb": kb,
        "saves": [dict(r) for r in saves],
        "recent_episodes": [dict(r) for r in recent_episodes],
        "unlistened_episodes": [dict(r) for r in unlistened],
    }


async def _fetch_news_broadly(topics: list[str]) -> list[dict]:
    """Fetch up to 8 articles per topic. No filtering — step 3 handles that."""
    items: list[dict] = []
    seen_titles: set[str] = set()

    async with httpx.AsyncClient(timeout=15.0) as client:
        for topic in topics[:8]:
            q = topic.replace(" ", "+")
            url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
            try:
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                root = ET.fromstring(resp.text)
                channel = root.find("channel")
                if channel is None:
                    continue
                count = 0
                for item in channel.findall("item"):
                    if count >= 8:
                        break
                    title = (item.findtext("title") or "").strip()
                    if not title or title in seen_titles:
                        continue
                    seen_titles.add(title)
                    items.append({
                        "topic": topic,
                        "title": title,
                        "link": (item.findtext("link") or "").strip(),
                        "description": (item.findtext("description") or "").strip()[:300],
                        "source": (item.findtext("source") or "").strip(),
                        "pub_date": (item.findtext("pubDate") or "").strip(),
                    })
                    count += 1
            except Exception as exc:
                logger.warning(f"News fetch failed for '{topic}': {exc}")

    return items


# ── Prompt builders ───────────────────────────────────────────────────────────

def _build_filter_prompt(news_items: list[dict], kb: UserKB, topics_override: list[str] | None = None) -> str:
    topics = topics_override or kb.interests.topics or []
    obsession = kb.interests.current_obsession

    articles = "\n".join(
        f"[{i}] [{item['topic']}] {item['title']}"
        + (f"\n    {item['description']}" if item.get("description") else "")
        for i, item in enumerate(news_items)
    )

    return f"""Filter these news articles for relevance to a user's interests.

USER INTERESTS:
Topics: {', '.join(topics)}{f' (currently focused on: {obsession})' if obsession else ''}

ARTICLES:
{articles}

Return ONLY a JSON object, no markdown fences, no commentary:
{{
  "filtered": [
    {{
      "index": <article index 0-based>,
      "relevance_score": <1-10, where 10 = directly on topic>,
      "relevance_reason": "<one short sentence>"
    }}
  ]
}}

Include only articles with relevance_score >= 6. Aim for 12-18. Sort by relevance_score descending.
If fewer than 6 articles score >= 6, include the top 6 regardless."""


def _build_curation_prompt(filtered_items: list[dict], kb: UserKB, topics_override: list[str] | None = None) -> str:
    topics = topics_override or kb.interests.topics or []
    obsession = kb.interests.current_obsession

    articles = "\n".join(
        f"[{i}] (score {item.get('relevance_score', '?')}/10) [{item['topic']}] {item['title']}\n"
        f"    Relevance: {item.get('relevance_reason', '')}"
        for i, item in enumerate(filtered_items)
    )

    return f"""Curate a 5-8 minute daily audio brief. Select 4-6 stories from this shortlist.

TIME BUDGET: 5-8 minutes total. Each headline = ~20 seconds (~50 words). Activity segment = ~45 seconds.
4-6 headlines fills the budget correctly.

USER INTERESTS: {', '.join(topics)}{f' — especially {obsession}' if obsession else ''}

SHORTLISTED ARTICLES:
{articles}

Return ONLY a JSON object, no markdown fences, no commentary:
{{
  "selected": [
    {{
      "index": <0-based index in shortlist above>,
      "reason": "<why this makes the cut — one sentence>",
      "angle": "<what aspect to focus on in the segment>"
    }}
  ],
  "excluded": [
    {{
      "index": <0-based index>,
      "reason": "<why cut — one sentence>"
    }}
  ]
}}

Prioritize variety across topics. Include at least one story adjacent to the user's usual interests."""


def _build_outline_prompt(curated_items: list[dict], kb: UserKB, user_ctx: dict) -> str:
    name = kb.identity.name or "the user"
    topics = kb.interests.topics or []
    saves = user_ctx["saves"]
    recent_eps = user_ctx["recent_episodes"]
    unlistened = user_ctx["unlistened_episodes"]

    save_lines = "\n".join(
        f"  - {s.get('title') or s.get('url', '')}"
        for s in saves[:6]
        if s.get("title") or s.get("url")
    ) or "  (none)"

    recent_listen = ""
    if recent_eps:
        ep = recent_eps[0]
        pct = int((ep.get("play_progress") or 0) * 100)
        status = "completed" if ep.get("listened") else f"{pct}% through"
        recent_listen = f'"{ep["title"]}" ({status})'

    tease_title = unlistened[0]["title"] if unlistened else None

    stories = "\n".join(
        f"[{i}] [{item['topic']}] {item['title']}\n"
        f"    Angle: {item.get('angle', '')}"
        for i, item in enumerate(curated_items)
    )

    return f"""Create the segment structure for {name}'s daily audio brief.

USER CONTEXT:
Name: {name}
Interests: {', '.join(topics)}
Recent saves:
{save_lines}
Most recent listen: {recent_listen or '(none)'}
Unlistened episode to tease: {tease_title or '(none)'}

SELECTED STORIES (in no particular order):
{stories}

Return ONLY a JSON object, no markdown fences, no commentary:
{{
  "outline": {{
    "intro": {{
      "angle": "<one sentence: what personal or timely detail to warm the greeting with>"
    }},
    "activity": {{
      "focus": "<which save or theme to highlight — be specific>",
      "question": "<the open question to end the activity segment with>"
    }},
    "headlines": [
      {{
        "story_index": <0-based index from SELECTED STORIES above>,
        "order": <1-based position in brief>,
        "angle": "<refined angle for the transcript>",
        "length": "short|standard|deep"
      }}
    ],
    "outro": {{
      "tease_episode": {json.dumps(tease_title)},
      "connection": "<how to connect tease to something in today's brief, or null>"
    }}
  }}
}}

Ordering: start with most engaging, end lighter. Mark one story "deep" if it warrants real context.
"short" = 30-40 words, "standard" = 50-80, "deep" = 90-120."""


def _build_transcript_prompt(
    outline_data: dict,
    curated_items: list[dict],
    kb: UserKB,
    user_ctx: dict,
) -> str:
    name = kb.identity.name or "there"
    topics = kb.interests.topics or []
    saves = user_ctx["saves"]
    recent_eps = user_ctx["recent_episodes"]
    unlistened = user_ctx["unlistened_episodes"]
    outline = outline_data["outline"]

    today = datetime.now(timezone.utc).strftime("%A, %B %-d")

    saves_block = ""
    if saves:
        lines = [s.get("title") or s.get("url", "") for s in saves[:6] if s.get("title") or s.get("url")]
        saves_block = f"{len(saves)} saves in the last 7 days:\n" + "\n".join(f"  - {l}" for l in lines)
    else:
        saves_block = "No saves in the last 7 days."

    recent_listen_line = ""
    if recent_eps:
        ep = recent_eps[0]
        pct = int((ep.get("play_progress") or 0) * 100)
        status = "completed" if ep.get("listened") else f"{pct}% through"
        recent_listen_line = f"Most recent listen: \"{ep['title']}\" ({status})"

    tease_title = unlistened[0]["title"] if unlistened else None

    hl_sorted = sorted(outline["headlines"], key=lambda h: h["order"])
    length_words = {"short": "30-40 words", "standard": "50-80 words", "deep": "90-120 words"}
    headline_specs = "\n".join(
        f"[Headline {h['order']}] {curated_items[h['story_index']]['title']}\n"
        f"  Source URL: {curated_items[h['story_index']].get('link', '')}\n"
        f"  Angle: {h['angle']}\n"
        f"  Length: {h['length']} ({length_words.get(h['length'], '50-80 words')})"
        for h in hl_sorted
        if h["story_index"] < len(curated_items)
    )

    activity = outline["activity"]
    intro = outline["intro"]
    outro = outline["outro"]

    tease_instruction = (
        f'Tease this specific unlistened episode by name: "{tease_title}". '
        f'{outro.get("connection") or "Connect it to something from the brief if possible."}'
        if tease_title else
        "No unlistened episodes. Close warmly without a tease."
    )

    return f"""Generate a daily audio brief transcript for {name}. Today is {today}.

USER CONTEXT
Interests: {', '.join(topics)}
{saves_block}
{recent_listen_line}

INTRO ANGLE: {intro['angle']}

ACTIVITY FOCUS: {activity['focus']}
Activity open question: {activity['question']}

HEADLINES TO WRITE (in order):
{headline_specs}

Return ONLY a JSON object, no markdown fences, no commentary, with this exact structure:

{{
  "segments": [
    {{
      "type": "intro_morning",
      "title": "Morning greeting",
      "text": "Warm, 2-sentence greeting. Include day + date. End: '...let\\'s get into it.'"
    }},
    {{
      "type": "intro_afternoon",
      "title": "Afternoon greeting",
      "text": "Same beat, afternoon phrasing. Different from morning version."
    }},
    {{
      "type": "intro_evening",
      "title": "Evening greeting",
      "text": "Evening version. Winding-down energy."
    }},
    {{
      "type": "intro_neutral",
      "title": "Neutral greeting",
      "text": "No time-of-day reference. Works at any hour."
    }},
    {{
      "type": "activity",
      "title": "What you've been reading",
      "text": "3-5 sentences. Focus: {activity['focus']}. End with this question: {activity['question']}"
    }},
    {{
      "type": "headline",
      "title": "<headline title>",
      "text": "<conversational, not a wire report. Match word count to length spec.>",
      "source_url": "<link from headline spec>"
    }},
    {{
      "type": "outro",
      "title": "Sign off",
      "text": "2-3 sentences. {tease_instruction}"
    }}
  ]
}}

Tone rules:
- Warm and direct. Not corporate. Not chipper.
- Smart listener who doesn't need everything spelled out.
- No filler: no 'In today\\'s fast-paced world', no 'That\\'s a wrap', no 'Stay curious'.
- Activity segment: thoughtful friend noticing something, not a database readback."""


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ── Public per-step functions (used by step endpoints) ────────────────────────

async def run_step_user_context(user_id: str) -> dict:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    topics = kb.interests.topics or ["technology", "world news"]
    recent_eps = user_ctx["recent_episodes"]
    recent_listen = None
    if recent_eps:
        ep = recent_eps[0]
        pct = int((ep.get("play_progress") or 0) * 100)
        status = "completed" if ep.get("listened") else f"{pct}%"
        recent_listen = f"{ep['title']} ({status})"
    return {
        "name": kb.identity.name,
        "topics": topics,
        "obsession": kb.interests.current_obsession,
        "saves_7d": len(user_ctx["saves"]),
        "saves": [s.get("title") or s.get("url", "") for s in user_ctx["saves"]],
        "recent_listen": recent_listen,
        "unlistened_count": len(user_ctx["unlistened_episodes"]),
    }


async def run_step_news_fetch(topics: list[str]) -> dict:
    items = await _fetch_news_broadly(topics)
    return {
        "count": len(items),
        "items": items,
        "topics_fetched": list({i["topic"] for i in items}),
    }


async def run_step_interest_filter(user_id: str, news_items: list[dict], topics: list[str]) -> dict:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    prompt = _build_filter_prompt(news_items, kb, topics_override=topics)
    client = _make_client()
    raw, usage = _llm_call(client, prompt, 2048)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Filter step JSON parse failed: {e}. Raw: {raw[:400]!r}")
    filtered = [
        {**news_items[r["index"]], **r}
        for r in data["filtered"]
        if r["index"] < len(news_items)
    ]
    return {
        "count": len(filtered),
        "items": filtered,
        "prompt": prompt,
        "raw_output": raw,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


async def run_step_curation(user_id: str, filtered_items: list[dict], topics: list[str]) -> dict:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    prompt = _build_curation_prompt(filtered_items, kb, topics_override=topics)
    client = _make_client()
    raw, usage = _llm_call(client, prompt, 1024)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Curation step JSON parse failed: {e}. Raw: {raw[:400]!r}")
    curated = [
        {**filtered_items[s["index"]], "angle": s["angle"], "curation_reason": s["reason"]}
        for s in data["selected"]
        if s["index"] < len(filtered_items)
    ]
    return {
        "count": len(curated),
        "selected": curated,
        "excluded": data.get("excluded", []),
        "prompt": prompt,
        "raw_output": raw,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


async def run_step_outline(user_id: str, curated_items: list[dict]) -> dict:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    prompt = _build_outline_prompt(curated_items, kb, user_ctx)
    client = _make_client()
    raw, usage = _llm_call(client, prompt, 1024)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Outline step JSON parse failed: {e}. Raw: {raw[:400]!r}")
    return {
        "outline": data["outline"],
        "prompt": prompt,
        "raw_output": raw,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


async def run_step_transcript(user_id: str, outline_data: dict, curated_items: list[dict]) -> dict:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    prompt = _build_transcript_prompt(outline_data, curated_items, kb, user_ctx)
    client = _make_client()
    raw, usage = _llm_call(client, prompt, 4096)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"Transcript step JSON parse failed: {e}. Raw: {raw[:400]!r}")
    segments = [
        {"type": s["type"], "title": s["title"], "text": s["text"],
         **({"source_url": s["source_url"]} if s.get("source_url") else {})}
        for s in data["segments"]
    ]
    return {
        "segment_count": len(segments),
        "segments": segments,
        "prompt": prompt,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


# ── Legacy streaming pipeline (kept for /brief/stream endpoint) ───────────────

async def generate_brief_stream(user_id: str) -> AsyncIterator[str]:
    try:
        client = _make_client()

        yield _sse({"step": "user_context", "status": "loading",
                    "message": "Fetching your saves and listening history…"})
        user_ctx = await _fetch_user_context(user_id)
        kb: UserKB = user_ctx["kb"]
        topics = kb.interests.topics or ["technology", "world news"]
        recent_eps = user_ctx["recent_episodes"]
        recent_listen = None
        if recent_eps:
            ep = recent_eps[0]
            pct = int((ep.get("play_progress") or 0) * 100)
            status_str = "completed" if ep.get("listened") else f"{pct}%"
            recent_listen = f"{ep['title']} ({status_str})"
        yield _sse({"step": "user_context", "status": "done", "data": {
            "name": kb.identity.name, "topics": topics,
            "obsession": kb.interests.current_obsession,
            "saves_7d": len(user_ctx["saves"]),
            "saves": [s.get("title") or s.get("url", "") for s in user_ctx["saves"][:8]],
            "recent_listen": recent_listen,
            "unlistened_count": len(user_ctx["unlistened_episodes"]),
        }})

        yield _sse({"step": "news_fetch", "status": "loading",
                    "message": f"Fetching news for {len(topics)} topics…"})
        news_items = await _fetch_news_broadly(topics)
        yield _sse({"step": "news_fetch", "status": "done", "data": {
            "count": len(news_items), "items": news_items,
            "topics_fetched": list({i["topic"] for i in news_items}),
        }})

        filter_prompt = _build_filter_prompt(news_items, kb)
        yield _sse({"step": "interest_filter", "status": "loading",
                    "message": f"Filtering {len(news_items)} articles…"})
        filter_raw, filter_usage = _llm_call(client, filter_prompt, 2048)
        filter_data = json.loads(filter_raw)
        filtered_items = [{**news_items[r["index"]], **r} for r in filter_data["filtered"] if r["index"] < len(news_items)]
        yield _sse({"step": "interest_filter", "status": "done", "data": {
            "count": len(filtered_items), "items": filtered_items,
            "prompt": filter_prompt, "raw_output": filter_raw,
            "input_tokens": filter_usage.input_tokens, "output_tokens": filter_usage.output_tokens,
        }})

        curation_prompt = _build_curation_prompt(filtered_items, kb)
        yield _sse({"step": "curation", "status": "loading",
                    "message": f"Curating {len(filtered_items)} articles to 4-6 stories…"})
        curation_raw, curation_usage = _llm_call(client, curation_prompt, 1024)
        curation_data = json.loads(curation_raw)
        curated_items = [{**filtered_items[s["index"]], "angle": s["angle"], "curation_reason": s["reason"]} for s in curation_data["selected"] if s["index"] < len(filtered_items)]
        yield _sse({"step": "curation", "status": "done", "data": {
            "count": len(curated_items), "selected": curated_items,
            "excluded": curation_data.get("excluded", []),
            "prompt": curation_prompt, "raw_output": curation_raw,
            "input_tokens": curation_usage.input_tokens, "output_tokens": curation_usage.output_tokens,
        }})

        outline_prompt = _build_outline_prompt(curated_items, kb, user_ctx)
        yield _sse({"step": "outline", "status": "loading", "message": "Structuring…"})
        outline_raw, outline_usage = _llm_call(client, outline_prompt, 1024)
        outline_data = json.loads(outline_raw)
        yield _sse({"step": "outline", "status": "done", "data": {
            "outline": outline_data["outline"],
            "prompt": outline_prompt, "raw_output": outline_raw,
            "input_tokens": outline_usage.input_tokens, "output_tokens": outline_usage.output_tokens,
        }})

        transcript_prompt = _build_transcript_prompt(outline_data, curated_items, kb, user_ctx)
        yield _sse({"step": "generating", "status": "loading", "message": "Writing your brief…"})
        gen_raw, gen_usage = _llm_call(client, transcript_prompt, 4096)
        gen_data = json.loads(gen_raw)
        segments = [Segment(type=s["type"], title=s["title"], text=s["text"], source_url=s.get("source_url")) for s in gen_data["segments"]]
        yield _sse({"step": "generating", "status": "done", "data": {
            "segment_count": len(segments),
            "prompt": transcript_prompt,
            "input_tokens": gen_usage.input_tokens, "output_tokens": gen_usage.output_tokens,
        }})

        yield _sse({"step": "done", "status": "done", "result": {
            "user_name": kb.identity.name, "topics": topics,
            "save_count_7d": len(user_ctx["saves"]),
            "segments": [{"type": s.type, "title": s.title, "text": s.text, **({"source_url": s.source_url} if s.source_url else {})} for s in segments],
            "debug": {"news_items": news_items},
        }})

    except Exception as exc:
        logger.exception(f"Brief generation failed for user {user_id}")
        yield _sse({"step": "error", "status": "error", "message": str(exc)})


async def generate_brief(user_id: str) -> BriefResult:
    user_ctx = await _fetch_user_context(user_id)
    kb: UserKB = user_ctx["kb"]
    topics = kb.interests.topics or ["technology", "world news"]
    client = _make_client()

    news_items = await _fetch_news_broadly(topics)
    filter_raw, _ = _llm_call(client, _build_filter_prompt(news_items, kb), 2048)
    filter_data = json.loads(filter_raw)
    filtered_items = [{**news_items[r["index"]], **r} for r in filter_data["filtered"] if r["index"] < len(news_items)]

    curation_raw, _ = _llm_call(client, _build_curation_prompt(filtered_items, kb), 1024)
    curation_data = json.loads(curation_raw)
    curated_items = [{**filtered_items[s["index"]], "angle": s["angle"], "curation_reason": s["reason"]} for s in curation_data["selected"] if s["index"] < len(filtered_items)]

    outline_raw, _ = _llm_call(client, _build_outline_prompt(curated_items, kb, user_ctx), 1024)
    outline_data = json.loads(outline_raw)

    gen_raw, _ = _llm_call(client, _build_transcript_prompt(outline_data, curated_items, kb, user_ctx), 4096)
    gen_data = json.loads(gen_raw)
    segments = [Segment(type=s["type"], title=s["title"], text=s["text"], source_url=s.get("source_url")) for s in gen_data["segments"]]

    return BriefResult(
        user_name=kb.identity.name, topics=topics,
        save_count_7d=len(user_ctx["saves"]), segments=segments, news_items=news_items,
    )
