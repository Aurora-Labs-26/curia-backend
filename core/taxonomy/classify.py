"""
core/taxonomy/classify.py
Topic classification: deterministic pins + one LLM judge (see "topics v1.md").

Flow per source:
  1. resolve_pins()  — domain / section / learned pins from publisher-declared
                       structure (hostname, URL path segment, YT channel). No
                       content matching of any kind.
  2. If pins fully specify Tier-1 AND Tier-2 → done, LLM skipped.
  3. Otherwise one LLM call (binding `classify.topics`): taxonomy(IDs) + raw
     evidence + pins-as-constraints. Output is ID-coded JSON, hard-validated.
  4. Terminal states:  valid tags → envelope · valid EMPTY → {"tags": []} kept ·
     LLM error → pins if any, else None (leave column NULL → natural retry).

classify_source() never raises. It is synchronous (DSPy) — call via
run_in_executor from async pipelines, like the other transformations.
"""

from __future__ import annotations

import json
import re
from typing import Optional
from urllib.parse import urlparse

import dspy
from loguru import logger

from core.prompts.loader import with_prompt
from .buckets import (
    SECTION_SLUGS,
    SEED_DOMAIN_PINS,
    TAXONOMY_PROMPT,
    TIER1_BY_ID,
    TIER1_ID,
    TIER2_BY_ID,
    is_valid,
)

ENVELOPE_VERSION = 1
MAX_TIER1 = 3
MAX_TIER2 = 2


# ---------------------------------------------------------------------------
# Layer 1 — pins (deterministic, publisher-declared structure only)
# ---------------------------------------------------------------------------


def _norm_host(url: str | None) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    return host.removeprefix("www.")


def resolve_pins(
    url: str | None,
    source_type: str = "article",
    learned_pin: tuple[str, str | None] | None = None,
) -> list[dict]:
    """
    Returns 0 or 1 pinned tag: {"tier1", "tier2": [...], "src"}.

    - YouTube: the URL is topically meaningless — only a learned channel pin
      (resolved by the caller from domain_pins via "yt:<channel>") applies.
    - Article: seed domain pin, else learned domain pin; section slug in the
      URL path. Section beats domain on Tier-1 conflict; on agreement they
      merge (section's Tier-2 wins).
    """
    if source_type == "youtube":
        if learned_pin and is_valid(learned_pin[0], learned_pin[1]):
            return [_pin_tag(learned_pin, "learned")]
        return []

    domain: tuple[str, str | None] | None = SEED_DOMAIN_PINS.get(_norm_host(url))
    domain_src = "domain"
    if domain is None and learned_pin and is_valid(learned_pin[0], learned_pin[1]):
        domain, domain_src = learned_pin, "learned"

    section: tuple[str, str | None] | None = None
    for seg in (urlparse(url or "").path or "").lower().split("/"):
        if seg in SECTION_SLUGS:
            section = SECTION_SLUGS[seg]
            break

    if section and domain:
        if section[0] == domain[0]:
            merged = (section[0], section[1] or domain[1])
            return [_pin_tag(merged, "section")]
        return [_pin_tag(section, "section")]  # per-article beats site-level
    if section:
        return [_pin_tag(section, "section")]
    if domain:
        return [_pin_tag(domain, domain_src)]
    return []


def _pin_tag(pin: tuple[str, str | None], src: str) -> dict:
    tier1, tier2 = pin
    return {"tier1": tier1, "tier2": [tier2] if tier2 else [], "src": src}


def _pins_complete(pins: list[dict]) -> bool:
    return bool(pins) and all(p["tier2"] for p in pins)


# ---------------------------------------------------------------------------
# Layer 2 — LLM judge
# ---------------------------------------------------------------------------


class JudgeTopics(dspy.Signature):
    """You assign topic buckets to an article for a podcast pipeline.

You get a numbered taxonomy (Tier-1 categories, each with Tier-2 subcategories),
the article's URL, title, and a text excerpt, and possibly some CONFIRMED tags
(derived from the publisher's own site structure — do not remove or contradict
them; assign their Tier-2 if the evidence makes it clear).

Rules:
- Choose at most 3 Tier-1 categories total (including confirmed ones), ordered
  most to least relevant. For each, at most 2 Tier-2 subcategories that belong
  to that Tier-1. Use an empty list when no Tier-2 clearly fits.
- Precision over coverage: only tag what the article is genuinely about. One
  strong Tier-1 beats three weak ones. If nothing in the taxonomy fits, return
  an empty array — that is a correct answer, do not force-fit.
- Output ONLY a JSON array using numeric IDs from the taxonomy. No prose, no
  code fences.

Examples of correct output shape:
  [{"t1": 25, "t2": ["25.1"]}]
  [{"t1": 23, "t2": ["23.33"]}, {"t1": 3, "t2": []}]
  []"""

    taxonomy: str = dspy.InputField(desc="Numbered Tier-1/Tier-2 taxonomy, one Tier-1 per line")
    url: str = dspy.InputField()
    title: str = dspy.InputField()
    text: str = dspy.InputField(desc="Summary or leading excerpt of the article")
    pinned: str = dspy.InputField(desc='Confirmed tags from site structure, or "none"')
    tags_json: str = dspy.OutputField(desc='JSON array like [{"t1": 23, "t2": ["23.33"]}] or []')


_judge = dspy.Predict(with_prompt(JudgeTopics, "classify_topics"))


def _call_judge(url: str, title: str, text: str, pinned: str) -> str:
    """Isolated LLM invocation — the seam tests patch."""
    from core.llm_config import resolve

    with dspy.context(lm=resolve.llm("classify.topics")):
        return _judge(
            taxonomy=TAXONOMY_PROMPT, url=url, title=title, text=text, pinned=pinned,
        ).tags_json


def _render_pinned(pins: list[dict]) -> str:
    if not pins:
        return "none"
    parts = []
    for p in pins:
        t1_id = TIER1_ID[p["tier1"]]
        sub = f" > {p['tier2'][0]}" if p["tier2"] else ""
        parts.append(f"{p['tier1']} (id {t1_id}){sub}")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Validation + merge
# ---------------------------------------------------------------------------


def _parse_wire(raw: str) -> list[dict]:
    """ID-coded judge output → [{"tier1", "tier2": [...]}], taxonomy-valid only."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", (raw or "").strip(), flags=re.MULTILINE).strip()
    data = json.loads(cleaned)
    if not isinstance(data, list):
        raise ValueError(f"judge output is not a JSON array: {type(data).__name__}")

    tags: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        try:
            t1_id = int(entry.get("t1"))
        except (TypeError, ValueError):
            continue
        tier1 = TIER1_BY_ID.get(t1_id)
        if not tier1:
            continue
        tier2: list[str] = []
        for t2_id in entry.get("t2") or []:
            resolved = TIER2_BY_ID.get(str(t2_id))
            if resolved and resolved[0] == tier1 and resolved[1] not in tier2:
                tier2.append(resolved[1])
        tags.append({"tier1": tier1, "tier2": tier2[:MAX_TIER2]})
    return tags


def _merge(pins: list[dict], llm_tags: list[dict]) -> list[dict]:
    """Pins first (unconditional), LLM tags fill the remaining Tier-1 slots.
    An LLM tag matching a pinned Tier-1 contributes its Tier-2s to the pin."""
    result = [dict(p, tier2=list(p["tier2"])[:MAX_TIER2]) for p in pins]
    by_tier1 = {t["tier1"]: t for t in result}
    for tag in llm_tags:
        existing = by_tier1.get(tag["tier1"])
        if existing is not None:
            for t2 in tag["tier2"]:
                if t2 not in existing["tier2"] and len(existing["tier2"]) < MAX_TIER2:
                    existing["tier2"].append(t2)
            continue
        if len(result) >= MAX_TIER1:
            continue
        new = {"tier1": tag["tier1"], "tier2": tag["tier2"], "src": "llm"}
        result.append(new)
        by_tier1[tag["tier1"]] = new
    return result


def _envelope(tags: list[dict]) -> dict:
    return {"version": ENVELOPE_VERSION, "tags": tags}


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def classify_source(
    url: str | None,
    title: str | None,
    text: str | None,
    source_type: str = "article",
    channel: str | None = None,
    learned_pin: tuple[str, str | None] | None = None,
) -> Optional[dict]:
    """
    Full classification. Returns the topics envelope dict, or None meaning
    "leave the column NULL" (LLM failed and there were no pins — retry later).
    Never raises.

    `channel` is informational for YouTube sources (appended to the title
    signal); the learned channel pin itself arrives via `learned_pin`,
    resolved by the caller from the domain_pins table ("yt:<channel>").
    """
    pins = resolve_pins(url, source_type=source_type, learned_pin=learned_pin)
    if _pins_complete(pins):
        return _envelope(pins)

    title_signal = (title or "").strip()
    if source_type == "youtube" and channel:
        title_signal = f"{title_signal} — channel: {channel}".strip(" —")

    try:
        raw = _call_judge(
            url="" if source_type == "youtube" else (url or ""),
            title=title_signal,
            text=(text or "")[:2000],
            pinned=_render_pinned(pins),
        )
        llm_tags = _parse_wire(raw)
        return _envelope(_merge(pins, llm_tags))
    except Exception as e:
        logger.warning(f"[topics] judge failed ({e}); pins={len(pins)}")
        return _envelope(pins) if pins else None
