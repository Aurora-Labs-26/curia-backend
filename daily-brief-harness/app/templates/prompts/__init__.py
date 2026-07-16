# Predefined prompt templates for the Daily Brief generation pipeline.
# These templates act as default starting points and are fully editable in the Test Harness.
#
# Each prompt lives in its own file (one per pipeline step) for editing convenience.
# This __init__ re-exports every constant so existing call sites
# (`from app.templates import prompts` / `prompts.SYSTEM_X_PROMPT`) keep working unchanged.

from app.templates.prompts.score_curate import SYSTEM_SCORE_CURATE_PROMPT, USER_SCORE_CURATE_PROMPT
from app.templates.prompts.article_segment_style_guide import ARTICLE_SEGMENT_STYLE_GUIDE
from app.templates.prompts.article_transcript_lead import SYSTEM_ARTICLE_TRANSCRIPT_LEAD_PROMPT
from app.templates.prompts.article_transcript_standard import SYSTEM_ARTICLE_TRANSCRIPT_STANDARD_PROMPT
from app.templates.prompts.article_transcript_local import SYSTEM_ARTICLE_TRANSCRIPT_LOCAL_PROMPT
from app.templates.prompts.article_segment_user import USER_ARTICLE_SEGMENT_PROMPT, USER_ARTICLE_SEGMENT_REGEN_BLOCK
from app.templates.prompts.intro_outro import SYSTEM_INTRO_OUTRO_PROMPT, USER_INTRO_OUTRO_PROMPT
from app.templates.prompts.relevance_judge import SYSTEM_RELEVANCE_JUDGE_PROMPT, USER_RELEVANCE_JUDGE_PROMPT
from app.templates.prompts.order_correctness_judge import (
    SYSTEM_ORDER_CORRECTNESS_JUDGE_PROMPT,
    USER_ORDER_CORRECTNESS_JUDGE_PROMPT,
)
from app.templates.prompts.faithfulness_judge import (
    FAITHFULNESS_SEVERITY_TAXONOMY,
    SYSTEM_FAITHFULNESS_ARTICLE_JUDGE_PROMPT,
    USER_FAITHFULNESS_ARTICLE_JUDGE_PROMPT,
    SYSTEM_FAITHFULNESS_BOOKEND_JUDGE_PROMPT,
    USER_FAITHFULNESS_BOOKEND_JUDGE_PROMPT,
)
from app.templates.prompts.tone_flow_judge import (
    SYSTEM_TONE_FLOW_PAIRWISE_JUDGE_PROMPT,
    USER_TONE_FLOW_PAIRWISE_JUDGE_PROMPT,
    SYSTEM_BRIEF_COHERENCE_JUDGE_PROMPT,
    USER_BRIEF_COHERENCE_JUDGE_PROMPT,
)
__all__ = [
    "SYSTEM_SCORE_CURATE_PROMPT",
    "USER_SCORE_CURATE_PROMPT",
    "ARTICLE_SEGMENT_STYLE_GUIDE",
    "SYSTEM_ARTICLE_TRANSCRIPT_LEAD_PROMPT",
    "SYSTEM_ARTICLE_TRANSCRIPT_STANDARD_PROMPT",
    "SYSTEM_ARTICLE_TRANSCRIPT_LOCAL_PROMPT",
    "USER_ARTICLE_SEGMENT_PROMPT",
    "USER_ARTICLE_SEGMENT_REGEN_BLOCK",
    "SYSTEM_INTRO_OUTRO_PROMPT",
    "USER_INTRO_OUTRO_PROMPT",
    "SYSTEM_RELEVANCE_JUDGE_PROMPT",
    "USER_RELEVANCE_JUDGE_PROMPT",
    "SYSTEM_ORDER_CORRECTNESS_JUDGE_PROMPT",
    "USER_ORDER_CORRECTNESS_JUDGE_PROMPT",
    "FAITHFULNESS_SEVERITY_TAXONOMY",
    "SYSTEM_FAITHFULNESS_ARTICLE_JUDGE_PROMPT",
    "USER_FAITHFULNESS_ARTICLE_JUDGE_PROMPT",
    "SYSTEM_FAITHFULNESS_BOOKEND_JUDGE_PROMPT",
    "USER_FAITHFULNESS_BOOKEND_JUDGE_PROMPT",
    "SYSTEM_TONE_FLOW_PAIRWISE_JUDGE_PROMPT",
    "USER_TONE_FLOW_PAIRWISE_JUDGE_PROMPT",
    "SYSTEM_BRIEF_COHERENCE_JUDGE_PROMPT",
    "USER_BRIEF_COHERENCE_JUDGE_PROMPT",
]
