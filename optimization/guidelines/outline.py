"""
optimization/guidelines/outline.py
Quality-floor guidelines for outline output.
"""

OUTLINE_GUIDELINES_V1 = """\
QUALITY FLOOR (non-negotiable):
- Output is a valid JSON object with keys: title, thread, segments.
- segments[i] keys are exactly: segment, purpose, primitives_used, transition.
- segment_count from episode_constraints honored exactly.
- structure_pattern from format_config maps directly to segment purposes in order.
- Each primitive (key_insights, human_stakes, etc.) referenced in at most one segment.
- thread is a single declarative sentence — what idea this episode follows.

BANNED:
- Inventing primitives that don't exist in source_primitives.
- Outline segments that include prose body text — outlines are allocations, not scripts.

REQUIRED:
- Final segment respects resolution_style from format_config (open_ended / explicit /
  micro_payoffs / partial).
- Title is concrete and specific, not generic ("On Memory" not "A Discussion").
"""
