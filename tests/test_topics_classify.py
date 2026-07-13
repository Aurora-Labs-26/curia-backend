"""
tests/test_topics_classify.py
Unit tests for core/taxonomy/ — the topics bucketing system ("topics v1.md").

All mock-only: the LLM judge is patched at the `_call_judge` seam, so no config,
API key, or network is needed.
"""

import json
from unittest.mock import patch

import pytest

from core.taxonomy import buckets, classify
from core.taxonomy.buckets import (
    SECTION_SLUGS,
    SEED_DOMAIN_PINS,
    TAXONOMY,
    TIER1_BY_ID,
    TIER1_ID,
    TIER2_BY_ID,
    is_valid,
)
from core.taxonomy.classify import (
    ENVELOPE_VERSION,
    _merge,
    _parse_wire,
    classify_source,
    resolve_pins,
)


# ---------------------------------------------------------------------------
# Taxonomy integrity
# ---------------------------------------------------------------------------


class TestTaxonomyIntegrity:
    def test_pets_excluded(self):
        assert "Pets" not in TAXONOMY

    def test_32_tier1_categories(self):
        assert len(TAXONOMY) == 32  # 28 IAB (minus Pets) + 4 custom

    def test_exactly_4_custom(self):
        customs = [t1 for t1, spec in TAXONOMY.items() if spec["custom"]]
        assert sorted(customs) == [
            "History", "Media & Journalism", "Philosophy", "Psychology & Self",
        ]

    def test_tier1_ids_are_1_to_32_and_bijective(self):
        assert sorted(TIER1_BY_ID) == list(range(1, 33))
        assert {TIER1_ID[name] for name in TAXONOMY} == set(range(1, 33))

    def test_tier2_ids_resolve_to_their_tier1(self):
        for t2_id, (t1, t2) in TIER2_BY_ID.items():
            major = int(t2_id.split(".")[0])
            assert TIER1_BY_ID[major] == t1
            assert t2 in TAXONOMY[t1]["tier2"]

    def test_is_valid(self):
        assert is_valid("Sports")
        assert is_valid("Sports", "Soccer")
        assert not is_valid("Sports", "Artificial Intelligence")
        assert not is_valid("Nonsense")

    def test_seed_pins_and_slugs_reference_real_labels(self):
        # (import-time asserts already guarantee this; keep as regression guard)
        for t1, t2 in SEED_DOMAIN_PINS.values():
            assert is_valid(t1, t2)
        for t1, t2 in SECTION_SLUGS.values():
            assert is_valid(t1, t2)


# ---------------------------------------------------------------------------
# resolve_pins
# ---------------------------------------------------------------------------


class TestResolvePins:
    def test_domain_pin(self):
        pins = resolve_pins("https://www.espn.com/story/12345")
        assert pins == [{"tier1": "Sports", "tier2": [], "src": "domain"}]

    def test_unknown_host_no_pins(self):
        assert resolve_pins("https://example.com/some-essay") == []

    def test_section_pin(self):
        pins = resolve_pins("https://www.theguardian.com/sport/2026/jul/01/final")
        assert pins == [{"tier1": "Sports", "tier2": [], "src": "section"}]

    def test_section_beats_domain_on_conflict(self):
        # espn.com domain says Sports, but /politics/ section says News and Politics
        pins = resolve_pins("https://espn.com/politics/some-story")
        assert len(pins) == 1
        assert pins[0]["tier1"] == "News and Politics"
        assert pins[0]["src"] == "section"

    def test_section_and_domain_agree_merge_tier2(self):
        # espn.com (Sports) + /soccer/ (Sports > Soccer) → complete pin
        pins = resolve_pins("https://espn.com/soccer/match-report")
        assert pins == [{"tier1": "Sports", "tier2": ["Soccer"], "src": "section"}]

    def test_learned_pin_used_when_host_not_seeded(self):
        pins = resolve_pins(
            "https://astralcodexten.substack.com/p/essay",
            learned_pin=("Philosophy", None),
        )
        assert pins == [{"tier1": "Philosophy", "tier2": [], "src": "learned"}]

    def test_invalid_learned_pin_ignored(self):
        assert resolve_pins("https://example.com/x", learned_pin=("Bogus", None)) == []

    def test_youtube_ignores_url_uses_channel_pin(self):
        pins = resolve_pins(
            "https://www.youtube.com/watch?v=abc12345678",
            source_type="youtube",
            learned_pin=("Science", None),
        )
        assert pins == [{"tier1": "Science", "tier2": [], "src": "learned"}]

    def test_youtube_without_pin_is_empty(self):
        assert resolve_pins(
            "https://youtu.be/abc12345678", source_type="youtube",
        ) == []


# ---------------------------------------------------------------------------
# _parse_wire (ID-coded judge output → validated labels)
# ---------------------------------------------------------------------------


class TestParseWire:
    def test_valid_output(self):
        sports, soccer_id = TIER1_ID["Sports"], None
        for t2_id, (t1, t2) in TIER2_BY_ID.items():
            if (t1, t2) == ("Sports", "Soccer"):
                soccer_id = t2_id
        tags = _parse_wire(json.dumps([{"t1": sports, "t2": [soccer_id]}]))
        assert tags == [{"tier1": "Sports", "tier2": ["Soccer"]}]

    def test_strips_code_fences(self):
        tags = _parse_wire('```json\n[{"t1": %d, "t2": []}]\n```' % TIER1_ID["Science"])
        assert tags == [{"tier1": "Science", "tier2": []}]

    def test_unknown_tier1_id_dropped(self):
        assert _parse_wire('[{"t1": 999, "t2": []}]') == []

    def test_tier2_from_wrong_tier1_dropped(self):
        # attach an AI tier2 id to Sports → tier2 dropped, tier1 kept
        ai_id = next(k for k, v in TIER2_BY_ID.items()
                     if v == ("Technology & Computing", "Artificial Intelligence"))
        tags = _parse_wire(json.dumps([{"t1": TIER1_ID["Sports"], "t2": [ai_id]}]))
        assert tags == [{"tier1": "Sports", "tier2": []}]

    def test_tier2_capped_at_2(self):
        sports = TIER1_ID["Sports"]
        three = [k for k, v in TIER2_BY_ID.items() if v[0] == "Sports"][:3]
        tags = _parse_wire(json.dumps([{"t1": sports, "t2": three}]))
        assert len(tags[0]["tier2"]) == 2

    def test_empty_array_is_valid(self):
        assert _parse_wire("[]") == []

    def test_non_array_raises(self):
        with pytest.raises(ValueError):
            _parse_wire('{"t1": 1}')


# ---------------------------------------------------------------------------
# _merge (pins unconditional, LLM fills remaining slots)
# ---------------------------------------------------------------------------


class TestMerge:
    def test_llm_fills_after_pins(self):
        pins = [{"tier1": "Sports", "tier2": [], "src": "domain"}]
        llm = [{"tier1": "Business and Finance", "tier2": ["Business"]}]
        merged = _merge(pins, llm)
        assert merged[0] == {"tier1": "Sports", "tier2": [], "src": "domain"}
        assert merged[1] == {"tier1": "Business and Finance", "tier2": ["Business"], "src": "llm"}

    def test_llm_duplicate_of_pin_contributes_tier2_keeps_pin_src(self):
        pins = [{"tier1": "Sports", "tier2": [], "src": "domain"}]
        llm = [{"tier1": "Sports", "tier2": ["Basketball"]}]
        merged = _merge(pins, llm)
        assert merged == [{"tier1": "Sports", "tier2": ["Basketball"], "src": "domain"}]

    def test_total_tier1_capped_at_3(self):
        pins = [{"tier1": "Sports", "tier2": [], "src": "domain"}]
        llm = [
            {"tier1": "Science", "tier2": []},
            {"tier1": "History", "tier2": []},
            {"tier1": "Movies", "tier2": []},   # 4th — dropped
        ]
        merged = _merge(pins, llm)
        assert len(merged) == 3
        assert [t["tier1"] for t in merged] == ["Sports", "Science", "History"]


# ---------------------------------------------------------------------------
# classify_source (terminal states)
# ---------------------------------------------------------------------------


def _wire(*pairs):
    """Build valid ID-coded judge output from (tier1_label, [tier2_labels])."""
    out = []
    for t1, t2s in pairs:
        t2_ids = [k for k, v in TIER2_BY_ID.items() if v[0] == t1 and v[1] in t2s]
        out.append({"t1": TIER1_ID[t1], "t2": t2_ids})
    return json.dumps(out)


class TestClassifySource:
    def test_complete_pin_skips_llm(self):
        def boom(*a, **k):
            raise AssertionError("LLM must not be called for a complete pin")
        with patch.object(classify, "_call_judge", boom):
            env = classify_source("https://espn.com/soccer/report", "t", "x")
        assert env == {
            "version": ENVELOPE_VERSION,
            "tags": [{"tier1": "Sports", "tier2": ["Soccer"], "src": "section"}],
        }

    def test_no_pins_llm_decides(self):
        with patch.object(classify, "_call_judge",
                          return_value=_wire(("Philosophy", ["Metaphysics"]))):
            env = classify_source("https://example.com/essay", "On Being", "text")
        assert env["tags"] == [
            {"tier1": "Philosophy", "tier2": ["Metaphysics"], "src": "llm"},
        ]

    def test_partial_pin_llm_adds_alongside(self):
        with patch.object(classify, "_call_judge",
                          return_value=_wire(("Sports", ["Basketball"]),
                                             ("Business and Finance", ["Business"]))):
            env = classify_source("https://espn.com/story/nba-tv-deal", "NBA TV", "x")
        assert env["tags"][0] == {"tier1": "Sports", "tier2": ["Basketball"], "src": "domain"}
        assert env["tags"][1] == {"tier1": "Business and Finance", "tier2": ["Business"], "src": "llm"}

    def test_valid_empty_is_kept(self):
        with patch.object(classify, "_call_judge", return_value="[]"):
            env = classify_source("https://example.com/x", "t", "x")
        assert env == {"version": ENVELOPE_VERSION, "tags": []}

    def test_llm_error_with_pins_returns_pins(self):
        with patch.object(classify, "_call_judge", side_effect=RuntimeError("api down")):
            env = classify_source("https://espn.com/story/1", "t", "x")
        assert env["tags"] == [{"tier1": "Sports", "tier2": [], "src": "domain"}]

    def test_llm_error_without_pins_returns_none(self):
        with patch.object(classify, "_call_judge", side_effect=RuntimeError("api down")):
            assert classify_source("https://example.com/x", "t", "x") is None

    def test_llm_garbage_without_pins_returns_none(self):
        with patch.object(classify, "_call_judge", return_value="not json at all"):
            assert classify_source("https://example.com/x", "t", "x") is None

    def test_youtube_channel_feeds_title_signal(self):
        seen = {}
        def spy(url, title, text, pinned):
            seen.update(url=url, title=title)
            return "[]"
        with patch.object(classify, "_call_judge", spy):
            classify_source(
                "https://youtu.be/abc12345678", "Great Talk", "x",
                source_type="youtube", channel="Veritasium",
            )
        assert seen["url"] == ""                    # YT URL carries no topic signal
        assert "Veritasium" in seen["title"]

    def test_envelope_always_versioned(self):
        with patch.object(classify, "_call_judge", return_value="[]"):
            env = classify_source("https://example.com/x", "t", "x")
        assert env["version"] == ENVELOPE_VERSION
