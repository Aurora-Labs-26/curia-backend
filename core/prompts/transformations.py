"""
core/prompts/transformations.py
DSPy modules for the 3 ingest-time transformations.

Each transformation extracts a specific kind of primitive from an article:
  - summary        (Tier 1 — always present, plain-text 2-4 sentence summary; UI blurb +
                    selection prompts only — measured inert in the briefing)
  - metadata       (Tier 1 — always present, structured JSON: author, type, tone, etc.
                    Topical categorization lives in source.topics — see core/taxonomy/)
  - stance         (Tier 2 — JSON stance card: canonical domain-free tension +
                    polarity + confidence + domain phrasing + counterpoint.
                    Ingest derives legacy core_tensions/counterpoints insight
                    rows from it and links the tension registry — see
                    core/ingest.py + core/tension/)

Removed (see "ablation results v1.md" + CHANGELOG):
  - human_stakes — derivable from clean_text by the transcript LLM; never load-bearing
  - examples     — rarely useful, often hallucinated (revamp v1 verdict); unconsumed
  - key_insights — measured inert in the briefing (0.56 alone; zero marginal value on
                   top of tensions+counterpoints) once article_text is present

Each is a separate DSPy Signature so it can be optimized independently
(MIPRO/GEPA target one Signature at a time). The `Transformations` Module
runs all three and returns them as a dict for backward compatibility with
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



class ExtractStanceCard(dspy.Signature):
    """Extract the article's STANCE CARD for a cross-domain matching system
(validated on 50 prod sources — see "stance card samples v1.md").

A TENSION is the contested question this article participates in — the
disagreement that would exist even if this article were never written. It is
NOT the topic and NOT the author's claim.

Rules for canonical_tension:
- Format "X vs Y" plus at most one clarifying clause
- MUST be domain-free: no names of fields, products, companies, technologies,
  sports, industries, or people. "automation of entry-level work vs the
  pipeline that creates mastery" — never "AI coding tools vs junior developers"
- Test: could this exact phrasing describe an article in a completely
  different field? If not, abstract further.
- If the article genuinely has no contested question (pure how-to, plain news
  report, product announcement with no argument), use tension "none"

Other fields:
- domain_phrasing: the same tension in the article's own domain terms,
  "[Force A] vs [Force B]" plus one sentence — this feeds the episode briefing
- polarity: which side the AUTHOR lands on — "side_a" (first side of your
  X vs Y), "side_b", or "neutral" (explores without taking a side)
- confidence: "high" | "medium" | "low" — how clearly the article carries it
- counterpoint: the strongest steelmanned argument AGAINST the article's main
  claim (1-2 sentences), whether or not the article raises it; "none" if no
  honest counterpoint exists

Output ONLY JSON, no prose, no code fences:
{"canonical_tension": "...", "domain_phrasing": "...",
 "polarity": "side_a|side_b|neutral", "confidence": "high|medium|low",
 "counterpoint": "..."}"""

    article: str = dspy.InputField(desc="Full article text (capped at 50k chars by caller)")
    card_json: str = dspy.OutputField(desc="JSON stance card")


# ---------------------------------------------------------------------------
# Module — runs all three transformations
# ---------------------------------------------------------------------------


class Transformations(dspy.Module):
    """Runs all 3 ingest transformations on an article and returns them as a dict.

    Backward-compatible interface — returns dict keyed by insight_type with raw string values,
    matching the shape the rest of the codebase expects.
    `metadata` value is a JSON string — callers who need fields parse it.
    """

    def __init__(self):
        super().__init__()
        # Tier 1 (always present)
        self.summary = dspy.Predict(with_prompt(ExtractSummary, "extract_summary"))
        self.metadata = dspy.Predict(with_prompt(ExtractMetadata, "extract_metadata"))
        # Tier 2 (may be "null"-bearing JSON)
        self.stance = dspy.Predict(with_prompt(ExtractStanceCard, "extract_stance"))

    def forward(self, article: str) -> dict[str, str]:
        return {
            "summary":       self.summary(article=article).summary.strip(),
            "metadata":      self.metadata(article=article).metadata_json.strip(),
            "stance":        self.stance(article=article).card_json.strip(),
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
            "stance":        lambda: self.stance(article=article).card_json,
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
    "stance",
)

TIER_1 = ("summary", "metadata")
TIER_2 = ("stance",)
