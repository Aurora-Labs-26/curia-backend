"""
api/schemas.py
Pydantic request/response models for the FastAPI surface.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl, field_validator


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


class CreateSourceRequest(BaseModel):
    url: HttpUrl
    standalone: bool = True  # True = evaluate this source alone; False = run full cluster pipeline
    format: Optional[str] = Field(
        default=None,
        description="Show format override for standalone sources. Frontend slug "
        "(slow-burn, sharp-take, live-wire, open-verdict) or backend name "
        "(narrative_drift, clarity_engine, momentum_loop, exploration_engine). "
        "When omitted, the LLM picks the format. Ignored for cluster sources.",
    )
    angle: Optional[str] = Field(
        default=None,
        description="Editorial angle/direction for standalone sources, appended to "
        "the generated angle. Ignored for cluster sources.",
    )

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        from studio.formats import resolve_format_name

        return resolve_format_name(v)  # raises ValueError for unknown values → 422


class SourceSummary(BaseModel):
    id: UUID
    title: Optional[str] = None
    url: Optional[str] = None
    status: str
    created_at: datetime
    error: Optional[str] = None
    covered_in: int = 0
    author: Optional[str] = None
    og_image: Optional[str] = None
    connectable: bool = False


class SourceDetail(SourceSummary):
    full_text: Optional[str] = None
    insights: dict[str, Optional[str]] = Field(default_factory=dict)


class CreateJobResponse(BaseModel):
    id: UUID
    status: str
    job_id: Optional[UUID] = None


# ---------------------------------------------------------------------------
# Ideas
# ---------------------------------------------------------------------------


class GenerateIdeasResponse(BaseModel):
    job_id: UUID
    status: str = "queued"


class ShowIdeaSummary(BaseModel):
    id: UUID
    angle: str
    idea_type: str
    format: str
    source_ids: list[UUID]
    generated: bool
    created_at: datetime


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


class CreateEpisodeRequest(BaseModel):
    show_name: str = Field(
        ...,
        description="One of: narrative_drift, clarity_engine, momentum_loop, exploration_engine",
    )
    show_idea_id: Optional[UUID] = None
    editorial_direction: Optional[str] = ""
    length_minutes: Optional[int] = Field(
        default=None,
        ge=3,
        le=30,
        description="Override episode length in minutes (3–30). Defaults to show format's configured length.",
    )
    speaker: Optional[Literal["kenji", "arjun", "emeka"]] = Field(
        default=None,
        description="Solo speaker override. One of: kenji, arjun, emeka. Mutually exclusive with speaker_pair.",
    )
    speaker_pair: Optional[List[Literal["kenji", "arjun", "emeka"]]] = Field(
        default=None,
        description="Two-host pair override. Exactly 2 distinct names. Ignored if speaker is set.",
    )

    @field_validator("speaker_pair")
    @classmethod
    def validate_speaker_pair(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return v
        if len(v) != 2:
            raise ValueError("speaker_pair must contain exactly 2 names")
        if v[0] == v[1]:
            raise ValueError("speaker_pair must contain 2 distinct names")
        # Sort by priority: kenji=1, arjun=2, emeka=3
        priority = {"kenji": 1, "arjun": 2, "emeka": 3}
        return sorted(v, key=lambda s: priority.get(s, 99))


class EpisodeSummary(BaseModel):
    id: UUID
    show_name: Optional[str] = None
    title: Optional[str] = None
    status: str
    created_at: datetime
    error: Optional[str] = None
    quality_score: Optional[float] = None    # 0..1, populated after judge runs
    length_minutes: Optional[int] = None
    speaker_override: Optional[str] = None
    source_ids: list[UUID] = Field(default_factory=list)
    source_objects: list["EpisodeSourceObject"] = Field(default_factory=list)
    outline: Optional[Any] = None
    play_progress: Optional[float] = None
    listened: bool = False
    last_played_at: Optional[datetime] = None
    show_idea_id: Optional[UUID] = None


class EpisodeSourceObject(BaseModel):
    id: UUID
    domain: str
    title: Optional[str] = None


class EpisodeDetail(EpisodeSummary):
    transcript: Optional[Any] = None
    outline: Optional[Any] = None
    audio_path: Optional[str] = None
    source_ids: list[UUID] = Field(default_factory=list)
    source_objects: list[EpisodeSourceObject] = Field(default_factory=list)
    editorial_direction: Optional[str] = None
    quality_feedback: Optional[str] = None
    quality_violations: list[str] = Field(default_factory=list)
    regenerated: bool = False
    tts_timings: Optional[Any] = None
    bgm_plan: Optional[Any] = None


# ---------------------------------------------------------------------------
# Me / KB
# ---------------------------------------------------------------------------


class MeResponse(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: str = "user"


class AuthUserResponse(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: str = "user"
    avatar_url: Optional[str] = None


class KBPayload(BaseModel):
    user_kb: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Admin (QA-only) responses
# ---------------------------------------------------------------------------


class AdminUserSummary(BaseModel):
    id: str
    email: Optional[str] = None
    name: Optional[str] = None
    role: str = "user"
    created_at: datetime
    has_kb: bool = False


class RubricResponse(BaseModel):
    """The judge prompt rendered for a (task, user) pair — what the LLM judge would see."""
    task: str
    user_id: str
    judge_prompt: str


# ---------------------------------------------------------------------------
# Optimization (QA-only) — examples, guidelines, runs
# ---------------------------------------------------------------------------


class GuidelineResponse(BaseModel):
    task: str
    body: str
    version: int = 1


class GuidelineUpdate(BaseModel):
    body: str = Field(..., min_length=10)


class ExampleCreate(BaseModel):
    task: str
    inputs: dict[str, Any]
    scope_type: str = "global"      # global | cohort | user
    scope_value: Optional[str] = None
    label: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExampleResponse(BaseModel):
    id: UUID
    task: str
    scope_type: str
    scope_value: Optional[str] = None
    label: Optional[str] = None
    inputs: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_episode_id: Optional[UUID] = None
    created_by: Optional[str] = None
    created_at: datetime


class ExampleImportFromEpisodeRequest(BaseModel):
    episode_id: UUID
    task: str = "transcript"
    scope_type: str = "global"
    scope_value: Optional[str] = None


class OptimizationRunCreate(BaseModel):
    task: str
    scope_type: str = "global"
    scope_value: Optional[str] = None
    config: dict[str, Any] = Field(default_factory=dict)


class OptimizationRunResponse(BaseModel):
    id: UUID
    task: str
    scope_type: str
    scope_value: Optional[str] = None
    status: str
    metric_name: str = "rubric_judge"
    trainset_size: Optional[int] = None
    valset_size: Optional[int] = None
    metric_score_baseline: Optional[float] = None
    metric_score_optimized: Optional[float] = None
    artifact_path: Optional[str] = None
    promoted: bool = False
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    created_by: Optional[str] = None
    created_at: datetime
