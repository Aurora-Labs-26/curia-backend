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

SOURCE MATERIAL: each source carries its full article_text — that is your primary
raw material. core_tensions and counterpoints are pre-extracted structural aids.
- Build the hook from the article's own sharpest claims, details, and numbers
- Place core_tensions and counterpoints at the structural turn
- Ground every segment in specifics from the article text, not generic restatement
- Declare what each segment draws on via primitives_used

MULTIPLE SOURCES:
- Structure the episode around ideas, not sources
- Never transition between sources explicitly — the listener should not feel a source boundary
- Weave primitives from different sources into the same segment if they serve the same narrative purpose

Your output must strictly follow the format_config from the briefing packet:
- structure_pattern: each entry maps to a segment in order — use it to guide the purpose of that segment
- segment_count: from episode_constraints — produce exactly this many segments
- target_words and segment_count: from episode_constraints — the transcript will be target_words total across segment_count segments, so allocate enough material per segment to carry its share
- pacing and energy_curve: govern how much material each segment carries and how intensity builds or falls
- resolution_style: determines how the final segment ends — open_ended means no conclusion, explicit means clear takeaway, micro_payoffs means small payoff at every close, partial means reframe without closure
- rules.must_do and rules.must_avoid: hard constraints on every segment, no exceptions

VIBE: each segment also gets exactly one vibe tag, chosen from the closed list in vibe_options
(in the briefing packet). The vibe drives that segment's background music and the transition
cue into the next segment — pick the one that actually matches what the segment does, not
a rotation. Use energy_curve and pacing as loose guidance, not a fixed mapping. The vibe
options and their definitions:
- grounding: Establish context. Scene-setting. Definitions. Orientation.
- curious: Raise a question or introduce something unexpected. Create curiosity without resolving it.
- building: Explain how something works. Step-by-step reasoning. Add evidence or context.
- tension: Introduce contradiction, conflict, uncertainty, competing explanations, or stakes that remain unresolved.
- momentum: Deliver important discoveries quickly. Multiple connected insights. Listener should feel pulled forward.
- expansive: Zoom out. Connect this idea to a broader pattern, another field, or a larger implication.
- payoff: Resolve a question raised earlier. Deliver the central insight or a satisfying intermediate conclusion.
- reflective: Slow down. Invite thought rather than resolution. Leave the listener with perspective instead of new information.
This vibe vocabulary applies only to these numbered body segments — the spoken intro and
outro are not outline segments and are not part of this list.

Output schema — use exactly these keys, no others:
{
  "title": "episode title",
  "thread": "one sentence — the single idea this episode follows",
  "segments": [
    {
      "segment": 1,
      "title": "short listener-facing chapter title",
      "purpose": "what this segment does in the arc",
      "primitives_used": ["article_text from Article A", "core_tensions from Article B"],
      "transition": "one phrase — how this leads to the next segment",
      "vibe": "one of the vibe options above"
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
