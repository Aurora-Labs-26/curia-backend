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

    # Optional audio assets — paths resolved relative to the repo root (or absolute).
    # When set, synthesize_and_stitch in studio/generator.py will:
    #   - crossfade intro_audio_path into the TTS body start
    #   - crossfade outro_audio_path out from the TTS body end
    #   - loop + overlay music_audio_path under the entire stitched body at music_gain_db
    # Crossfade timing constants live in studio/generator.py (INTRO_FULL_MS etc.)
    intro_audio_path: str | None = None
    outro_audio_path: str | None = None
    music_audio_path: str | None = None
    music_gain_db: float = -16.0   # negative = quieter; -16 keeps speech clearly on top


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

_AUDIO_INTRO = "assets/audio/intro.mp3"
_AUDIO_OUTRO = "assets/audio/outro.mp3"

NARRATIVE_DRIFT_PROFILE = EpisodeProfile(
    name="narrative_drift",
    format_name="narrative_drift",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
    intro_audio_path=_AUDIO_INTRO,
    outro_audio_path=_AUDIO_OUTRO,
)

CLARITY_ENGINE_PROFILE = EpisodeProfile(
    name="clarity_engine",
    format_name="clarity_engine",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
    intro_audio_path=_AUDIO_INTRO,
    outro_audio_path=_AUDIO_OUTRO,
)

MOMENTUM_LOOP_PROFILE = EpisodeProfile(
    name="momentum_loop",
    format_name="momentum_loop",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
    intro_audio_path=_AUDIO_INTRO,
    outro_audio_path=_AUDIO_OUTRO,
)

EXPLORATION_ENGINE_PROFILE = EpisodeProfile(
    name="exploration_engine",
    format_name="exploration_engine",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
    intro_audio_path=_AUDIO_INTRO,
    outro_audio_path=_AUDIO_OUTRO,
)


# ---------------------------------------------------------------------------
# Two-host speaker config — Kenji (explainer) + Arjun (skeptic)
# ---------------------------------------------------------------------------

CROSSFIRE_SPEAKERS = SpeakerProfile(
    name="crossfire",
    speakers=[
        Speaker(
            name="kenji",
            backstory=(
                "Former wire journalist who reported from three continents. "
                "Stepped back to think more carefully about what he was actually witnessing. "
                "In this show, he builds the case — he's the one who found the material and wants to make it land."
            ),
            speech_patterns=(
                "Keeps sentences tight. "
                "Prefers one idea per line. "
                "Builds arguments with concrete details before stating the conclusion. "
                "Uses contrast sparingly but sharply. "
                "When challenged, responds with evidence rather than deflection."
            ),
        ),
        Speaker(
            name="arjun",
            backstory=(
                "Economist turned essayist. Spent a decade in policy before deciding "
                "the interesting questions were upstream of any policy solution. "
                "In this show, he's the skeptic — harder to convince, asks the uncomfortable questions."
            ),
            speech_patterns=(
                "Asks pointed questions. "
                "Sits with tension before resolving it. "
                "Occasionally impatient with vague claims — pushes for specifics. "
                "Concedes clearly when convinced, doesn't hedge. "
                "Ends challenges with an open question rather than a counterstatement."
            ),
        ),
    ],
)

CROSSFIRE_PROFILE = EpisodeProfile(
    name="crossfire",
    format_name="crossfire",
    language="en-US",
    speaker_config=CROSSFIRE_SPEAKERS,
    intro_audio_path=_AUDIO_INTRO,
    outro_audio_path=_AUDIO_OUTRO,
)


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

SHOW_PROFILES = {
    "narrative_drift": NARRATIVE_DRIFT_PROFILE,
    "clarity_engine": CLARITY_ENGINE_PROFILE,
    "momentum_loop": MOMENTUM_LOOP_PROFILE,
    "exploration_engine": EXPLORATION_ENGINE_PROFILE,
    "crossfire": CROSSFIRE_PROFILE,
}

SPEAKER_PROFILES = {
    "kenji": KENJI_SPEAKER,
    "arjun": ARJUN_SPEAKER,
    "emeka": EMEKA_SPEAKER,
}
