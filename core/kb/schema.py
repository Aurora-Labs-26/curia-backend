"""
core/kb/schema.py
Pydantic schema for the User Knowledge Bank (KB).

Layered design (see FUTURE_THESIS_1.md §3.2 for the intent):
  Layer 1 (now)    — onboarding-stated facts:  identity, interests, preferences,
                     listening_context, dislikes
  Layer 2 (later)  — derived from behavior (completion rates, skip patterns)
  Layer 3 (later)  — companion curatorial state (open_threads, dismissed_ideas)

For v1 we only ship Layer 1 — populated via scripts/onboard.py or PUT /me/kb.
Layers 2-3 plug in as new fields without breaking older payloads.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Layer 1 — stated facts (onboarding output)
# ---------------------------------------------------------------------------


Tone = Literal[
    "dry",
    "warm",
    "analytical",
    "conversational",
    "literary",
    "punchy",
    "dispassionate",
]

AmbiguityTolerance = Literal["low", "medium", "high"]

FormatName = Literal[
    "narrative_drift",
    "clarity_engine",
    "momentum_loop",
    "exploration_engine",
]


class Identity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    reading_volume_per_week: str | None = Field(
        default=None,
        description="Free text — e.g. '5-10 articles', 'a couple', 'lots'",
    )


class Interests(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[str] = Field(default_factory=list)
    current_obsession: str | None = Field(
        default=None,
        description="What the user is most actively chewing on right now",
    )


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_length_minutes: int | None = Field(default=None, ge=3, le=30)
    preferred_formats: list[FormatName] = Field(default_factory=list)
    preferred_tone: Tone | None = None
    tolerates_ambiguity: AmbiguityTolerance | None = None
    novelty_appetite: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0=stay strictly in lane, 1=always surprise me",
    )


class ListeningContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: str | None = Field(
        default=None,
        description="When the user typically listens — e.g. 'morning_commute', 'evening walk'",
    )
    while_doing: str | None = Field(
        default=None,
        description="What the user is doing while listening — e.g. 'walking', 'driving'",
    )


class Dislikes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formats: list[FormatName] = Field(default_factory=list)
    tones: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)


class UserKB(BaseModel):
    """
    Top-level knowledge bank object stored in users.user_kb (JSONB).

    All sections optional — `empty_kb()` returns a usable empty shell that the
    rubric generator can render against (it'll fall back to guideline-only).
    """

    model_config = ConfigDict(extra="forbid")

    identity: Identity = Field(default_factory=Identity)
    interests: Interests = Field(default_factory=Interests)
    preferences: Preferences = Field(default_factory=Preferences)
    listening_context: ListeningContext = Field(default_factory=ListeningContext)
    dislikes: Dislikes = Field(default_factory=Dislikes)

    # Bookkeeping — used by rubric cache to detect when to regenerate
    version: int = 1
