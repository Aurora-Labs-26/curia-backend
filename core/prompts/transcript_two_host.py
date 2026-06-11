"""
core/prompts/transcript_two_host.py
DSPy modules for the three-call two-host transcript pipeline.

Call 1: GenerateHostA — builds the body argument (~60% of words). Role defined by
         host_a_role in the briefing (e.g. teacher, thesis holder).
Call 2: GenerateHostB — reacts to Host A's actual words (~40% of words). Role defined by
         host_b_role in the briefing (e.g. student, antithesis holder).
Call 3: MergeDialogue — interleaves both into natural conversation, writes intro and outro.

All quality rules, AI tell bans, examples, and structural instructions live in the
corresponding .txt prompt files (transcript_host_a.txt, transcript_host_b.txt,
transcript_merge.txt). The signatures define inputs/outputs only.

Model: all three use the transcript binding (Sonnet) since this is the quality point.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


# ---------------------------------------------------------------------------
# Call 1 — Host A
# ---------------------------------------------------------------------------


class GenerateHostA(dspy.Signature):
    """You write one half of a two-host podcast script. You are HOST A.
Output ONLY a valid JSON array. No prose, no explanation, no markdown."""

    briefing: str = dspy.InputField(
        desc="The full briefing packet as JSON, wrapped in <briefing> tags."
    )
    speaker: str = dspy.InputField(
        desc="Host A speaker definition wrapped in <speaker> tags: name, backstory, role, speech_patterns."
    )
    outline: str = dspy.InputField(
        desc="Episode outline as JSON, wrapped in <outline> tags."
    )
    host_a_json: str = dspy.OutputField(
        desc='JSON array of {"speaker": str, "text": str}. No wrapper, no markdown fences.'
    )


# ---------------------------------------------------------------------------
# Call 2 — Host B (reacts to A)
# ---------------------------------------------------------------------------


class GenerateHostB(dspy.Signature):
    """You write the second half of a two-host podcast script. You are HOST B.
Output ONLY a valid JSON array. No prose, no explanation, no markdown.
You are REACTING to Host A's actual words provided in <host_a_transcript>."""

    briefing: str = dspy.InputField(
        desc="The full briefing packet as JSON, wrapped in <briefing> tags."
    )
    speaker: str = dspy.InputField(
        desc="Host B speaker definition wrapped in <speaker> tags: name, backstory, role, speech_patterns."
    )
    outline: str = dspy.InputField(
        desc="Episode outline as JSON, wrapped in <outline> tags."
    )
    host_a_transcript: str = dspy.InputField(
        desc="Host A's full transcript JSON, wrapped in <host_a_transcript> tags. React to their actual words."
    )
    host_b_json: str = dspy.OutputField(
        desc='JSON array of {"speaker": str, "text": str}. No wrapper, no markdown fences.'
    )


# ---------------------------------------------------------------------------
# Call 3 — Merger
# ---------------------------------------------------------------------------


class MergeDialogue(dspy.Signature):
    """You merge two separate podcast host scripts into a single natural conversation.
Output ONLY a valid JSON array. No prose, no explanation, no markdown."""

    briefing: str = dspy.InputField(
        desc="The full briefing packet as JSON, wrapped in <briefing> tags (for target_words, format rules, and display_name)."
    )
    outline: str = dspy.InputField(
        desc="Episode outline as JSON, wrapped in <outline> tags. Use this to verify segment coverage."
    )
    host_a_transcript: str = dspy.InputField(
        desc="Host A's full transcript JSON, wrapped in <host_a_transcript> tags."
    )
    host_b_transcript: str = dspy.InputField(
        desc="Host B's full transcript JSON, wrapped in <host_b_transcript> tags."
    )
    speaker_a_name: str = dspy.InputField(desc="Host A's speaker name (lowercase)")
    speaker_b_name: str = dspy.InputField(desc="Host B's speaker name (lowercase)")
    merged_json: str = dspy.OutputField(
        desc='Interleaved JSON array of {"speaker": str, "text": str} with intro and outro.'
    )


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

generate_host_a = dspy.Predict(with_prompt(GenerateHostA, "transcript_host_a"))
generate_host_b = dspy.Predict(with_prompt(GenerateHostB, "transcript_host_b"))
merge_dialogue = dspy.Predict(with_prompt(MergeDialogue, "transcript_merge"))
