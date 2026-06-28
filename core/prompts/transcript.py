"""
core/prompts/transcript.py
DSPy module for single-host episode transcript generation.

All quality rules, AI tell bans, examples, and structural instructions live in
prompts/transcript.txt. This file defines inputs/outputs only.

Model: Sonnet (transcript is the highest-quality call in the pipeline).
Caller scopes via `with dspy.context(lm=resolve.llm("transcript", show=show_name))`
(see core/llm_config/). Default binding is `sonnet-4-6` per config/models.yaml.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


class GenerateTranscript(dspy.Signature):
    """You are a single-host podcast scriptwriter. Your only output is a valid JSON array of spoken lines. No prose, no markdown, no explanation."""

    briefing: str = dspy.InputField(
        desc=(
            "The full briefing packet as JSON, wrapped in <briefing> tags. "
            "Contains: format, format_config (voice_style, rules, energy_curve), "
            "episode_constraints (target_words, segment_count, intro_budget_words, outro_budget_words), "
            "editorial_direction, source_primitives, and optional listener_context."
        )
    )

    speaker: str = dspy.InputField(
        desc=(
            "Speaker definition wrapped in <speaker> tags. "
            "Contains: name, backstory, and speech_patterns. "
            "speech_patterns controls sentence rhythm and quirks — apply subtly, not literally."
        )
    )

    outline: str = dspy.InputField(
        desc=(
            "Episode outline as JSON, wrapped in <outline> tags. "
            "Contains: title, thread, and segments array. "
            "Each segment has: segment number, title, purpose, primitives_used, transition, vibe. "
            "Execute this structure exactly — do not redesign it."
        )
    )

    transcript_json: str = dspy.OutputField(
        desc=(
            'JSON array of {"speaker": str, "text": str, "segment": int}. No wrapper object, '
            "no markdown fences. segment is the outline segment number for body lines, "
            "-1 for the intro lines you write, 0 for the outro lines you write."
        )
    )


generate_transcript = dspy.Predict(with_prompt(GenerateTranscript, "transcript"))
