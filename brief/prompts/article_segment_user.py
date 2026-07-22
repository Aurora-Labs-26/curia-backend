USER_ARTICLE_SEGMENT_PROMPT = """Here is the article this segment is based on:
- Title: {title}
- Source: {source}
- Full text: {full_text}{source_note}{regen_block}

Write the complete spoken segment following the rules above."""

# ---------------------------------------------------------
# Regeneration addendum — spliced in only when a previous draft of this
# segment was fact-checked and flagged (see faithfulness_regen_service.py
# and faithfulness-judge-plan.md section 5). Framed as "fix this correction"
# rather than "write it again from scratch": the previous draft's tone,
# pacing, and length were fine (nothing here asks the model to change
# those), only specific flagged statements need to change, so pushing it
# toward minimal, targeted edits over a full rewrite is what actually keeps
# the VOICE/LENGTH rules above intact across a retry.
# ---------------------------------------------------------

USER_ARTICLE_SEGMENT_REGEN_BLOCK = """

Your previous draft of this segment was fact-checked and flagged:

{flagged_claims}

Previous draft:
\"\"\"
{prior_text}
\"\"\"

Rewrite the segment to fix every flagged statement above, using only what's
actually verifiable from the article text and general knowledge you're
confident in — if a specific fact, number, or quote can't be verified,
either drop it or state the point more generally rather than inventing a
replacement detail to fill the gap. Keep the same voice, pacing, and length
target as the previous draft; this is a correction to specific statements,
not a rewrite from scratch."""
