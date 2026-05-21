"""
scripts/dashboard.py
Unified real-time pipeline dashboard — one terminal, all components.

Layout:
  ┌─ HEADER ──────────────────────────────────────────────────────────────────┐
  │  live counts: sources / jobs / episodes / ideas                           │
  │  active pipeline stages with spinners                                     │
  └───────────────────────────────────────────────────────────────────────────┘
  scrolling event log (newest at bottom):
    SOURCE   →  scraping → transforming → embedding → ready / failed
    JOB      →  queued → running → done / failed
    CLUSTER  →  pair scores, which sources clustered
    IDEA     →  angle preview, format, type
    EPISODE  →  queued → generating → done / failed + title
    LLM      →  call start/end, duration, output preview

Usage:
  python3 scripts/dashboard.py
  python3 scripts/dashboard.py --llm        # also show LLM call details
  python3 scripts/dashboard.py --llm-output # show full LLM output text too
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import asyncpg

sys.path.insert(0, str(Path(__file__).parent.parent))
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

POLL_DB   = 1.5   # seconds between DB polls
POLL_LOG  = 0.25  # seconds between log file reads
LOGS_DIR  = Path(os.getenv("CURIA_LOGS_DIR", Path(__file__).parent.parent / "logs"))
LLM_LOG   = LOGS_DIR / "llm.log"

# ── ANSI ─────────────────────────────────────────────────────────────────────
R   = "\033[0m"
B   = "\033[1m"          # bold
D   = "\033[2m"          # dim
U   = "\033[4m"          # underline
BLK = "\033[30m"
RED = "\033[31m"
GRN = "\033[32m"
YLW = "\033[33m"
BLU = "\033[34m"
MAG = "\033[35m"
CYN = "\033[36m"
WHT = "\033[37m"
GRY = "\033[90m"         # bright black / grey

BG_BLK  = "\033[40m"
BG_DKGR = "\033[48;5;234m"   # dark grey bg for header
BG_DKBL = "\033[48;5;17m"
BG_BAR  = "\033[48;5;236m"

# Status badge colours
STATUS_COLOR = {
    "ready":       GRN,
    "done":        GRN,
    "completed":   GRN,
    "queued":      YLW,
    "running":     CYN,
    "scraping":    CYN,
    "transforming":CYN,
    "embedding":   CYN,
    "generating":  CYN,
    "failed":      RED,
    "error":       RED,
    "new":         BLU,
    "in-progress": MAG,
    "listened":    GRY,
}

SPINNER = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

# Component labels & colours
COMP = {
    "SOURCE":   (CYN,  "◈ SOURCE "),
    "JOB":      (YLW,  "⚙ JOB    "),
    "CLUSTER":  (MAG,  "⬡ CLUSTER"),
    "IDEA":     (BLU,  "✦ IDEA   "),
    "EPISODE":  (GRN,  "▶ EPISODE"),
    "LLM":      (WHT,  "◎ LLM    "),
    "ERROR":    (RED,  "✖ ERROR  "),
    "INFO":     (GRY,  "· INFO   "),
}

def term_width() -> int:
    return shutil.get_terminal_size((120, 40)).columns

def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")

def badge(status: str) -> str:
    c = STATUS_COLOR.get(status, GRY)
    return f"{B}{c}{status}{R}"

def trunc(s: str, n: int) -> str:
    if not s:
        return ""
    return s[:n] + "…" if len(s) > n else s

def fmt_payload(payload_str) -> str:
    try:
        p = json.loads(payload_str) if isinstance(payload_str, str) else dict(payload_str)
    except Exception:
        return str(payload_str)[:100]
    parts = []
    for key in ("standalone","show_name","speaker","length_minutes","angle_override","source_id","url","auto_generate"):
        if key in p and p[key] is not None:
            parts.append(f"{GRY}{key}{R}={str(p[key])[:40]}")
    return "  ".join(parts) or str(p)[:100]

# ── Event log line builder ────────────────────────────────────────────────────

def strip_ansi(s: str) -> str:
    import re
    return re.sub(r'\033\[[0-9;]*m', '', s)

def make_line(component: str, lines: list[str]) -> list[str]:
    """Return formatted output lines for one event, truncated to terminal width."""
    c, label = COMP.get(component, (GRY, f"  {component:<7}"))
    w = term_width()
    out = []
    for i, line in enumerate(lines):
        if i == 0:
            prefix = f"{GRY}{ts()}{R}  {B}{c}{label}{R}  "
        else:
            prefix = f"           {D}│{R}  "
        full = prefix + line
        # Truncate based on visible length (strip ANSI for measurement)
        visible = strip_ansi(full)
        if len(visible) > w:
            # Trim the content part, keeping prefix intact
            over = len(visible) - w
            full = prefix + line[:max(0, len(strip_ansi(line)) - over - 1)] + f"{GRY}…{R}"
        out.append(full)
    return out

# ── Header ───────────────────────────────────────────────────────────────────

def render_header(counts: dict, active: dict, spin_frame: int) -> list[str]:
    w  = term_width()
    sp = SPINNER[spin_frame % len(SPINNER)]
    sep = f"{GRY}{'─' * w}{R}"

    # Row 1 — title bar
    title = f"{B}  CURIA PIPELINE DASHBOARD{R}"
    clock = f"{GRY}{datetime.now().strftime('%Y-%m-%d  %H:%M:%S')}{R}  "
    row1  = title + " " * max(1, w - 28 - len(datetime.now().strftime('%Y-%m-%d  %H:%M:%S')) - 2) + clock

    # Row 2 — counts
    def cnt(label, val, color):
        return f"{D}{label}{R} {B}{color}{val}{R}"
    row2 = (
        "  "
        + cnt("SOURCES",  counts.get("sources",  0), CYN)  + "   "
        + cnt("JOBS",     counts.get("jobs",     0), YLW)  + "   "
        + cnt("EPISODES", counts.get("episodes", 0), GRN)  + "   "
        + cnt("IDEAS",    counts.get("ideas",    0), BLU)
    )

    # Row 3 — active stages
    stages = []
    for stage, items in active.items():
        if items:
            stages.append(f"{CYN}{sp}{R} {stage} ({len(items)})")
    row3 = "  " + "   ".join(stages) if stages else f"  {GRY}idle{R}"

    return [sep, row1, row2, row3, sep]

# ── LLM log reader ────────────────────────────────────────────────────────────

class LLMLogReader:
    def __init__(self, path: Path, show_output: bool):
        self.path        = path
        self.show_output = show_output
        self._fh         = None
        self._open()

    def _open(self):
        if self.path.exists():
            self._fh = open(self.path, "r")
            self._fh.seek(0, 2)  # tail

    def read_new(self) -> list[list[str]]:
        """Return list of event line-groups."""
        if self._fh is None:
            self._open()
            return []
        events = []
        while True:
            line = self._fh.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            try:
                msg = rec["record"]["message"]
            except Exception:
                continue
            if not msg:
                continue

            if "LLM_CALL_START" in msg:
                # format: "LLM_CALL_START | task=outline show=sharp-take briefing_len=1234"
                parts = dict(p.split("=", 1) for p in msg.split(" | ", 1)[-1].split() if "=" in p)
                task  = parts.get("task", "?")
                show  = parts.get("show", "")
                blen  = parts.get("briefing_len", "?")
                events.append(make_line("LLM", [
                    f"▶ {B}{YLW}{task}{R}  show={CYN}{show}{R}  briefing_len={blen}",
                ]))

            elif "LLM_CALL_END" in msg:
                parts = dict(p.split("=", 1) for p in msg.split(" | ", 1)[-1].split() if "=" in p)
                dur   = parts.get("duration", "?")
                olen  = parts.get("output_len", "?")
                events.append(make_line("LLM", [
                    f"✓ done  {GRN}{dur}s{R}  output_len={olen}",
                ]))

            elif "LLM_OUTPUT" in msg and self.show_output:
                # format: "LLM_OUTPUT | task=outline | <json text>"
                segs = msg.split(" | ", 2)
                text = segs[2].strip() if len(segs) > 2 else msg
                preview = trunc(text.replace("\n", " "), term_width() - 20)
                events.append(make_line("LLM", [
                    f"{D}output: {preview}{R}",
                ]))

            elif "[cluster_sources]" in msg:
                body = msg.split("]", 1)[1].strip() if "]" in msg else msg
                events.append(make_line("CLUSTER", [f"{MAG}{body}{R}"]))

        return events

# ── DB poller ────────────────────────────────────────────────────────────────

class DBPoller:
    def __init__(self, conn: asyncpg.Connection):
        self.conn         = conn
        self.seen_sources  = {}   # id → status
        self.seen_jobs     = {}   # id → status
        self.seen_episodes = {}   # id → status
        self.seen_ideas    = set()
        self.counts        = {"sources": 0, "jobs": 0, "episodes": 0, "ideas": 0}
        self.active        = {"scraping": [], "transforming": [], "embedding": [],
                               "generating": [], "queued_jobs": []}

    async def poll(self) -> list[list[str]]:
        events = []
        try:
            events += await self._poll_sources()
            events += await self._poll_jobs()
            events += await self._poll_episodes()
            events += await self._poll_ideas()
            await self._update_counts()
            await self._update_active()
        except Exception as e:
            events.append(make_line("ERROR", [str(e)[:120]]))
        return events

    async def _poll_sources(self):
        rows = await self.conn.fetch(
            "SELECT id, url, title, status FROM source ORDER BY created_at DESC LIMIT 40"
        )
        out = []
        for r in rows:
            key, val = str(r["id"]), r["status"]
            prev = self.seen_sources.get(key)
            if prev == val:
                continue
            label = trunc(r["title"] or r["url"] or "", 55)
            if prev is None:
                out.append(make_line("SOURCE", [
                    f"{label}",
                    f"id={GRY}{key[:8]}{R}  status={badge(val)}",
                ]))
            else:
                arrow = f"{GRY}{prev}{R} → {badge(val)}"
                err_row = []
                out.append(make_line("SOURCE", [f"{label}  {arrow}"]))
            self.seen_sources[key] = val
        return out

    async def _poll_jobs(self):
        rows = await self.conn.fetch(
            "SELECT id, type, status, payload, last_error, created_at FROM jobs ORDER BY created_at DESC LIMIT 40"
        )
        out = []
        for r in rows:
            key, val = str(r["id"]), r["status"]
            prev = self.seen_jobs.get(key)
            if prev == val:
                continue
            jtype = r["type"] or "?"
            pld   = fmt_payload(r["payload"])
            if prev is None:
                out.append(make_line("JOB", [
                    f"{B}{jtype}{R}  {badge(val)}",
                    f"id={GRY}{key[:8]}{R}  {pld}",
                ]))
            else:
                err = f"  {RED}{trunc(r['last_error'] or '', 100)}{R}" if r["last_error"] else ""
                out.append(make_line("JOB", [
                    f"{B}{jtype}{R}  {GRY}{prev}{R} → {badge(val)}{err}",
                ]))
            self.seen_jobs[key] = val
        return out

    async def _poll_episodes(self):
        rows = await self.conn.fetch(
            """SELECT id, status, error, show_name, title,
                      editorial_direction, length_minutes, speaker_override
               FROM episode ORDER BY created_at DESC LIMIT 30"""
        )
        out = []
        for r in rows:
            key, val = str(r["id"]), r["status"]
            prev = self.seen_episodes.get(key)
            if prev == val:
                continue
            show   = r["show_name"] or "?"
            host   = r["speaker_override"] or "default"
            length = f"{r['length_minutes']}m" if r["length_minutes"] else "?"
            title  = trunc(r["title"] or "", 60)
            angle  = trunc(r["editorial_direction"] or "", 80)
            if prev is None:
                lines = [
                    f"show={CYN}{show}{R}  host={host}  length={length}  {badge(val)}",
                    f"id={GRY}{key[:8]}{R}",
                ]
                if angle: lines.append(f"{D}angle: {angle}{R}")
                if title: lines.append(f"title: {B}{title}{R}")
                out.append(make_line("EPISODE", lines))
            else:
                err = f"\n    {RED}{trunc(r['error'] or '', 100)}{R}" if r["error"] else ""
                out.append(make_line("EPISODE", [
                    f"{B}{show}{R}  {GRY}{prev}{R} → {badge(val)}{err}",
                ]))
            self.seen_episodes[key] = val
        return out

    async def _poll_ideas(self):
        rows = await self.conn.fetch(
            "SELECT id, angle, idea_type, format, source_ids, generated FROM show_idea ORDER BY created_at DESC LIMIT 20"
        )
        out = []
        for r in rows:
            key = str(r["id"])
            if key in self.seen_ideas:
                continue
            self.seen_ideas.add(key)
            src_count = len(r["source_ids"]) if r["source_ids"] else 0
            angle = trunc(r["angle"] or "", 100)
            lines = [
                f"format={BLU}{r['format']}{R}  type={r['idea_type']}  sources={src_count}  generated={r['generated']}",
            ]
            if angle:
                lines.append(f"{D}{angle}{R}")
            out.append(make_line("IDEA", lines))
        return out

    async def _update_counts(self):
        for table, key in [("source","sources"),("jobs","jobs"),("episode","episodes"),("show_idea","ideas")]:
            row = await self.conn.fetchrow(f"SELECT COUNT(*) AS c FROM {table}")
            self.counts[key] = row["c"] if row else 0

    async def _update_active(self):
        self.active = {"scraping": [], "transforming": [], "embedding": [],
                       "generating": [], "queued_jobs": []}
        sources = await self.conn.fetch(
            "SELECT id FROM source WHERE status = ANY($1)", ["scraping","transforming","embedding"]
        )
        for r in sources:
            for stage in ("scraping","transforming","embedding"):
                pass  # just count
        # refetch with status
        rows = await self.conn.fetch(
            "SELECT status, COUNT(*) AS c FROM source WHERE status IN ('scraping','transforming','embedding') GROUP BY status"
        )
        for r in rows:
            self.active[r["status"]] = ["x"] * r["c"]

        rows2 = await self.conn.fetch(
            "SELECT COUNT(*) AS c FROM episode WHERE status IN ('queued','generating','running')"
        )
        if rows2:
            self.active["generating"] = ["x"] * rows2[0]["c"]

        rows3 = await self.conn.fetch(
            "SELECT COUNT(*) AS c FROM jobs WHERE status = 'running'"
        )
        if rows3:
            self.active["queued_jobs"] = ["x"] * rows3[0]["c"]

# ── Main loop ─────────────────────────────────────────────────────────────────

HEADER_LINES = 5   # number of lines the header occupies

async def run(show_llm: bool, show_llm_output: bool):
    dsn    = os.environ["DATABASE_URL"]
    conn   = await asyncpg.connect(dsn)
    poller = DBPoller(conn)
    llm_reader = LLMLogReader(LLM_LOG, show_llm_output) if show_llm else None

    log_lines: list[str] = []
    spin = 0

    # Initial poll to seed seen state silently
    await poller.poll()

    def redraw_header():
        # Move cursor to top, overwrite header rows
        sys.stdout.write(f"\033[{HEADER_LINES + 1}A")  # up N+1 lines
        for line in render_header(poller.counts, poller.active, spin):
            w = term_width()
            # pad to full width to clear previous content
            sys.stdout.write(f"\r{line}\033[K\n")
        sys.stdout.flush()

    def print_events(events: list[list[str]]):
        for group in events:
            for line in group:
                print(line, flush=True)

    # Print initial blank header placeholder
    for line in render_header(poller.counts, poller.active, spin):
        print(line)
    # Spacer so header rewrites don't clobber log
    print()

    last_db   = 0.0
    last_log  = 0.0

    while True:
        now = time.monotonic()
        spin += 1
        new_events: list[list[str]] = []

        # DB poll
        if now - last_db >= POLL_DB:
            new_events += await poller.poll()
            last_db = now

        # LLM log poll
        if llm_reader and now - last_log >= POLL_LOG:
            new_events += llm_reader.read_new()
            last_log = now

        # Print new events
        if new_events:
            print_events(new_events)

        # Redraw header in place
        redraw_header()

        await asyncio.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description="Curia unified pipeline dashboard.")
    parser.add_argument("--llm",        action="store_true", help="Show LLM call start/end events")
    parser.add_argument("--llm-output", action="store_true", help="Show LLM output previews too (implies --llm)")
    args = parser.parse_args()

    show_llm   = args.llm or args.llm_output
    show_output = args.llm_output

    try:
        asyncio.run(run(show_llm, show_output))
    except KeyboardInterrupt:
        print(f"\n{GRY}dashboard stopped{R}")


if __name__ == "__main__":
    main()
