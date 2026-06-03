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
    intro_budget_words: int = 150
    outro_budget_words: int = 70

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
    pacing="fast",
    resolution_style="implication",
    energy_curve="linear",
    structure_pattern=["hook", "stakes", "mechanism", "payoff", "landing"],
    voice_style="Planet Money narration — complete sentences, editorial confidence, no fragments",
    rules=FormatRules(
        must_do=[
            "open with a single unexpected fact or concrete scene from the source — not a question, not a thesis",
            "establish who is affected and how within the first two segments",
            "deliver the mechanism — why this happened or why it matters, not just what happened",
            "land on one thing to remember — a single insight, number, or line",
            "close with a single forward-pointing implication — what this means, not what to ask next",
        ],
        must_avoid=[
            "re-hooking mid-episode — the listener chose brevity, trust that",
            "closing with a question or an open thread — Rundown completes, it does not open",
            "padding to fill time — if the source is thin, the episode is short",
            "more than one core idea — Rundown carries one thing, carried well",
            "forward-momentum transitions that tease what's next — each segment completes itself",
        ]
    ),
    default_segment_count=5,
    default_length_minutes=4,
    intro_budget_words=40,
    outro_budget_words=30,
)

EXPLORATION_ENGINE = FormatConfig(
    name="exploration_engine",
    pacing="medium",
    resolution_style="located_tension",
    energy_curve="steady_with_spikes",
    structure_pattern=["hook", "tension", "counterpoint", "mechanism", "turn", "landing"],
    voice_style="Planet Money narration — complete sentences, analytical confidence, no fragments",
    rules=FormatRules(
        must_do=[
            "open with the source's claim stated at its strongest — make the best case for it before challenging anything",
            "establish what's actually at stake in believing this claim — who it affects, what depends on it",
            "bring the strongest opposing case from world knowledge — the counterpoint does not need to come from the source",
            "separate fact from opinion explicitly — what the evidence shows vs. what the source concludes from it",
            "identify where the genuine disagreement lives vs. where the conflict is false or semantic",
            "close by locating the unresolved tension precisely — not a verdict, not a question, just what's actually in dispute",
        ],
        must_avoid=[
            "stating the claim neutrally — open with it at full strength",
            "strawmanning the counterpoint — the opposing case must be the strongest available version",
            "forcing a verdict — the tension should be intact at the end, not resolved",
            "blending fact and opinion — they must be distinguishable at every point",
            "closing with a summary — the landing must name the disagreement, not recap the episode",
            "staying within the source for the counterpoint — world knowledge is required here",
        ]
    ),
    default_segment_count=6,
    default_length_minutes=12,
    segment_weights=[1.2, 1.0, 1.5, 1.3, 1.2, 0.8],
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
    "momentum_loop":      "Rundown",
    "exploration_engine": "Counter",
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
        },
        "intro_budget_words": fmt.intro_budget_words,
        "outro_budget_words": fmt.outro_budget_words,
    }
