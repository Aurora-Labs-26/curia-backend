"""
shows/profiles.py
EpisodeProfile + SpeakerProfile definitions.

What these own (post-config-layer refactor):
  - Speaker identity: name, backstory, speech_patterns
  - Show name + format_name (key into studio/formats.py)
  - The list of speakers attached to each show

What these NO LONGER own:
  - Model selection (outline / transcript LLMs) — moved to config/models.yaml
    under bindings.task and bindings.show
  - Voice IDs — moved to config/models.yaml under bindings.speaker.<name>.voice_id

The transcript LLM resolves which speaker to use by indexing into
speaker_config.speakers; the speaker's `name` is then looked up against
config/models.yaml to fetch the voice_id and TTS model.
"""

from dataclasses import dataclass


@dataclass
class Speaker:
    name: str               # used to look up TTS binding (config/models.yaml: bindings.speaker.<name>)
    backstory: str          # passed to transcript LLM for context
    speech_patterns: str    # 4-6 short behavioral rules that shape generated text


@dataclass
class SpeakerProfile:
    name: str
    speakers: list[Speaker]


@dataclass
class EpisodeProfile:
    name: str
    format_name: str        # key into FORMATS in studio/formats.py
    language: str
    speaker_config: SpeakerProfile

    # Audio backing (intro/outro lead-in/tail-out silence, per-segment vibe BGM,
    # transition SFX) is no longer per-show config — it's driven uniformly by
    # the outline's per-segment vibe tags via core/audio/vibe_mix.py.


# ---------------------------------------------------------------------------
# Speakers
# ---------------------------------------------------------------------------

KENJI_SPEAKER = SpeakerProfile(
    name="kenji",
    speakers=[
        Speaker(
            name="kenji",
            backstory=(
                "Former wire journalist who reported from three continents. "
                "Stepped back to think more carefully about what he was actually witnessing."
            ),
            speech_patterns=(
                "Keeps sentences tight. "
                "Prefers one idea per line. "
                "Occasionally undercuts a statement with a softer follow-up. "
                "Uses contrast sparingly but sharply. "
                "Avoids over-explaining. "
                "Sometimes restates an idea in simpler terms after saying it once."
            )
        )
    ]
)

ARJUN_SPEAKER = SpeakerProfile(
    name="arjun",
    speakers=[
        Speaker(
            name="arjun",
            backstory=(
                "Economist turned essayist. Spent a decade in policy before deciding "
                "the interesting questions were upstream of any policy solution."
            ),
            speech_patterns=(
                "Builds arguments step by step. "
                "Sits with tension before resolving it. "
                "Occasionally impatient with vague claims — pushes for specifics. "
                "Uses precise language; rarely hedges. "
                "Ends segments with an open question rather than a conclusion."
            )
        )
    ]
)

EMEKA_SPEAKER = SpeakerProfile(
    name="emeka",
    speakers=[
        Speaker(
            name="emeka",
            backstory=(
                "Systems thinker with a background in infrastructure and urban planning. "
                "Believes most interesting problems are coordination problems in disguise."
            ),
            speech_patterns=(
                "Moves fast between ideas. "
                "Connects domains without explaining the connection first. "
                "Short punchy sentences followed by one longer one. "
                "Skeptical of received wisdom — names it before dismissing it. "
                "Rarely softens a claim."
            )
        )
    ]
)


# ---------------------------------------------------------------------------
# Episode Profiles — one per format
#
# Note: outline_llm / transcript_llm fields are gone. Model selection happens
# in config/models.yaml under bindings.show.<show_name>.{outline, transcript}
# (or falls back to bindings.task.{outline, transcript}).
# ---------------------------------------------------------------------------

NARRATIVE_DRIFT_PROFILE = EpisodeProfile(
    name="narrative_drift",
    format_name="narrative_drift",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)

CLARITY_ENGINE_SPEAKERS = SpeakerProfile(
    name="clarity_engine",
    speakers=[
        Speaker(
            name="kenji",
            backstory=(
                "Former wire journalist who reported from three continents. "
                "Stepped back to think more carefully about what he was actually witnessing."
            ),
            speech_patterns=(
                "Keeps sentences tight. "
                "Prefers one idea per line. "
                "Builds from concrete detail to the general claim, not the other way around. "
                "Uses contrast sparingly but sharply. "
                "When asked a question, answers it directly before elaborating."
            ),
        ),
        Speaker(
            name="emeka",
            backstory=(
                "Systems thinker with a background in infrastructure and urban planning. "
                "Believes most interesting problems are coordination problems in disguise."
            ),
            speech_patterns=(
                "Asks short, direct questions. "
                "Says 'wait' or 'hold on' when the logic doesn't land. "
                "Connects the explanation to a real-world system or mechanism. "
                "When satisfied, moves on quickly without over-affirming. "
                "Pushes for specifics when the answer stays abstract."
            ),
        ),
    ],
)

CLARITY_ENGINE_PROFILE = EpisodeProfile(
    name="clarity_engine",
    format_name="clarity_engine",
    language="en-US",
    speaker_config=CLARITY_ENGINE_SPEAKERS,
)

MOMENTUM_LOOP_PROFILE = EpisodeProfile(
    name="momentum_loop",
    format_name="momentum_loop",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)

EXPLORATION_ENGINE_SPEAKERS = SpeakerProfile(
    name="exploration_engine",
    speakers=[
        Speaker(
            name="kenji",
            backstory=(
                "Former wire journalist who reported from three continents. "
                "Stepped back to think more carefully about what he was actually witnessing."
            ),
            speech_patterns=(
                "Keeps sentences tight. "
                "States claims plainly before supporting them. "
                "Uses specific evidence — names, numbers, mechanisms. "
                "When challenged, doesn't retreat but reframes: 'that's fair, but consider this.' "
                "Occasionally restates an idea more precisely after the first pass."
            ),
        ),
        Speaker(
            name="emeka",
            backstory=(
                "Systems thinker with a background in infrastructure and urban planning. "
                "Believes most interesting problems are coordination problems in disguise."
            ),
            speech_patterns=(
                "Moves fast between ideas. "
                "Uses 'but what if' and 'there's another way to read this' to pivot. "
                "Connects counter-arguments to structural or systemic patterns. "
                "Concedes partial points clearly before pressing her own. "
                "Ends exchanges with a reframe that shifts the lens."
            ),
        ),
    ],
)

EXPLORATION_ENGINE_PROFILE = EpisodeProfile(
    name="exploration_engine",
    format_name="exploration_engine",
    language="en-US",
    speaker_config=EXPLORATION_ENGINE_SPEAKERS,
)


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

SHOW_PROFILES = {
    "narrative_drift": NARRATIVE_DRIFT_PROFILE,
    "clarity_engine": CLARITY_ENGINE_PROFILE,
    "momentum_loop": MOMENTUM_LOOP_PROFILE,
    "exploration_engine": EXPLORATION_ENGINE_PROFILE,
}

SPEAKER_PROFILES = {
    "kenji": KENJI_SPEAKER,
    "arjun": ARJUN_SPEAKER,
    "emeka": EMEKA_SPEAKER,
}
