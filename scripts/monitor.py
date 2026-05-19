"""
scripts/monitor.py
Real-time terminal monitor for jobs, episodes, sources, and ideas.
Prints a line whenever any row changes status or a new row appears.

Run:  python3 scripts/monitor.py
"""

import asyncio, asyncpg, os, sys, json
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

POLL = 2

# ── ANSI colours ─────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
BLUE   = "\033[34m"
MAGENTA = "\033[35m"

def ts():
    return datetime.now().strftime("%H:%M:%S")

def status_color(s):
    if s in ("ready", "done", "completed"):   return GREEN + s + RESET
    if s in ("queued",):                       return YELLOW + s + RESET
    if s in ("running", "scraping",
             "transforming", "embedding"):     return CYAN + s + RESET
    if s in ("failed", "error"):               return RED + s + RESET
    return s

def fmt_payload(payload_str):
    """Pull the interesting fields out of a job payload."""
    try:
        p = json.loads(payload_str) if isinstance(payload_str, str) else dict(payload_str)
    except Exception:
        return str(payload_str)[:120]
    parts = []
    for key in ("standalone", "show_name", "speaker", "length_minutes", "angle_override",
                "source_id", "url", "auto_generate"):
        if key in p and p[key] is not None:
            parts.append(f"{key}={p[key]}")
    return "  ".join(parts) or str(p)[:120]


async def main():
    dsn = os.environ["DATABASE_URL"]
    conn = await asyncpg.connect(dsn)

    seen_sources  = {}   # id → status
    seen_jobs     = {}   # id → status
    seen_episodes = {}   # id → status
    seen_ideas    = set()

    print(f"{BOLD}[MONITOR]{RESET} started — polling every {POLL}s  (Ctrl+C to stop)\n", flush=True)

    while True:
        try:
            # ── Jobs ─────────────────────────────────────────────────────────
            jobs = await conn.fetch(
                """
                SELECT id, type, status, payload, last_error, created_at
                FROM jobs
                ORDER BY created_at DESC LIMIT 30
                """
            )
            for r in jobs:
                key = str(r["id"])
                val = r["status"]
                if seen_jobs.get(key) != val:
                    label = f"{BOLD}[JOB:{r['type']}]{RESET}"
                    sc    = status_color(val)
                    err   = f"\n    {RED}error:{RESET} {r['last_error'][:120]}" if r["last_error"] else ""
                    pld   = fmt_payload(r["payload"])
                    if seen_jobs.get(key) is None:
                        print(f"{ts()} {label} id={key[:8]}  {sc}", flush=True)
                        print(f"    {DIM}{pld}{RESET}{err}", flush=True)
                    else:
                        print(f"{ts()} {label} id={key[:8]}  {seen_jobs[key]} → {sc}{err}", flush=True)
                    seen_jobs[key] = val

            # ── Sources ──────────────────────────────────────────────────────
            sources = await conn.fetch(
                """
                SELECT id, url, title, status
                FROM source
                ORDER BY created_at DESC LIMIT 30
                """
            )
            for r in sources:
                key = str(r["id"])
                val = r["status"]
                if seen_sources.get(key) != val:
                    label  = f"{BOLD}[SOURCE]{RESET}"
                    sc     = status_color(val)
                    title  = (r["title"] or "")[:50] or (r["url"] or "")[-50:]
                    if seen_sources.get(key) is None:
                        print(f"{ts()} {label} {title}  {sc}", flush=True)
                    else:
                        print(f"{ts()} {label} {title}  {seen_sources[key]} → {sc}", flush=True)
                    seen_sources[key] = val

            # ── Episodes ─────────────────────────────────────────────────────
            episodes = await conn.fetch(
                """
                SELECT id, status, error, show_name, title,
                       editorial_direction, length_minutes, speaker_override
                FROM episode
                ORDER BY created_at DESC LIMIT 20
                """
            )
            for r in episodes:
                key = str(r["id"])
                val = r["status"]
                prev = seen_episodes.get(key)
                if prev != val:
                    label = f"{BOLD}{MAGENTA}[EPISODE]{RESET}"
                    sc    = status_color(val)
                    err   = f"\n    {RED}error:{RESET} {r['error'][:120]}" if r["error"] else ""
                    if prev is None:
                        host   = r["speaker_override"] or "default"
                        length = f"{r['length_minutes']}m" if r["length_minutes"] else "default"
                        show   = r["show_name"] or "?"
                        title  = (r["title"] or "")[:60]
                        angle  = (r["editorial_direction"] or "")[:100]
                        print(f"{ts()} {label} id={key[:8]}  {sc}", flush=True)
                        print(f"    show={CYAN}{show}{RESET}  host={host}  length={length}", flush=True)
                        if angle:
                            print(f"    angle: {DIM}{angle}{RESET}", flush=True)
                        if title:
                            print(f"    title: {title}", flush=True)
                    else:
                        print(f"{ts()} {label} id={key[:8]}  {prev} → {sc}{err}", flush=True)
                    seen_episodes[key] = val

            # ── Ideas ────────────────────────────────────────────────────────
            ideas = await conn.fetch(
                """
                SELECT id, angle, idea_type, format, source_ids, generated
                FROM show_idea
                ORDER BY created_at DESC LIMIT 20
                """
            )
            for r in ideas:
                key = str(r["id"])
                if key not in seen_ideas:
                    seen_ideas.add(key)
                    src_count = len(r["source_ids"]) if r["source_ids"] else 0
                    label = f"{BOLD}{BLUE}[IDEA]{RESET}"
                    print(f"{ts()} {label} format={r['format']}  type={r['idea_type']}  sources={src_count}  generated={r['generated']}", flush=True)
                    if r["angle"]:
                        print(f"    angle: {DIM}{(r['angle'] or '')[:120]}{RESET}", flush=True)

        except Exception as e:
            print(f"{ts()} {RED}[MONITOR ERROR]{RESET} {e}", flush=True)

        await asyncio.sleep(POLL)


asyncio.run(main())
