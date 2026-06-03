"""
studio/formats.py
Format config registry for all episode formats.
Formats are configs, not prompts — all format-specific behaviour is encoded here.
"""

from dataclasses import dataclass, field


@dataclass
class FormatRules:
    must_do: list[str]
    must_avoid: list[str]


WORDS_PER_MINUTE = 150  # standard spoken English TTS rate

# Intro/outro word budgets — subtracted before allocating body words to segments
INTRO_WORDS = 150  # ~8-12 sentences — host intro, show name, pile reference, source name, tease
OUTRO_WORDS = 70   # ~4-5 sentences


@dataclass
class FormatConfig:
    name: str
    pacing: str
    resolution_style: str
    energy_curve: str
    structure_pattern: list[str]
    voice_style: str
    rules: FormatRules
    default_segment_count: int
    default_length_minutes: int
    # Relative depth weight per structure_pattern entry. Controls how body words
    # are distributed across segment types — heavier segments get more words.
    # Must have same length as structure_pattern. Weights are normalised internally.
    segment_weights: list[float] = None

    @property
    def target_words(self) -> int:
        return round(self.default_length_minutes * WORDS_PER_MINUTE)


# ---------------------------------------------------------------------------
# Format Definitions
# ---------------------------------------------------------------------------

NARRATIVE_DRIFT = FormatConfig(
    name="narrative_drift",
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
    # scene and expansion carry the weight; detail is connective; soft_reflection is brief
    segment_weights=[1.4, 0.8, 1.5, 0.8],
)

CLARITY_ENGINE = FormatConfig(
    name="clarity_engine",
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
    # mechanism and example carry the argument; question is a hook; summary is tight
    segment_weights=[0.7, 1.0, 1.6, 1.4, 0.8],
)

MOMENTUM_LOOP = FormatConfig(
    name="momentum_loop",
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
    # insight and story carry the substance; hooks are punchy and short by design
    segment_weights=[0.6, 1.4, 1.2, 0.6, 1.4],
)

EXPLORATION_ENGINE = FormatConfig(
    name="exploration_engine",
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
    # claim/expansion/link/reframe are the argument; counterpoint is a pivot — keep it tight
    segment_weights=[1.4, 0.7, 1.5, 1.2, 1.3],
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


_SLUG_TO_BACKEND: dict[str, str] = {
    "slow-burn":    "narrative_drift",
    "sharp-take":   "clarity_engine",
    "live-wire":    "momentum_loop",
    "open-verdict": "exploration_engine",
}

# Human-readable show names for use in intros/outros
DISPLAY_NAMES: dict[str, str] = {
    "narrative_drift":    "Slow Burn",
    "clarity_engine":     "Sharp Take",
    "momentum_loop":      "Live Wire",
    "exploration_engine": "Open Verdict",
}


def resolve_format_name(name: str) -> str:
    """Accept either a frontend slug (e.g. 'sharp-take') or a backend name
    (e.g. 'clarity_engine') and return the canonical backend name.
    Raises ValueError for unknown values."""
    if name in FORMATS:
        return name
    if name in _SLUG_TO_BACKEND:
        return _SLUG_TO_BACKEND[name]
    raise ValueError(f"Unknown format: '{name}'. Available slugs: {list(_SLUG_TO_BACKEND)} or backend names: {list(FORMATS)}")


def get_format(name: str) -> FormatConfig:
    if name not in FORMATS:
        raise ValueError(f"Unknown format: '{name}'. Available: {list(FORMATS.keys())}")
    return FORMATS[name]


def segment_word_budgets(fmt: FormatConfig, segment_count: int, body_words: int) -> list[int]:
    """
    Return a list of per-segment word budgets that sum to body_words.
    Weights are cycled across segment_count using the structure_pattern weights.
    Falls back to even distribution if no weights defined.
    """
    weights = fmt.segment_weights
    if not weights:
        even = max(50, round(body_words / segment_count))
        return [even] * segment_count
    # Cycle the weight pattern across however many segments the episode has
    cycled = [weights[i % len(weights)] for i in range(segment_count)]
    total_weight = sum(cycled)
    budgets = [max(50, round((w / total_weight) * body_words)) for w in cycled]
    # Adjust last segment to absorb rounding error
    budgets[-1] += body_words - sum(budgets)
    return budgets


def format_config_to_dict(fmt: FormatConfig) -> dict:
    """Serialize FormatConfig to a plain dict for the briefing packet."""
    return {
        "name": fmt.name,
        "display_name": DISPLAY_NAMES.get(fmt.name, fmt.name),
        "pacing": fmt.pacing,
        "resolution_style": fmt.resolution_style,
        "energy_curve": fmt.energy_curve,
        "structure_pattern": fmt.structure_pattern,
        "voice_style": fmt.voice_style,
        "target_words": fmt.target_words,
        "segment_weights": fmt.segment_weights,
        "rules": {
            "must_do": fmt.rules.must_do,
            "must_avoid": fmt.rules.must_avoid,
        }
    }
