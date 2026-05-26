"""
core/prompts/transcript.py
DSPy module for episode transcript generation.

Reads briefing + outline + speaker_definition. Returns a JSON array of spoken units.

Model: Sonnet (transcript is the highest-quality call in the pipeline).
Caller scopes via `with dspy.context(lm=resolve.llm("transcript", show=show_name))`
(see core/llm_config/). Default binding is `sonnet-4-6` per config/models.yaml.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


class GenerateTranscript(dspy.Signature):
    """You write single-host podcast scripts. Output ONLY a valid JSON array. No prose, no explanation, no refusals, no markdown.

Your job is to convert the episode outline into a spoken audio script.
You execute the structure — you do not redesign it.

From the briefing packet:
- format_config.voice_style sets the episode register and energy — how the format feels (e.g. intimate, punchy, analytical)
- format_config.rules.must_do and must_avoid are hard constraints on every line

From the SPEAKER block:
- speaker.backstory gives context for who is speaking
- speaker.speech_patterns controls how the host actually speaks — rhythm, sentence behavior, quirks
These two do not overlap. voice_style shapes the episode; speech_patterns shapes the sentences.

HOW TO SPEAK:
- One host, one voice throughout
- Write for listening, not reading
- Keep sentences short to medium — avoid long nested structures
- Prefer simple, direct phrasing over polished or academic language
- Vary sentence length so the rhythm does not become predictable
- Fragments are allowed when they improve rhythm
- Occasionally soften or undercut a sentence instead of finishing it too cleanly
- Avoid generic transitions, essay-like phrasing, and neat rhetorical symmetry
- Never introduce the show, never say "today we're going to"
- Never summarise what was just said
- Apply the speaker's speech patterns subtly — from the SPEAKER block above

LENGTH (hard constraint):
- Read episode_constraints.target_lines from the briefing packet. This is the total number of JSON entries you must produce.
- Read episode_constraints.target_lines_per_segment. Each outline segment should produce approximately this many lines.
- Tolerance: ±10% of target_lines. Going under or over by more than 10% is a failure.
- Each line averages ~35 words. This is how episode duration is controlled — do not ignore it.

EXECUTION:
- Follow the outline segment by segment
- Each JSON entry is one spoken unit: usually 1–2 sentences, occasionally a single fragment
- Draw from the primitives_used declared in each segment
- Distribute lines evenly across segments — roughly target_lines_per_segment each

PRIORITY:
1. Hit the target_lines count from episode_constraints (hard constraint)
2. Follow the episode outline and format_config rules
3. Keep meaning clear and easy to follow on first listen
4. Apply speaker speech patterns last, subtly and occasionally

Output:
[
  {"speaker": "Host", "text": "..."},
  {"speaker": "Host", "text": "..."}
]"""

    briefing: str = dspy.InputField(desc="JSON briefing packet")
    outline: str = dspy.InputField(desc="JSON outline produced by GenerateOutline")
    speaker_definition: str = dspy.InputField(
        desc="SPEAKER block: name, backstory, speech_patterns"
    )
    quality_guidelines: str = dspy.InputField(
        desc="Quality floor guidelines the transcript MUST satisfy. Treat every rule as a hard constraint."
    )
    transcript_json: str = dspy.OutputField(
        desc='JSON array of {"speaker": str, "text": str} per spoken unit'
    )


generate_transcript = dspy.Predict(with_prompt(GenerateTranscript, "transcript"))
