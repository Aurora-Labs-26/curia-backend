"""
shows/prompts.py
DEPRECATED — kept only for backward compatibility with anything that still imports
the OUTLINE_PROMPT / TRANSCRIPT_PROMPT constants by name.

The canonical prompt definitions are now DSPy Signatures in:
    core/prompts/outline.py
    core/prompts/transcript.py
    core/prompts/transformations.py
    core/prompts/idea_evaluation.py

The Signature docstrings hold the actual prompt content; DSPy renders them at
call time. The constants below are *not* read by the active pipeline — both
studio/generator.py and intelligence/idea_generator.py use the DSPy modules
directly. Delete this file once you've verified no external scripts import it.
"""

from core.prompts.outline import GenerateOutline
from core.prompts.transcript import GenerateTranscript


# Plain-text mirrors of the active Signature instructions, for any legacy code
# that pulls these as strings. Always equal to the docstring.
OUTLINE_PROMPT: str = (GenerateOutline.__doc__ or "").strip()
TRANSCRIPT_PROMPT: str = (GenerateTranscript.__doc__ or "").strip()
