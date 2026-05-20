"""
scripts/tail_llm.py
Pretty-print LLM calls from logs/llm.log in real time.

Each entry shows:
  - Task (outline / transcript / idea_eval)
  - Duration and output length
  - Full LLM output text

Usage:
  python3 scripts/tail_llm.py              # tail live
  python3 scripts/tail_llm.py --all        # also show historical entries
  python3 scripts/tail_llm.py --outputs    # show full LLM output text
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

LOGS_DIR = Path(os.getenv("CURIA_LOGS_DIR", Path(__file__).parent.parent / "logs"))
LLM_LOG  = LOGS_DIR / "llm.log"

# ── ANSI ─────────────────────────────────────────────────────────────────────
RESET   = "\033[0m"
BOLD    = "\033[1m"
DIM     = "\033[2m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
CYAN    = "\033[36m"
MAGENTA = "\033[35m"
RED     = "\033[31m"

def parse_args():
    p = argparse.ArgumentParser(description="Tail and pretty-print LLM log entries.")
    p.add_argument("--all",     action="store_true", help="Show historical entries too (default: tail only)")
    p.add_argument("--outputs", action="store_true", help="Print full LLM output text")
    return p.parse_args()


def format_entry(record: dict, show_outputs: bool) -> str | None:
    msg = record.get("text", record.get("record", {}).get("message", ""))
    if not msg:
        return None

    ts = record.get("record", {}).get("time", {}).get("repr", "")[:19] if isinstance(record.get("record"), dict) else ""

    if "LLM_CALL_START" in msg:
        parts = dict(p.split("=", 1) for p in msg.split("|")[1].strip().split() if "=" in p)
        task  = parts.get("task", "?")
        show  = parts.get("show", "")
        blen  = parts.get("briefing_len", "?")
        return (
            f"\n{BOLD}{CYAN}▶ LLM CALL{RESET}  task={YELLOW}{task}{RESET}  show={show}  briefing_len={blen}"
            + (f"  {DIM}{ts}{RESET}" if ts else "")
        )

    if "LLM_CALL_END" in msg:
        parts = dict(p.split("=", 1) for p in msg.split("|")[1].strip().split() if "=" in p)
        dur   = parts.get("duration", "?")
        olen  = parts.get("output_len", "?")
        return f"  {GREEN}✓ done{RESET}  {dur}s  output_len={olen}"

    if "LLM_OUTPUT" in msg and show_outputs:
        # Format: "LLM_OUTPUT | task=outline | <text>"
        segments = msg.split("|", 2)
        text = segments[2].strip() if len(segments) > 2 else msg
        sep = "─" * 60
        return f"  {DIM}{sep}\n{text}\n{sep}{RESET}"

    return None


def tail(path: Path, show_all: bool, show_outputs: bool):
    if not path.exists():
        print(f"{RED}[tail_llm]{RESET} Log file not found: {path}", flush=True)
        print("Start the worker or run a test script to generate LLM calls.", flush=True)
        return

    print(f"{BOLD}[tail_llm]{RESET} Watching {path}  (Ctrl+C to stop)\n", flush=True)

    with open(path, "r") as f:
        if not show_all:
            f.seek(0, 2)  # jump to end

        while True:
            line = f.readline()
            if not line:
                time.sleep(0.25)
                continue
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            out = format_entry(record, show_outputs)
            if out:
                print(out, flush=True)


if __name__ == "__main__":
    args = parse_args()
    try:
        tail(LLM_LOG, show_all=args.all, show_outputs=args.outputs)
    except KeyboardInterrupt:
        print(f"\n{DIM}[tail_llm] stopped{RESET}")
