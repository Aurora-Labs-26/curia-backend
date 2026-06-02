"""
core/prompts/transcript_two_host.py
DSPy modules for the three-call two-host transcript pipeline.

Call 1: GenerateHostA — explainer/advocate builds the case
Call 2: GenerateHostB — skeptic/challenger reacts to Host A's actual words
Call 3: MergeDialogue — interleaves both into natural conversation with intro/outro

Model: all three use the transcript binding (Sonnet) since this is the quality point.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


# ---------------------------------------------------------------------------
# Call 1 — Host A (explainer)
# ---------------------------------------------------------------------------


class GenerateHostA(dspy.Signature):
    """You write one half of a two-host podcast script. You are HOST A — the explainer, the advocate.
Output ONLY a valid JSON array. No prose, no explanation, no markdown.

Your job is to build the case. You walk through the material, lay out the argument, connect the dots.
You are genuinely committed to the ideas — not neutral, not hedging."""

    briefing: str = dspy.InputField(desc="JSON briefing packet")
    outline: str = dspy.InputField(desc="JSON outline from GenerateOutline")
    speaker_definition: str = dspy.InputField(desc="Host A speaker block: name, backstory, speech_patterns")
    quality_guidelines: str = dspy.InputField(desc="Quality floor guidelines")
    host_a_json: str = dspy.OutputField(desc='JSON array of {"speaker": str, "text": str}')


# ---------------------------------------------------------------------------
# Call 2 — Host B (skeptic)
# ---------------------------------------------------------------------------


class GenerateHostB(dspy.Signature):
    """You write the SECOND HOST script for a two-host podcast. You are HOST B — the skeptic, the challenger.
Output ONLY a valid JSON array. No prose, no explanation, no markdown.

You have just read Host A's full transcript. Your job is to REACT to what they actually said.
You push back, ask the hard questions, redirect when they skip something important."""

    briefing: str = dspy.InputField(desc="JSON briefing packet")
    outline: str = dspy.InputField(desc="JSON outline")
    speaker_definition: str = dspy.InputField(desc="Host B speaker block: name, backstory, speech_patterns")
    host_a_transcript: str = dspy.InputField(desc="Host A's full transcript JSON to react to")
    quality_guidelines: str = dspy.InputField(desc="Quality floor guidelines")
    host_b_json: str = dspy.OutputField(desc='JSON array of {"speaker": str, "text": str}')


# ---------------------------------------------------------------------------
# Call 3 — Merger
# ---------------------------------------------------------------------------


class MergeDialogue(dspy.Signature):
    """You merge two separate podcast host scripts into a single natural conversation.
Output ONLY a valid JSON array. No prose, no explanation, no markdown.

You have Host A's script (the explainer) and Host B's script (the skeptic).
Your job is to INTERLEAVE them into a single flowing dialogue that sounds like two people
actually talking to each other."""

    briefing: str = dspy.InputField(desc="JSON briefing packet (for target_lines and format rules)")
    outline: str = dspy.InputField(desc="JSON outline")
    host_a_transcript: str = dspy.InputField(desc="Host A's full transcript JSON")
    host_b_transcript: str = dspy.InputField(desc="Host B's full transcript JSON")
    speaker_a_name: str = dspy.InputField(desc="Host A's speaker name (lowercase)")
    speaker_b_name: str = dspy.InputField(desc="Host B's speaker name (lowercase)")
    merged_json: str = dspy.OutputField(
        desc='Interleaved JSON array of {"speaker": str, "text": str} with intro and outro'
    )


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

generate_host_a = dspy.Predict(with_prompt(GenerateHostA, "transcript_host_a"))
generate_host_b = dspy.Predict(with_prompt(GenerateHostB, "transcript_host_b"))
merge_dialogue = dspy.Predict(with_prompt(MergeDialogue, "transcript_merge"))
