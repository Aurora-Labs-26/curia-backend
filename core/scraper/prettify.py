"""
core/scraper/prettify.py
Rule-based text prettifier — makes scraped article text ready for LLM
consumption WITHOUT any LLM, rewriting, or reordering (revamp v1 "Layer 1").

Runs in process_source after scraping, before any transformation. Output is
stored in source.clean_text; the raw full_text is kept untouched for audit.

What it does:
  1. HTML entities        &amp; → &, &quot; → ", double-encoded too
  2. Mojibake             â€™ → ', â€œ → ", â€” → — (curated common cases)
  3. Unicode hygiene      NBSP → space, zero-width chars / soft hyphens dropped
  4. Boilerplate lines    short standalone lines matching web-chrome patterns
                          (subscribe / cookie banner / share / read-more …)
  5. Duplicate paragraphs scraper double-grabs (pull-quotes, sidebars)
  6. Whitespace           collapse space runs and 3+ blank lines

What it never does: summarize, paraphrase, reorder, or add anything. A safety
valve returns the original text unchanged if cleaning removed too much.
"""

from __future__ import annotations

import html
import re
import unicodedata

# ---------------------------------------------------------------------------
# 2. Mojibake — the common UTF-8-read-as-latin1 artifacts. Curated, not
#    exhaustive: only sequences that are unambiguous garbage in real text.
# ---------------------------------------------------------------------------

_MOJIBAKE = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€\x9d": '"',
    "â€”": "—",
    "â€“": "–",
    "â€¦": "…",
    "â€¢": "•",
    "Â·": "·",
    "Â°": "°",
    "Â ": " ",       # stray  before NBSP-mapped spaces
    "Ã©": "é",
    "Ã¨": "è",
    "Ã¡": "á",
    "Ã³": "ó",
    "Ã±": "ñ",
    "Ã¼": "ü",
    "Ã¶": "ö",
    "Ã¤": "ä",
}

# 3. Zero-width and control-ish characters that confuse tokenizers.
_ZERO_WIDTH = re.compile(r"[​‌‍﻿­]")

# ---------------------------------------------------------------------------
# 4. Boilerplate line patterns. A line is dropped ONLY if it is short
#    (≤ _BOILERPLATE_MAX_LEN chars) AND matches — a real sentence that merely
#    mentions "newsletter" survives because prose runs longer than chrome.
# ---------------------------------------------------------------------------

_BOILERPLATE_MAX_LEN = 80

_BOILERPLATE = re.compile(
    r"(?:"
    r"subscribe|sign up|sign in|log ?in|newsletter"
    r"|accept (?:all )?cookies|cookie (?:policy|settings|preferences)|we use cookies"
    r"|privacy policy|terms of (?:service|use)"
    r"|advertisement|sponsored(?: content)?"
    r"|share (?:this|on)|follow us|related (?:articles|posts|stories|coverage)"
    r"|read more|continue reading|click here|learn more"
    r"|all rights reserved|copyright ©?|skip to (?:main )?content"
    r"|loading\.{3}|comments? \(\d+\)|leave a (?:comment|reply)"
    r")",
    re.IGNORECASE,
)

# 5. Only blocks at least this long participate in dedup — short repeated
#    lines (section headers, "***" separators) are legitimate.
_DEDUP_MIN_LEN = 100

# Safety valve: if cleaning removed more than this fraction, distrust it.
_MAX_REMOVAL_RATIO = 0.7


def _fix_encoding(text: str) -> str:
    # Entities, twice: handles the common &amp;quot; double-encoding.
    text = html.unescape(html.unescape(text))
    for bad, good in _MOJIBAKE.items():
        text = text.replace(bad, good)
    text = unicodedata.normalize("NFC", text)
    text = text.replace(" ", " ")          # NBSP
    text = _ZERO_WIDTH.sub("", text)
    return text


def _strip_boilerplate_lines(text: str) -> str:
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) <= _BOILERPLATE_MAX_LEN and _BOILERPLATE.search(stripped):
            continue
        kept.append(line)
    return "\n".join(kept)


def _dedup_paragraphs(text: str) -> str:
    blocks = re.split(r"\n{2,}", text)
    seen: set[str] = set()
    kept = []
    for block in blocks:
        norm = re.sub(r"\s+", " ", block).strip().lower()
        if len(norm) >= _DEDUP_MIN_LEN:
            if norm in seen:
                continue
            seen.add(norm)
        kept.append(block)
    return "\n\n".join(kept)


def _normalize_whitespace(text: str) -> str:
    # per-line: collapse runs of spaces/tabs, strip trailing whitespace
    lines = [re.sub(r"[ \t]+", " ", ln).rstrip() for ln in text.splitlines()]
    text = "\n".join(lines)
    # collapse 3+ blank lines to one blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def prettify(text: str | None) -> str:
    """
    Clean scraped article text for LLM consumption. Deterministic, order- and
    voice-preserving. Never raises; never returns less than 30% of the input
    (safety valve → original text comes back verbatim instead).
    """
    if not text or not text.strip():
        return (text or "").strip()

    original = text
    text = _fix_encoding(text)
    text = _strip_boilerplate_lines(text)
    text = _dedup_paragraphs(text)
    text = _normalize_whitespace(text)

    if len(text) < (1 - _MAX_REMOVAL_RATIO) * len(original.strip()):
        # Cleaning nuked most of the document — the input probably wasn't
        # shaped like we assumed. Don't guess; ship the original.
        return original.strip()
    return text
