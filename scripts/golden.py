#!/usr/bin/env python3
"""
scripts/golden.py
TUI for building a golden evaluation dataset.

Two CSVs:
  data/golden_sources.csv  — scrape + 7 transform ratings per source
  data/golden_episodes.csv — outline + transcript + judge ratings per episode

Usage:
    python scripts/golden.py
"""

import asyncio
import csv
import json
import os
import sys
import tempfile
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "studio"))

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT / ".env")

from core.db.connection import get_pool, db_query, db_fetchrow, db_execute
from core.ingest import process_source, get_or_create_source
from core.queue import enqueue

# ── Paths ─────────────────────────────────────────────────────────────────────

DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
SOURCES_CSV = DATA_DIR / "golden_sources.csv"
EPISODES_CSV = DATA_DIR / "golden_episodes.csv"

# ── CSV headers ───────────────────────────────────────────────────────────────

SOURCE_FIELDS = [
    "id", "url", "title",
    "scrape_verdict",
    "summary", "summary_verdict", "summary_golden",
    "metadata", "metadata_verdict",
    "key_insights", "key_insights_verdict", "key_insights_golden",
    "human_stakes", "human_stakes_verdict", "human_stakes_golden",
    "core_tensions", "core_tensions_verdict", "core_tensions_golden",
    "counterpoints", "counterpoints_verdict", "counterpoints_golden",
    "examples", "examples_verdict", "examples_golden",
    "created_at",
]

EPISODE_FIELDS = [
    "id", "source_ids", "format", "speaker", "angle",
    "title", "title_verdict", "title_golden",
    "outline", "outline_verdict", "outline_golden",
    "transcript", "transcript_verdict", "transcript_golden",
    "judge_score", "judge_verdict", "judge_golden_score",
    "created_at",
]

# ── Terminal helpers ──────────────────────────────────────────────────────────

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"

def hr():
    print(f"{DIM}{'━' * 60}{RESET}")

def show(label: str, text: str, max_lines: int = 15):
    hr()
    print(f"{BOLD}{CYAN}{label}:{RESET}")
    lines = text.strip().split("\n")
    for line in lines[:max_lines]:
        print(f"  {line}")
    if len(lines) > max_lines:
        print(f"  {DIM}... ({len(lines) - max_lines} more lines){RESET}")
    hr()

def ask_verdict() -> str:
    """Ask user for good/bad/edit. Returns 'good', 'bad', or 'edit'."""
    while True:
        choice = input(f"  {GREEN}[g]{RESET}ood / {RED}[b]{RESET}ad / {YELLOW}[e]{RESET}dit > ").strip().lower()
        if choice in ("g", "good"):
            return "good"
        if choice in ("b", "bad"):
            return "bad"
        if choice in ("e", "edit"):
            return "edit"
        print("  Enter g, b, or e")

def edit_text(current: str) -> str:
    """Open text in $EDITOR or fall back to inline editing."""
    editor = os.environ.get("EDITOR", "")
    if editor:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write(current)
            f.flush()
            tmp = f.name
        os.system(f"{editor} {tmp}")
        with open(tmp) as f:
            result = f.read()
        os.unlink(tmp)
        return result.strip()
    else:
        print(f"  {DIM}(Enter new text. Empty line + Enter to finish){RESET}")
        lines = []
        while True:
            line = input("  > ")
            if line == "":
                break
            lines.append(line)
        return "\n".join(lines) if lines else current

def rate_stage(label: str, content: str) -> tuple[str, str]:
    """Show content, get verdict, optionally edit. Returns (verdict, golden)."""
    show(label, content)
    verdict = ask_verdict()
    golden = ""
    if verdict == "edit":
        golden = edit_text(content)
        verdict = "bad"  # edited means the original was bad
        print(f"  {GREEN}Saved edit.{RESET}")
    elif verdict == "bad":
        want_edit = input(f"  {YELLOW}Want to provide the correct version? [y/n]{RESET} > ").strip().lower()
        if want_edit == "y":
            golden = edit_text(content)
    return verdict, golden

# ── CSV helpers ───────────────────────────────────────────────────────────────

def _ensure_csv(path: Path, fields: list[str]):
    if not path.exists():
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()

def append_source_row(row: dict):
    _ensure_csv(SOURCES_CSV, SOURCE_FIELDS)
    with open(SOURCES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SOURCE_FIELDS)
        writer.writerow({k: row.get(k, "") for k in SOURCE_FIELDS})

def append_episode_row(row: dict):
    _ensure_csv(EPISODES_CSV, EPISODE_FIELDS)
    with open(EPISODES_CSV, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=EPISODE_FIELDS)
        writer.writerow({k: row.get(k, "") for k in EPISODE_FIELDS})

def count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path) as f:
        return sum(1 for _ in f) - 1  # minus header

# ── Pipeline runners ──────────────────────────────────────────────────────────

async def ingest_and_rate(url: str, user_id: str) -> str | None:
    """Ingest a URL, rate each stage. Returns source_id or None."""
    print(f"\n{BOLD}Ingesting: {url}{RESET}")

    source_id = await get_or_create_source(url=url, user_id=user_id)

    # Check if already ready
    row = await db_fetchrow(
        "SELECT status FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    if row and row["status"] != "ready":
        print("  Running ingest pipeline...")
        try:
            await process_source(source_id=source_id)
        except Exception as e:
            print(f"  {RED}Ingest failed: {e}{RESET}")
            return None

    # Fetch source + insights
    source = await db_fetchrow(
        "SELECT id, title, url, full_text FROM source WHERE id = $id::uuid",
        {"id": source_id},
    )
    insights = await db_query(
        "SELECT insight_type, content FROM source_insight WHERE source_id = $id::uuid",
        {"id": source_id},
    )
    insight_map = {r["insight_type"]: r.get("content", "") or "" for r in (insights or [])}

    title = source.get("title", "") or url
    full_text = source.get("full_text", "") or ""

    print(f"  {GREEN}✓{RESET} Scraped: {title} ({len(full_text)} chars)")

    # Rate scraping
    show("SCRAPED CONTENT (first 500 chars)", full_text[:500])
    scrape_verdict = input(f"  Scrape quality — {GREEN}[g]{RESET}ood / {RED}[b]{RESET}ad > ").strip().lower()
    scrape_verdict = "good" if scrape_verdict in ("g", "good") else "bad"

    # Rate each transform
    csv_row = {
        "id": source_id,
        "url": url,
        "title": title,
        "scrape_verdict": scrape_verdict,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    transform_stages = [
        "summary", "metadata", "key_insights", "human_stakes",
        "core_tensions", "counterpoints", "examples",
    ]

    for stage in transform_stages:
        content = insight_map.get(stage, "(not generated)")
        if not content or content.strip().lower() == "null":
            content = "(null — LLM determined not applicable)"
        csv_row[stage] = content
        verdict, golden = rate_stage(stage.upper().replace("_", " "), content)
        csv_row[f"{stage}_verdict"] = verdict
        if golden:
            csv_row[f"{stage}_golden"] = golden

    append_source_row(csv_row)
    print(f"\n  {GREEN}✓ Source rated and saved to golden_sources.csv{RESET}")
    return source_id


async def generate_and_rate(user_id: str, source_ids: list[str] | None = None, show_name: str = "clarity_engine"):
    """Generate an episode and rate it."""
    from studio.generator import process_episode

    print(f"\n{BOLD}Generating episode (format={show_name})...{RESET}")

    episode_id = str(uuid4())
    await db_execute(
        """
        INSERT INTO episode (id, user_id, show_name, status)
        VALUES ($id::uuid, $user_id, $show, 'queued')
        """,
        {"id": episode_id, "user_id": user_id, "show": show_name},
    )

    try:
        await process_episode(episode_id=episode_id, user_id=user_id)
    except Exception as e:
        print(f"  {RED}Episode generation failed: {e}{RESET}")
        return

    # Fetch episode
    ep = await db_fetchrow(
        """
        SELECT id, title, show_name, speaker_override, editorial_direction,
               outline, transcript, quality_score, quality_feedback,
               quality_violations, source_ids
        FROM episode WHERE id = $id::uuid
        """,
        {"id": episode_id},
    )

    if not ep:
        print(f"  {RED}Episode not found after generation{RESET}")
        return

    title = ep.get("title", "")
    outline = ep.get("outline") or {}
    transcript = ep.get("transcript") or []
    quality_score = ep.get("quality_score", 0)
    source_ids_db = ep.get("source_ids") or []

    if isinstance(outline, str):
        outline = json.loads(outline)
    if isinstance(transcript, str):
        transcript = json.loads(transcript)

    print(f"  {GREEN}✓{RESET} Generated: {title}")
    print(f"  {GREEN}✓{RESET} Quality score: {quality_score}")

    csv_row = {
        "id": episode_id,
        "source_ids": json.dumps([str(s) for s in source_ids_db]),
        "format": show_name,
        "speaker": ep.get("speaker_override", ""),
        "angle": ep.get("editorial_direction", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Rate title
    csv_row["title"] = title
    title_verdict, title_golden = rate_stage("EPISODE TITLE", title)
    csv_row["title_verdict"] = title_verdict
    csv_row["title_golden"] = title_golden

    # Rate outline
    outline_text = json.dumps(outline, indent=2)
    csv_row["outline"] = outline_text
    outline_verdict, outline_golden = rate_stage("OUTLINE", outline_text)
    csv_row["outline_verdict"] = outline_verdict
    csv_row["outline_golden"] = outline_golden

    # Rate transcript
    transcript_preview = "\n".join(
        f"[{line.get('speaker', '?')}] {line.get('text', '')}"
        for line in (transcript if isinstance(transcript, list) else [])
    )
    csv_row["transcript"] = json.dumps(transcript)
    transcript_verdict, transcript_golden = rate_stage("TRANSCRIPT", transcript_preview)
    csv_row["transcript_verdict"] = transcript_verdict
    csv_row["transcript_golden"] = transcript_golden

    # Rate judge
    judge_text = f"Score: {quality_score}\nFeedback: {ep.get('quality_feedback', '')}\nViolations: {ep.get('quality_violations', [])}"
    show("JUDGE SCORE", judge_text)
    judge_verdict = input(f"  Judge accurate? {GREEN}[g]{RESET}ood / {RED}[b]{RESET}ad > ").strip().lower()
    csv_row["judge_score"] = quality_score
    csv_row["judge_verdict"] = "good" if judge_verdict in ("g", "good") else "bad"
    if csv_row["judge_verdict"] == "bad":
        try:
            golden_score = float(input("  What should the score be (0.0-1.0)? > "))
            csv_row["judge_golden_score"] = golden_score
        except ValueError:
            pass

    append_episode_row(csv_row)
    print(f"\n  {GREEN}✓ Episode rated and saved to golden_episodes.csv{RESET}")


# ── Main TUI ──────────────────────────────────────────────────────────────────

async def main():
    await get_pool()

    # Get or create user
    user_row = await db_fetchrow("SELECT id FROM users LIMIT 1")
    if not user_row:
        print(f"{RED}No users found. Run: python scripts/create_user.py --email you@test.com --name Test{RESET}")
        return
    user_id = str(user_row["id"])

    while True:
        print(f"""
{BOLD}Golden Dataset Manager{RESET}
{DIM}Sources: {count_rows(SOURCES_CSV)} rated | Episodes: {count_rows(EPISODES_CSV)} rated{RESET}

  {CYAN}1{RESET}. Evaluate a source (ingest URL → rate transforms)
  {CYAN}2{RESET}. Evaluate an episode (pick existing sources → generate → rate)
  {CYAN}3{RESET}. Full run (ingest URL → generate episode → rate everything)
  {CYAN}4{RESET}. View dataset stats
  {CYAN}q{RESET}. Quit
""")
        choice = input("> ").strip()

        if choice == "1":
            url = input("URL: ").strip()
            if url:
                await ingest_and_rate(url, user_id)

        elif choice == "2":
            # List ready sources
            sources = await db_query(
                "SELECT id, title, url FROM source WHERE user_id = $uid AND status = 'ready' ORDER BY created_at DESC LIMIT 20",
                {"uid": user_id},
            )
            if not sources:
                print(f"  {YELLOW}No ready sources. Ingest some first (option 1 or 3).{RESET}")
                continue

            print(f"\n  {BOLD}Ready sources:{RESET}")
            for i, s in enumerate(sources):
                print(f"  {CYAN}{i+1}{RESET}. {s.get('title', s.get('url', '?'))[:60]}")

            fmt = input(f"\n  Format [{CYAN}clarity_engine{RESET}]: ").strip() or "clarity_engine"
            await generate_and_rate(user_id, show_name=fmt)

        elif choice == "3":
            url = input("URL: ").strip()
            fmt = input(f"Format [{CYAN}clarity_engine{RESET}]: ").strip() or "clarity_engine"
            if url:
                source_id = await ingest_and_rate(url, user_id)
                if source_id:
                    await generate_and_rate(user_id, source_ids=[source_id], show_name=fmt)

        elif choice == "4":
            src_count = count_rows(SOURCES_CSV)
            ep_count = count_rows(EPISODES_CSV)
            print(f"""
  {BOLD}Dataset Stats{RESET}
  Sources rated:  {src_count}
  Episodes rated: {ep_count}
  Files:
    {SOURCES_CSV}
    {EPISODES_CSV}
""")
            if src_count > 0:
                # Quick verdict breakdown
                with open(SOURCES_CSV) as f:
                    reader = csv.DictReader(f)
                    good = bad = 0
                    for row in reader:
                        for k, v in row.items():
                            if k.endswith("_verdict"):
                                if v == "good":
                                    good += 1
                                elif v == "bad":
                                    bad += 1
                    total = good + bad
                    if total:
                        print(f"  Source verdicts: {GREEN}{good} good{RESET} / {RED}{bad} bad{RESET} ({good/total*100:.0f}% pass rate)")

        elif choice in ("q", "quit", "exit"):
            print("Bye!")
            break


if __name__ == "__main__":
    asyncio.run(main())
