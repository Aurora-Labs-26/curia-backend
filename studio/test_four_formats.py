"""
studio/test_four_formats.py
End-to-end pipeline test across all four formats. No TTS, no audio.

Pipeline per format:
  1. scrape URL → article text
  2. run all 7 transformations (insights extraction)
  3. idea_evaluation → format recommendation + angle
  4. briefing builder
  5. outline (Haiku)
  6. transcript (Sonnet)

All four formats are forced regardless of what idea_evaluation recommends.
The recommended format from idea_evaluation is printed as a flag.

Run from curia-v2 root:
    python studio/test_four_formats.py
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

import dspy
from core.llm_config import resolve
from core.prompts.outline import generate_outline as _outline_module
from core.prompts.transcript import generate_transcript as _transcript_module, TRANSCRIPT_EXAMPLES
from core.prompts.transformations import Transformations
from core.prompts.idea_evaluation import evaluate_single_idea
from core.scraper.cascade import scrape
from studio.briefing_builder import build_briefing_packet, briefing_packet_to_str
from studio.shows.profiles import SHOW_PROFILES

URL = "https://open.substack.com/pub/davidoks/p/why-china-got-rich-and-india-didnt?r=2et3b&utm_campaign=post-expanded-share&utm_medium=post%20viewer"

ALL_FORMATS = ["narrative_drift", "clarity_engine", "momentum_loop", "exploration_engine"]

SOURCE_ID = "00000000-0000-0000-0000-000000000001"

SEP = "=" * 70


# ---------------------------------------------------------------------------
# Step 1: Scrape
# ---------------------------------------------------------------------------

async def fetch_article(url: str) -> tuple[str, str]:
    import httpx
    import trafilatura

    print(f"\n{SEP}")
    print("STEP 1: Scraping article...")
    print(f"  URL: {url}")
    t = time.time()

    # Resolve redirects first (open.substack.com → canonical)
    from core.scraper.cascade import resolve_redirects, normalize_url
    url = normalize_url(url)
    url = await resolve_redirects(url)
    print(f"  Resolved: {url}")

    # Fetch HTML directly with a browser User-Agent, then pass to trafilatura.
    # ⚑ NOTE: trafilatura.fetch_url() sends no User-Agent, causing Substack/JS-heavy
    # sites to return a shell page with 0 content. Fetching with httpx+UA first fixes this.
    # The production scraper has this same bug — flagging it.
    print(f"  ⚑ FLAG: production scraper/_scrape_trafilatura uses trafilatura.fetch_url()")
    print(f"          which sends no User-Agent — Substack returns 0 chars as a result.")
    print(f"          Fix: fetch HTML with httpx (User-Agent set), pass to trafilatura.extract().")

    async with httpx.AsyncClient(
        timeout=20,
        follow_redirects=True,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
    ) as client:
        resp = await client.get(url)

    html = resp.text
    content = trafilatura.extract(html, include_comments=False, include_tables=False) or ""

    title = ""
    try:
        from trafilatura.metadata import extract_metadata
        meta = extract_metadata(html)
        if meta and meta.title:
            title = meta.title
    except Exception:
        pass
    title = title or url

    if len(content.strip()) < 200:
        raise RuntimeError(f"Could not extract content from {url} ({len(content)} chars)")

    print(f"  Title: {title}")
    print(f"  Content: {len(content)} chars ({time.time()-t:.1f}s)")
    return content.strip(), title


# ---------------------------------------------------------------------------
# Step 2: Insight extraction
# ---------------------------------------------------------------------------

def extract_insights(article_text: str) -> dict:
    print(f"\n{SEP}")
    print("STEP 2: Extracting insights (7 transformations)...")
    t = time.time()
    transformations = Transformations()
    # Use Haiku for transformations (fast + cheap)
    with dspy.context(lm=resolve.llm("outline")):
        result = transformations.forward(article=article_text[:50_000])
    elapsed = time.time() - t
    print(f"  Done in {elapsed:.1f}s")
    for k, v in result.items():
        preview = (v or "null")[:120].replace("\n", " ")
        print(f"  [{k}] {preview}")

    # ⚑ FLAG: summary and metadata are extracted but never passed to the LLM pipeline
    print("\n  ⚑ FLAG: summary + metadata are extracted here but stripped before idea_evaluation")
    print("          and briefing builder — they are computed but never used downstream.")

    return result


# ---------------------------------------------------------------------------
# Step 3: idea_evaluation
# ---------------------------------------------------------------------------

def run_idea_evaluation(title: str, insights: dict) -> dict:
    print(f"\n{SEP}")
    print("STEP 3: idea_evaluation → format recommendation + angle...")

    from intelligence.idea_generator import format_group
    source = {
        "id": SOURCE_ID,
        "title": title,
        "insights": insights,
    }
    group_text = format_group("g0", "STANDALONE", [source])

    t = time.time()
    with dspy.context(lm=resolve.llm("outline")):
        prediction = evaluate_single_idea(group_text=group_text)
    raw = prediction.idea_json.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    idea = json.loads(raw.strip())
    elapsed = time.time() - t

    print(f"  Recommended format : {idea.get('format')}")
    print(f"  Angle              : {idea.get('angle')}")
    print(f"  Done in {elapsed:.1f}s")

    return idea


# ---------------------------------------------------------------------------
# Step 4+5+6: briefing → outline → transcript per format
# ---------------------------------------------------------------------------

def run_format(
    format_name: str,
    title: str,
    insights: dict,
    angle: str,
) -> dict:
    print(f"\n{SEP}")
    print(f"FORMAT: {format_name.upper()}")
    print(f"  Angle: {angle}")

    sources = [{"id": SOURCE_ID, "title": title, "url": None}]
    insights_map = {SOURCE_ID: {
        k: v for k, v in insights.items()
        if k in ("key_insights", "human_stakes", "core_tensions", "counterpoints", "examples")
    }}

    packet = build_briefing_packet(
        format_name=format_name,
        sources=sources,
        insights=insights_map,
        editorial_direction=angle,
    )
    briefing = briefing_packet_to_str(packet)

    # Outline
    print(f"  Generating outline (Haiku)...")
    t = time.time()
    with dspy.context(lm=resolve.llm("outline", show=format_name)):
        pred_outline = _outline_module(briefing=briefing)
    raw_outline = pred_outline.outline_json.strip()
    if "```" in raw_outline:
        raw_outline = raw_outline.split("```")[1]
        if raw_outline.startswith("json"):
            raw_outline = raw_outline[4:]
    start, end = raw_outline.find("{"), raw_outline.rfind("}")
    if start != -1 and end != -1:
        raw_outline = raw_outline[start:end+1]
    outline = json.loads(raw_outline)
    print(f"  Outline: '{outline.get('title')}' — {len(outline.get('segments', []))} segments ({time.time()-t:.1f}s)")

    # Transcript
    profile = SHOW_PROFILES.get(format_name)
    if profile is None:
        print(f"  ⚑ FLAG: No SHOW_PROFILE found for '{format_name}' — using kenji defaults")
        speaker_def = "Speaker name: kenji\nBackstory: Journalist and host.\nSpeech patterns: Tight sentences. One idea per line."
    else:
        speaker = profile.speaker_config.speakers[0]
        speaker_def = (
            f"Speaker name: {speaker.name}\n"
            f"Backstory: {speaker.backstory}\n"
            f"Speech patterns: {speaker.speech_patterns}"
        )

    print(f"  Generating transcript (Sonnet)...")
    t = time.time()
    with dspy.context(lm=resolve.llm("transcript", show=format_name)):
        pred_transcript = _transcript_module(
            briefing=briefing,
            outline=json.dumps(outline, ensure_ascii=False),
            speaker_definition=speaker_def,
            examples=TRANSCRIPT_EXAMPLES,
        )
    raw_tx = pred_transcript.transcript_json.strip()
    if "```" in raw_tx:
        raw_tx = raw_tx.split("```")[1]
        if raw_tx.startswith("json"):
            raw_tx = raw_tx[4:]
    start, end = raw_tx.find("["), raw_tx.rfind("]")
    if start != -1 and end != -1:
        raw_tx = raw_tx[start:end+1]
    transcript = json.loads(raw_tx)
    word_count = sum(len(line.get("text","").split()) for line in transcript)
    print(f"  Transcript: {len(transcript)} lines, ~{word_count} words ({time.time()-t:.1f}s)")

    target_words = packet["episode_constraints"]["target_words"]
    pct = round(word_count / target_words * 100)
    print(f"  Word count: {word_count} / {target_words} target ({pct}%)")
    if pct < 90:
        print(f"  ⚑ FLAG: transcript is under 90% of target word count ({pct}%) — content may be underdeveloped")
    elif pct > 110:
        print(f"  ⚑ FLAG: transcript is over 110% of target word count ({pct}%) — may run long")

    return {
        "format": format_name,
        "outline": outline,
        "transcript": transcript,
        "word_count": word_count,
        "target_words": target_words,
    }


# ---------------------------------------------------------------------------
# Save + print
# ---------------------------------------------------------------------------

def print_transcript(format_name: str, transcript: list[dict]):
    print(f"\n--- TRANSCRIPT: {format_name.upper()} ---")
    for line in transcript:
        print(f"  [{line.get('speaker','?')}] {line.get('text','')}")


def save_results(results: list[dict], title: str):
    out_path = Path(__file__).parent / "test_four_formats_output.json"
    with open(out_path, "w") as f:
        json.dump({
            "title": title,
            "results": results,
        }, f, indent=2, ensure_ascii=False)
    print(f"\n{SEP}")
    print(f"Saved all transcripts → {out_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY not set")
        sys.exit(1)

    total_start = time.time()

    # Step 1: Scrape
    article_text, title = await fetch_article(URL)

    # Step 2: Insights
    insights = extract_insights(article_text)

    # Step 3: idea_evaluation (get recommended format + angle)
    idea = run_idea_evaluation(title, insights)
    angle = idea.get("angle", "Follow the most interesting thread in the material.")
    recommended_format = idea.get("format", "clarity_engine")

    print(f"\n{SEP}")
    print(f"Running all 4 formats with angle: \"{angle}\"")
    print(f"(idea_evaluation recommended: {recommended_format})\n")

    # Steps 4-6: all four formats
    results = []
    for fmt in ALL_FORMATS:
        marker = " ← recommended" if fmt == recommended_format else ""
        print(f"\n{'▶'} {fmt}{marker}")
        result = run_format(fmt, title, insights, angle)
        results.append(result)

    # Print all transcripts
    print(f"\n{SEP}")
    print("ALL TRANSCRIPTS")
    for r in results:
        print_transcript(r["format"], r["transcript"])

    # Summary
    print(f"\n{SEP}")
    print("SUMMARY")
    for r in results:
        rec = " ← idea_eval recommended" if r["format"] == recommended_format else ""
        pct = round(r["word_count"] / r["target_words"] * 100)
        flag = " ⚑" if pct < 90 or pct > 110 else ""
        print(f"  {r['format']:25s}  {r['word_count']:4d}/{r['target_words']} words ({pct}%){flag}{rec}")

    save_results(results, title)
    print(f"\nTotal time: {time.time()-total_start:.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
