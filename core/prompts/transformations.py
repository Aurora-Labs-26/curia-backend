"""
core/prompts/transformations.py
DSPy modules for the 4 ingest-time transformations.

Each transformation extracts a specific kind of primitive from an article:
  - summary        (Tier 1 — always present, plain-text 2-4 sentence summary; UI blurb +
                    selection prompts only — measured inert in the briefing)
  - metadata       (Tier 1 — always present, structured JSON: author, type, tone, etc.
                    Topical categorization lives in source.topics — see core/taxonomy/)
  - core_tensions  (Tier 2 — null if not applicable)
  - counterpoints  (Tier 2 — null if not applicable)

Removed (see "ablation results v1.md" + CHANGELOG):
  - human_stakes — derivable from clean_text by the transcript LLM; never load-bearing
  - examples     — rarely useful, often hallucinated (revamp v1 verdict); unconsumed
  - key_insights — measured inert in the briefing (0.56 alone; zero marginal value on
                   top of tensions+counterpoints) once article_text is present

Each is a separate DSPy Signature so it can be optimized independently
(MIPRO/GEPA target one Signature at a time). The `Transformations` Module
runs all four and returns them as a dict for backward compatibility with
the existing ingest pipeline.

Article text is capped at 50_000 chars at the call site (matches existing
behaviour in core/ingest.py). DSPy does not enforce input length itself.
"""

from __future__ import annotations

import dspy

from core.prompts.loader import with_prompt

# ---------------------------------------------------------------------------
# Signatures
# ---------------------------------------------------------------------------


class ExtractSummary(dspy.Signature):
    """You are summarizing an article for an internal podcast pipeline.

Write a tight 2 to 4 sentence summary of the article. The summary is read by producers
and shown in archive lists — it should communicate what the piece *argues*, not just what topic
it touches.

Requirements:
- 2 to 4 plain sentences
- State the central claim or argument, not just the topic
- Do NOT begin with "This article..." or "The piece..." — write as if briefing a producer directly
- Specific over general — preserve the actual claim, not a generic restatement
- Plain text only, no markdown, no bullets, no headers
- If it's a personal essay or reflection, summarize what it argues for or against
- Never refuse — every article gets a summary"""

    article: str = dspy.InputField(desc="Full article text (capped at 50k chars by caller)")
    summary: str = dspy.OutputField(
        desc="2-4 plain sentences capturing the article's central argument or claim"
    )


class ExtractMetadata(dspy.Signature):
    """You are extracting structured metadata from an article for an internal podcast pipeline.

Read the article and return a JSON object with the fields below. If a field cannot be determined
from the text alone, use null for strings or [] for lists. Do NOT invent facts that aren't supported
by the text.

Required fields:
- "author":        author name(s) if mentioned in the text, else null. String or null.
- "publication":   publication or website name if discernible from the text, else null. String or null.
- "type":          exactly one of:
                   ["essay", "news_article", "research_summary", "opinion",
                    "interview", "review", "personal_reflection", "tutorial", "other"]
- "tone":          exactly one of:
                   ["academic", "journalistic", "conversational", "technical",
                    "literary", "polemical", "dispassionate"]
- "length_bucket": one of "short" (<800 words), "medium" (800-3000 words), "long" (>3000 words)
- "key_entities":  up to 5 named people, organizations, or places that are central to the piece.
                   Empty list if none. List of strings.
- "approx_year":   integer year the piece is grounded in (the year of the events or claims it discusses).
                   Null if unclear. Do NOT use the publication year if events span a different era.
- "language":      ISO 639-1 code: "en", "es", "fr", "de", etc.

Output ONLY a single valid JSON object. No prose, no markdown, no code fences:
{
  "author": "...",
  "publication": "...",
  "type": "essay",
  "tone": "journalistic",
  "length_bucket": "medium",
  "key_entities": ["...", "..."],
  "approx_year": 2024,
  "language": "en"
}"""

    article: str = dspy.InputField(desc="Full article text (capped at 50k chars by caller)")
    metadata_json: str = dspy.OutputField(
        desc="JSON object with author, publication, type, tone, length_bucket, key_entities, approx_year, language"
    )



class ExtractCoreTensions(dspy.Signature):
    """You are extracting material for a single-host audio podcast.

From the article below, extract the central tension or contradiction AND the most important question
it leaves unresolved. These two things together create the structural turn and the closing of an episode.

Requirements:
- One tension: "[Force A] vs [Force B]" followed by one sentence explaining the conflict
- One unresolved question: a direct question the article raises but does not answer
- Plain text only, no markdown, no headers, no bold
- If neither a real tension nor an unresolved question exists, return the single word: null"""

    article: str = dspy.InputField()
    tension: str = dspy.OutputField(
        desc='"Force A vs Force B" + one sentence + one question, OR the literal word "null"'
    )


class ExtractCounterpoints(dspy.Signature):
    """You are extracting material for a single-host audio podcast.

From the article below, extract the strongest counterpoint to the article's main claim — the best
argument against what the article is saying, whether the article raises it or not.

Requirements:
- One counterpoint only, steelmanned as strongly as possible
- One to two plain sentences
- No markdown, no headers, no bold
- If no meaningful counterpoint can be honestly constructed, return the single word: null"""

    article: str = dspy.InputField()
    counterpoint: str = dspy.OutputField(
        desc='One steelmanned counterpoint in 1-2 sentences, OR the literal word "null"'
    )


# ---------------------------------------------------------------------------
# Module — runs all four transformations
# ---------------------------------------------------------------------------


class Transformations(dspy.Module):
    """Runs all 4 ingest transformations on an article and returns them as a dict.

    Backward-compatible interface — returns dict keyed by insight_type with raw string values,
    matching the shape the rest of the codebase expects.
    `metadata` value is a JSON string — callers who need fields parse it.
    """

    def __init__(self):
        super().__init__()
        # Tier 1 (always present)
        self.summary = dspy.Predict(with_prompt(ExtractSummary, "extract_summary"))
        self.metadata = dspy.Predict(with_prompt(ExtractMetadata, "extract_metadata"))
        # Tier 2 (may be "null")
        self.core_tensions = dspy.Predict(with_prompt(ExtractCoreTensions, "extract_core_tensions"))
        self.counterpoints = dspy.Predict(with_prompt(ExtractCounterpoints, "extract_counterpoints"))

    def forward(self, article: str) -> dict[str, str]:
        return {
            "summary":       self.summary(article=article).summary.strip(),
            "metadata":      self.metadata(article=article).metadata_json.strip(),
            "core_tensions": self.core_tensions(article=article).tension.strip(),
            "counterpoints": self.counterpoints(article=article).counterpoint.strip(),
        }

    def run_one(self, article: str, transformation_name: str) -> str:
        """
        Backward-compat helper — run a single transformation by name.
        Drop-in replacement for the old `run_transformation(text, prompt)` shape
        used in core/ingest.py.
        """
        runner = {
            "summary":       lambda: self.summary(article=article).summary,
            "metadata":      lambda: self.metadata(article=article).metadata_json,
            "core_tensions": lambda: self.core_tensions(article=article).tension,
            "counterpoints": lambda: self.counterpoints(article=article).counterpoint,
        }
        if transformation_name not in runner:
            raise ValueError(
                f"Unknown transformation '{transformation_name}'. "
                f"Expected one of: {list(runner)}"
            )
        return runner[transformation_name]().strip()


# Singleton — reuse module across calls (DSPy modules are stateless wrt requests)
transformations = Transformations()


# ---------------------------------------------------------------------------
# Public surface — TRANSFORMATION_NAMES drives the ingest loop in core/ingest.py.
# Order matters only for log output; transformations run in parallel.
# Tier 1 first by convention.
# ---------------------------------------------------------------------------

TRANSFORMATION_NAMES = (
    # Tier 1
    "summary",
    "metadata",
    # Tier 2
    "core_tensions",
    "counterpoints",
)

TIER_1 = ("summary", "metadata")
TIER_2 = ("core_tensions", "counterpoints")
