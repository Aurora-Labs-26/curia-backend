"""
scripts/onboard.py
Interactive CLI: collect Curia user knowledge bank (KB) via prompts and persist.

Usage:
    python scripts/onboard.py --user-id <uuid>
    python scripts/onboard.py --token <api_token>     # alt: lookup by token
    python scripts/onboard.py --user-id default       # the seeded default user

Or inside docker-compose:
    docker compose exec api python scripts/onboard.py --user-id default

The flow asks ~10 questions and writes the result to users.user_kb (JSONB).
Skip any question by hitting Enter; defaults are preserved.

This is the v1 mechanism — the same data eventually populates via a frontend
hitting PUT /me/kb. The schema is shared; the input shape is the only difference.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

from core.db.connection import db_fetchrow
from core.kb import (
    Dislikes,
    Identity,
    Interests,
    ListeningContext,
    Preferences,
    UserKB,
    load_kb,
    save_kb,
)


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------


def _prompt(question: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    raw = input(f"\n{question}{suffix}\n› ").strip()
    return raw if raw else (default or "")


def _prompt_list(question: str, default: list[str] | None = None) -> list[str]:
    default_str = ", ".join(default) if default else ""
    raw = _prompt(f"{question} (comma-separated)", default_str)
    if not raw:
        return default or []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _prompt_choice(
    question: str,
    choices: list[str],
    default: str | None = None,
) -> str:
    suffix = f" [{default}]" if default else ""
    print(f"\n{question}{suffix}")
    for i, c in enumerate(choices, start=1):
        marker = " ← default" if c == default else ""
        print(f"  {i}. {c}{marker}")
    while True:
        raw = input("› ").strip()
        if not raw and default:
            return default
        if raw.isdigit() and 1 <= int(raw) <= len(choices):
            return choices[int(raw) - 1]
        if raw in choices:
            return raw
        print(f"  please pick one: {', '.join(choices)} (or a number 1-{len(choices)})")


def _prompt_int(question: str, default: int, lo: int, hi: int) -> int:
    while True:
        raw = _prompt(f"{question} (range {lo}-{hi})", str(default))
        if not raw:
            return default
        try:
            n = int(raw)
            if lo <= n <= hi:
                return n
            print(f"  must be between {lo} and {hi}")
        except ValueError:
            print("  not a number, try again")


def _prompt_float(question: str, default: float, lo: float, hi: float) -> float:
    while True:
        raw = _prompt(f"{question} (range {lo}-{hi})", str(default))
        if not raw:
            return default
        try:
            f = float(raw)
            if lo <= f <= hi:
                return f
            print(f"  must be between {lo} and {hi}")
        except ValueError:
            print("  not a number, try again")


# ---------------------------------------------------------------------------
# The onboarding script — 10 questions ≈ 5 minutes
# ---------------------------------------------------------------------------


async def onboard(user_id: str) -> UserKB:
    existing = await load_kb(user_id)

    print(
        "\n"
        "──────────────────────────────────────────────\n"
        f" Onboarding KB for user_id={user_id}\n"
        "──────────────────────────────────────────────\n"
        "Existing values shown in [brackets]; press Enter to keep them.\n"
        "All fields are optional; skip anything you're unsure about."
    )

    # ── Identity ──────────────────────────────────────────────────────────
    name = _prompt("Your name (or anything to address you by)?", existing.identity.name)
    reading_volume = _prompt(
        "How many articles do you save per week, roughly?",
        existing.identity.reading_volume_per_week,
    )

    # ── Interests ─────────────────────────────────────────────────────────
    topics = _prompt_list(
        "What topics interest you most?",
        existing.interests.topics,
    )
    current_obsession = _prompt(
        "What's the idea you're most actively chewing on right now?",
        existing.interests.current_obsession,
    )

    # ── Preferences ──────────────────────────────────────────────────────
    preferred_length = _prompt_int(
        "Preferred episode length in minutes?",
        existing.preferences.preferred_length_minutes,
        lo=3,
        hi=30,
    )
    formats = _prompt_list(
        "Preferred formats? (narrative_drift, clarity_engine, momentum_loop, exploration_engine)",
        list(existing.preferences.preferred_formats),
    )
    preferred_tone = _prompt(
        "Preferred tone? (dry, warm, analytical, conversational, literary, punchy, dispassionate)",
        existing.preferences.preferred_tone,
    )
    tolerates_ambiguity = _prompt_choice(
        "Tolerance for unresolved/ambiguous endings?",
        choices=["low", "medium", "high"],
        default=existing.preferences.tolerates_ambiguity,
    )
    novelty_appetite = _prompt_float(
        "Novelty appetite? (0=stay strictly in lane, 1=always surprise me)",
        existing.preferences.novelty_appetite,
        lo=0.0,
        hi=1.0,
    )

    # ── Listening context ─────────────────────────────────────────────────
    when = _prompt(
        "When do you typically listen? (free text — e.g. 'morning commute')",
        existing.listening_context.when,
    )
    while_doing = _prompt(
        "What are you usually doing while listening? (e.g. 'walking', 'driving')",
        existing.listening_context.while_doing,
    )

    # ── Dislikes ──────────────────────────────────────────────────────────
    dislike_themes = _prompt_list(
        "Themes you'd rather avoid?",
        existing.dislikes.themes,
    )
    dislike_tones = _prompt_list(
        "Tones you'd rather avoid? (e.g. 'sponsor-y', 'overly polished')",
        existing.dislikes.tones,
    )

    # ── Assemble ──────────────────────────────────────────────────────────
    kb = UserKB(
        identity=Identity(
            name=name or None,
            reading_volume_per_week=reading_volume or None,
        ),
        interests=Interests(
            topics=topics,
            current_obsession=current_obsession or None,
        ),
        preferences=Preferences(
            preferred_length_minutes=preferred_length,
            preferred_formats=formats,        # validation will catch typos
            preferred_tone=preferred_tone or None,
            tolerates_ambiguity=tolerates_ambiguity,
            novelty_appetite=novelty_appetite,
        ),
        listening_context=ListeningContext(
            when=when or None,
            while_doing=while_doing or None,
        ),
        dislikes=Dislikes(
            themes=dislike_themes,
            tones=dislike_tones,
        ),
        version=existing.version + 1,
    )

    print("\n──────────────────────────────────────────────")
    print(" KB summary")
    print("──────────────────────────────────────────────")
    print(kb.model_dump_json(indent=2))
    confirm = input("\nSave? [Y/n] ").strip().lower()
    if confirm and confirm[0] == "n":
        print("\nNot saved. Re-run when ready.")
        return kb

    await save_kb(user_id, kb)
    print(f"\nSaved KB v{kb.version} for user {user_id}.")
    return kb


# ---------------------------------------------------------------------------
# Resolve --user-id or --token
# ---------------------------------------------------------------------------


async def _resolve_user(args: argparse.Namespace) -> str:
    if args.user_id:
        row = await db_fetchrow(
            "SELECT id FROM users WHERE id = $id",
            {"id": args.user_id},
        )
        if not row:
            raise SystemExit(f"user {args.user_id!r} not found")
        return str(row["id"])
    if args.token:
        row = await db_fetchrow(
            "SELECT id FROM users WHERE api_token = $token",
            {"token": args.token},
        )
        if not row:
            raise SystemExit(f"no user with that api_token")
        return str(row["id"])
    raise SystemExit("pass --user-id or --token")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Curia onboarding — populate user_kb interactively")
    parser.add_argument("--user-id", help="User UUID (or 'default' for the seeded user)")
    parser.add_argument("--token", help="API token to look up the user by")
    args = parser.parse_args()

    try:
        user_id = await _resolve_user(args)
        await onboard(user_id)
    except (KeyboardInterrupt, EOFError):
        print("\n\naborted.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
