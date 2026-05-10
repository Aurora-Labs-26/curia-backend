"""
core/prompts/idea_evaluation.py
DSPy modules for show-idea generation from clustered or standalone source groups.

Two signatures:
  - EvaluateIdeasBatch  → one call across many groups, returns a list of ideas
  - EvaluateSingleIdea  → fallback: one group at a time, returns one idea

Caller (intelligence/idea_generator.py) tries the batch first; on parse/API failure
it falls back to per-group calls. That control flow stays unchanged.

The expected schemas mirror the existing prompts so JSON parsing on the caller side
keeps working.
"""

from __future__ import annotations

import dspy

# ---------------------------------------------------------------------------
# Batch — one call across many groups
# ---------------------------------------------------------------------------


class EvaluateIdeasBatch(dspy.Signature):
    """You generate podcast episode ideas for a single-host audio show.

Each group contains one or more articles with extracted insights. Your job is to find the most
interesting, non-obvious angle that could sustain a full episode — not a summary of the articles,
but a genuine idea the material makes possible.

A good angle:
- Makes a specific claim or observation, not a vague topic
- Has tension, surprise, or something the listener wouldn't already assume
- Can be explored for 10-12 minutes without exhausting itself

Each group has a GROUP_ID, a type (CLUSTER or STANDALONE), and one or more articles with insights.
For each group, output one angle. You MUST include the group_id exactly as given.
Only skip a group if it has no insights at all.

Also recommend the format that best fits the angle and material:
- narrative_drift: slow, atmospheric, open-ended — for personal, emotional, or reflective material
- clarity_engine: structured, rising — for explaining a mechanism, system, or counterintuitive fact
- momentum_loop: fast, punchy — for actionable ideas with multiple payoffs and high energy
- exploration_engine: analytical — for ideas with strong tension, counterpoints, or a reframe at the end

Output ONLY a valid JSON array. No prose, no markdown:
[
  {
    "group_id": "the exact group_id from the input",
    "type": "standalone" or "cluster",
    "angle": "one sentence — what this episode is actually about",
    "format": "narrative_drift" or "clarity_engine" or "momentum_loop" or "exploration_engine"
  }
]"""

    groups_text: str = dspy.InputField(
        desc="Concatenated group blocks, each prefixed with '--- GROUP_ID: gN | TYPE ---'"
    )
    ideas_json: str = dspy.OutputField(
        desc="JSON array of {group_id, type, angle, format}, one entry per non-empty group"
    )


# ---------------------------------------------------------------------------
# Per-group fallback — used if batch fails
# ---------------------------------------------------------------------------


class EvaluateSingleIdea(dspy.Signature):
    """You generate a single podcast episode idea for a single-host audio show.

Given one or more articles with insights, find the most interesting non-obvious angle — not a summary,
but a genuine idea the material makes possible.

A good angle makes a specific claim, has tension or surprise, and can sustain 10-12 minutes.

Also recommend the best format:
- narrative_drift: slow, atmospheric, open-ended — personal or reflective material
- clarity_engine: structured, rising — explaining a mechanism or counterintuitive fact
- momentum_loop: fast, punchy — actionable ideas with multiple payoffs
- exploration_engine: analytical — tension, counterpoints, or a reframe at the end

Output ONLY valid JSON: {"type": "standalone", "angle": "one sentence", "format": "narrative_drift or clarity_engine or momentum_loop or exploration_engine"}"""

    group_text: str = dspy.InputField(
        desc="A single group block prefixed with '--- GROUP_ID: gN | TYPE ---'"
    )
    idea_json: str = dspy.OutputField(
        desc='Single JSON object: {"type": ..., "angle": ..., "format": ...}'
    )


# ---------------------------------------------------------------------------
# Singletons
# ---------------------------------------------------------------------------

evaluate_ideas_batch = dspy.Predict(EvaluateIdeasBatch)
evaluate_single_idea = dspy.Predict(EvaluateSingleIdea)
