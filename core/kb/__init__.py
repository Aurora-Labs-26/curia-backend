"""
core/kb
=======

User Knowledge Bank (KB) — the structured per-user state that drives personalization.

Schema lives in core/kb/schema.py.
Persistence: users.user_kb JSONB column (one row per user).

Public API:
    UserKB                  Pydantic model — full KB shape
    load_kb(user_id)        async — read from DB, validate, return UserKB
    save_kb(user_id, kb)    async — validate, persist
    empty_kb()              factory for a freshly-onboarded blank KB

Used by:
    - api/routes/me.py        (GET/PUT /me/kb)
    - scripts/onboard.py      (CLI Q&A → save_kb)
    - studio/generator.py     (briefing builder reads preferences)
    - optimization/rubrics/   (judge prompt is built from KB)
"""

from .schema import (
    Dislikes,
    Identity,
    Interests,
    ListeningContext,
    Preferences,
    UserKB,
)
from .store import empty_kb, load_kb, save_kb

__all__ = [
    "UserKB",
    "Identity",
    "Interests",
    "Preferences",
    "ListeningContext",
    "Dislikes",
    "load_kb",
    "save_kb",
    "empty_kb",
]
