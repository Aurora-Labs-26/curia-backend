"""
optimization/guidelines/transcript.py
Quality-floor guidelines for transcript output. Every transcript must satisfy these
regardless of user preferences. Owned by the team; bump GUIDELINES_VERSION when changed.
"""

TRANSCRIPT_GUIDELINES_V1 = """\
QUALITY FLOOR (non-negotiable):
- Every claim must be grounded in the briefing's source primitives. No invention.
- Format adherence: format_config.rules.must_do and must_avoid honored on every line.
- Speaker voice: the speech_patterns block applied subtly throughout — don't mimic, suggest.
- Outline fidelity: each transcript line traces to a segment in the outline.
- Output is a valid JSON array of {speaker, text}.
- Length bounds: 6 to 80 lines.

BANNED:
- Meta-commentary about being a podcast or AI-generated.
- Direct address of "you" the listener except as the format permits.
- Sponsor-style energy or hype language.
- Generic transitions ("now let's talk about...", "moving on...").
- Neat rhetorical symmetry — avoid sentences that wrap up too cleanly.

REQUIRED:
- Sentences vary in length so the rhythm doesn't become predictable.
- Fragments are allowed when they improve cadence.
- Source boundaries are not audible — never transition between sources explicitly.
"""
