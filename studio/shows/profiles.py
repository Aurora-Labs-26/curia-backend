"""
shows/profiles.py
EpisodeProfile and SpeakerProfile definitions.
Each profile carries model selection, voice config, and format_name.
format_name points to a FormatConfig in studio/formats.py.
The briefing builder reads format_name from the profile to assemble the briefing packet.
"""

from dataclasses import dataclass


@dataclass
class Speaker:
    name: str
    voice_id: str          # path to .wav for XTTS
    backstory: str         # who this person is — passed to transcript LLM for context
    speech_patterns: str   # 4-6 short behavioral rules that change output


@dataclass
class SpeakerProfile:
    name: str
    voice_model: str       # "xtts_v2" | "elevenlabs"
    speakers: list[Speaker]


@dataclass
class EpisodeProfile:
    name: str
    format_name: str       # key into FORMATS in studio/formats.py
    outline_llm: str
    transcript_llm: str
    language: str
    speaker_config: SpeakerProfile


# ---------------------------------------------------------------------------
# Speakers
# ---------------------------------------------------------------------------

KENJI_SPEAKER = SpeakerProfile(
    name="kenji",
    voice_model="xtts_v2",
    speakers=[
        Speaker(
            name="Kenji",
            voice_id="/Users/bhabanimohapatra/Documents/Projects/curia/studio/kenji_reference.wav",
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

# Placeholders — voices to be added when reference WAVs are ready
ARJUN_SPEAKER = SpeakerProfile(
    name="arjun",
    voice_model="xtts_v2",
    speakers=[
        Speaker(
            name="Arjun",
            voice_id="/Users/bhabanimohapatra/Documents/Projects/curia/studio/arjun_reference.wav",
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
    voice_model="xtts_v2",
    speakers=[
        Speaker(
            name="Emeka",
            voice_id="/Users/bhabanimohapatra/Documents/Projects/curia/studio/emeka_reference.wav",
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
# ---------------------------------------------------------------------------

NARRATIVE_DRIFT_PROFILE = EpisodeProfile(
    name="narrative_drift",
    format_name="narrative_drift",
    outline_llm="claude-haiku-4-5-20251001",
    transcript_llm="claude-sonnet-4-6",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)

CLARITY_ENGINE_PROFILE = EpisodeProfile(
    name="clarity_engine",
    format_name="clarity_engine",
    outline_llm="claude-haiku-4-5-20251001",
    transcript_llm="claude-sonnet-4-6",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)

MOMENTUM_LOOP_PROFILE = EpisodeProfile(
    name="momentum_loop",
    format_name="momentum_loop",
    outline_llm="claude-haiku-4-5-20251001",
    transcript_llm="claude-sonnet-4-6",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)

EXPLORATION_ENGINE_PROFILE = EpisodeProfile(
    name="exploration_engine",
    format_name="exploration_engine",
    outline_llm="claude-haiku-4-5-20251001",
    transcript_llm="claude-sonnet-4-6",
    language="en-US",
    speaker_config=KENJI_SPEAKER,
)


# ---------------------------------------------------------------------------
# Registry
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
