"""
core/prompts/outline.py
DSPy module for episode outline generation.

Reads a JSON briefing packet (from studio/briefing_builder.py) and returns a
structured outline with title, thread, and segments. The outline is a deterministic
allocation — primitive → segment — not prose.

Model selection: caller scopes via `with dspy.context(lm=resolve.llm("outline", show=show_name))`
(see core/llm_config/). Default binding is `haiku-4-5` per config/models.yaml.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt


class GenerateOutline(dspy.Signature):
    """You are a podcast episode architect. Output ONLY valid JSON. No prose, no explanation, no markdown.

Your job is to read the briefing packet and produce a structured episode outline.
You allocate — you do not write prose, you do not editorialize.

SOURCE PRIMITIVES are your raw material. Allocate them across segments:
- Use key_insights and examples early to hook the listener
- Place core_tensions and counterpoints at the structural turn
- Use human_stakes to ground abstract ideas in consequence
- Each primitive should appear in at most one segment
- Declare which primitives you used in each segment via primitives_used

MULTIPLE SOURCES:
- Structure the episode around ideas, not sources
- Never transition between sources explicitly — the listener should not feel a source boundary
- Weave primitives from different sources into the same segment if they serve the same narrative purpose

Your output must strictly follow the format_config from the briefing packet:
- structure_pattern: each entry maps to a segment in order — use it to guide the purpose of that segment
- segment_count: from episode_constraints — produce exactly this many segments
- target_lines_per_segment: from episode_constraints — each segment will become approximately this many spoken lines in the transcript, so allocate enough material per segment to fill that count
- pacing and energy_curve: govern how much material each segment carries and how intensity builds or falls
- resolution_style: determines how the final segment ends — open_ended means no conclusion, explicit means clear takeaway, micro_payoffs means small payoff at every close, partial means reframe without closure
- rules.must_do and rules.must_avoid: hard constraints on every segment, no exceptions

Output schema — use exactly these keys, no others:
{
  "title": "episode title",
  "thread": "one sentence — the single idea this episode follows",
  "segments": [
    {
      "segment": 1,
      "title": "short listener-facing chapter title",
      "purpose": "what this segment does in the arc",
      "primitives_used": ["key_insights from Article A", "examples from Article B"],
      "transition": "one phrase — how this leads to the next segment"
    }
  ]
}"""

    briefing: str = dspy.InputField(
        desc="JSON briefing packet from studio/briefing_builder.py"
    )
    outline_json: str = dspy.OutputField(
        desc='JSON object with keys "title", "thread", and "segments" (an array)'
    )


generate_outline = dspy.Predict(with_prompt(GenerateOutline, "outline"))
