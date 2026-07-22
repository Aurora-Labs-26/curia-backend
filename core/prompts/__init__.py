"""
core/prompts
============

Every LLM interaction in Curia is defined here as a DSPy Signature + Module.

Why this exists:
  - Single source of truth for what the model is asked to do
  - Prompt content lives in Signature docstrings — readable, versioned, optimizable
  - Each module is GEPA / MIPRO compatible without further refactor
  - Optimized prompt artifacts (post-Phase) load by replacing the module:
        transformations.core_tensions.load("prompts/optimized/core_tensions_v1.json")

Layout:
  transformations.py    3 ingest extractions (summary, metadata, stance)
  idea_evaluation.py    batch + per-group fallback for show idea generation
  outline.py            episode outline generation (Haiku)
  transcript.py         episode transcript generation (Sonnet)

Importing this package configures DSPy globally — see core/dspy_setup.py.
"""

# Side-effect: configure DSPy on import.
from core import dspy_setup  # noqa: F401

from .transformations import (
    ExtractSummary,
    ExtractMetadata,
    ExtractStanceCard,
    Transformations,
    transformations,
    TRANSFORMATION_NAMES,
    TIER_1,
    TIER_2,
)
from .idea_evaluation import (
    EvaluateIdeasBatch,
    EvaluateSingleIdea,
    evaluate_ideas_batch,
    evaluate_single_idea,
)
from .outline import GenerateOutline, generate_outline
from .transcript import GenerateTranscript, generate_transcript

__all__ = [
    # transformations
    "ExtractSummary",
    "ExtractMetadata",
    "ExtractStanceCard",
    "Transformations",
    "transformations",
    "TRANSFORMATION_NAMES",
    "TIER_1",
    "TIER_2",
    # idea evaluation
    "EvaluateIdeasBatch",
    "EvaluateSingleIdea",
    "evaluate_ideas_batch",
    "evaluate_single_idea",
    # outline
    "GenerateOutline",
    "generate_outline",
    # transcript
    "GenerateTranscript",
    "generate_transcript",
]
