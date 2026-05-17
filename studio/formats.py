"""
studio/formats.py
Format config registry for all episode formats.
Formats are configs, not prompts — all format-specific behaviour is encoded here.

Frontend ↔ backend name mapping
--------------------------------
Frontend slug   Backend name         Display label
-----------     ----------------     -------------
slow-burn       narrative_drift      Slow Burn
sharp-take      clarity_engine       Sharp Take
live-wire       momentum_loop        Live Wire
open-verdict    exploration_engine   Open Verdict

Use `resolve_format_name()` to accept either form.
Use `GET /formats` to expose the mapping to clients.
"""

from dataclasses import dataclass, field


@dataclass
class FormatRules:
    must_do: list[str]
    must_avoid: list[str]


@dataclass
class FormatConfig:
    name: str           # backend canonical name, e.g. "narrative_drift"
    frontend_name: str  # frontend slug, e.g. "slow-burn"
    display_name: str   # human label, e.g. "Slow Burn"
    pacing: str
    resolution_style: str
    energy_curve: str
    structure_pattern: list[str]
    voice_style: str
    rules: FormatRules
    default_segment_count: int
    default_length_minutes: int


# ---------------------------------------------------------------------------
# Format Definitions
# ---------------------------------------------------------------------------

NARRATIVE_DRIFT = FormatConfig(
    name="narrative_drift",
    frontend_name="slow-burn",
    display_name="Slow Burn",
    pacing="slow",
    resolution_style="open_ended",
    energy_curve="declining",
    structure_pattern=["scene", "detail", "expansion", "soft_reflection"],
    voice_style="intimate, unhurried",
    rules=FormatRules(
        must_do=[
            "use sensory or concrete details to ground the opening",
            "let ideas develop slowly — resist the urge to explain",
            "maintain a single emotional register throughout",
            "end without resolution — leave the listener sitting with something",
        ],
        must_avoid=[
            "sharp explanations or sudden cognitive spikes",
            "numbered lists or structured arguments",
            "conclusions that close off the idea",
            "high-energy transitions",
        ]
    ),
    default_segment_count=8,
    default_length_minutes=12,
)

CLARITY_ENGINE = FormatConfig(
    name="clarity_engine",
    frontend_name="sharp-take",
    display_name="Sharp Take",
    pacing="medium_fast",
    resolution_style="explicit",
    energy_curve="rising",
    structure_pattern=["question", "context", "mechanism", "example", "summary"],
    voice_style="structured, precise",
    rules=FormatRules(
        must_do=[
            "open with a clear question the episode will answer",
            "explain the mechanism step by step",
            "use a concrete example to anchor each key idea",
            "close with an explicit takeaway the listener can use",
        ],
        must_avoid=[
            "ambiguity or unresolved ideas",
            "long atmospheric passages",
            "open-ended conclusions",
            "jargon without immediate explanation",
        ]
    ),
    default_segment_count=6,
    default_length_minutes=10,
)

MOMENTUM_LOOP = FormatConfig(
    name="momentum_loop",
    frontend_name="live-wire",
    display_name="Live Wire",
    pacing="dynamic_fast",
    resolution_style="micro_payoffs",
    energy_curve="oscillating",
    structure_pattern=["hook", "insight", "story", "hook", "insight"],
    voice_style="energetic, punchy",
    rules=FormatRules(
        must_do=[
            "re-hook the listener every 60 to 90 seconds",
            "keep each segment short — no more than 2 to 3 sentences per beat",
            "deliver a small payoff at the end of every segment",
            "use forward-momentum transitions — always point to what's next",
        ],
        must_avoid=[
            "long exposition or slow buildup",
            "segments that end without a payoff",
            "passive or reflective tone",
            "complex multi-part arguments in a single segment",
        ]
    ),
    default_segment_count=10,
    default_length_minutes=10,
)

EXPLORATION_ENGINE = FormatConfig(
    name="exploration_engine",
    frontend_name="open-verdict",
    display_name="Open Verdict",
    pacing="medium",
    resolution_style="partial",
    energy_curve="steady_with_spikes",
    structure_pattern=["claim", "counterpoint", "expansion", "link", "reframe"],
    voice_style="analytical, exploratory",
    rules=FormatRules(
        must_do=[
            "state a strong claim early, then complicate it",
            "include the strongest counterpoint — steelman it",
            "introduce at least one unexpected connection across sources",
            "end with a reframe — a new way of seeing the original claim",
        ],
        must_avoid=[
            "premature closure or tidy conclusions",
            "one-sided narratives that ignore tension",
            "energy spikes without intellectual payoff",
            "summarising instead of advancing the argument",
        ]
    ),
    default_segment_count=8,
    default_length_minutes=12,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

FORMATS: dict[str, FormatConfig] = {
    "narrative_drift": NARRATIVE_DRIFT,
    "clarity_engine": CLARITY_ENGINE,
    "momentum_loop": MOMENTUM_LOOP,
    "exploration_engine": EXPLORATION_ENGINE,
}

# Secondary index: frontend slug → backend name
_FRONTEND_TO_BACKEND: dict[str, str] = {
    fmt.frontend_name: fmt.name for fmt in FORMATS.values()
}


def resolve_format_name(name: str) -> str:
    """
    Accept either a backend name ("narrative_drift") or a frontend slug ("slow-burn")
    and return the canonical backend name. Raises ValueError if neither matches.
    """
    if name in FORMATS:
        return name
    if name in _FRONTEND_TO_BACKEND:
        return _FRONTEND_TO_BACKEND[name]
    all_valid = list(FORMATS.keys()) + list(_FRONTEND_TO_BACKEND.keys())
    raise ValueError(f"Unknown format: '{name}'. Valid values: {all_valid}")


def get_format(name: str) -> FormatConfig:
    canonical = resolve_format_name(name)
    return FORMATS[canonical]


def format_config_to_dict(fmt: FormatConfig) -> dict:
    """Serialize FormatConfig to a plain dict for the briefing packet."""
    return {
        "name": fmt.name,
        "pacing": fmt.pacing,
        "resolution_style": fmt.resolution_style,
        "energy_curve": fmt.energy_curve,
        "structure_pattern": fmt.structure_pattern,
        "voice_style": fmt.voice_style,
        "rules": {
            "must_do": fmt.rules.must_do,
            "must_avoid": fmt.rules.must_avoid,
        }
    }
