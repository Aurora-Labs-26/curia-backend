"""
tests/test_tension_connect.py
Tension registry + Connect selection (core/tension/) — snap-or-create, cast
classification/assembly, abstention, and the validate+angle call. Mock-only.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from core.tension import connect as connect_mod
from core.tension import registry as registry_mod
from core.tension.connect import _primary_tier1, find_cast, validate_and_angle
from core.tension.registry import link_source, upsert_tension


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


class TestRegistry:
    async def test_snaps_to_close_existing_tension(self):
        nearest = {"id": "tid-1", "canonical": "x vs y", "score": 0.91}
        with patch.object(registry_mod, "db_fetchrow", AsyncMock(return_value=nearest)), \
             patch.object(registry_mod, "db_execute", AsyncMock()) as ex, \
             patch("core.embeddings.get_embedding_column", return_value="embedding"):
            tid = await upsert_tension("x versus y phrased differently", [0.1] * 4)
        assert tid == "tid-1"
        assert "source_count + 1" in ex.call_args[0][0]

    async def test_creates_when_below_threshold(self):
        far = {"id": "tid-9", "canonical": "unrelated", "score": 0.40}
        created = {"id": "tid-new"}
        fetch = AsyncMock(side_effect=[far, created])
        with patch.object(registry_mod, "db_fetchrow", fetch), \
             patch("core.embeddings.get_embedding_column", return_value="embedding"):
            tid = await upsert_tension("a brand new question vs its opposite", [0.1] * 4)
        assert tid == "tid-new"

    async def test_none_and_empty_rejected(self):
        assert await upsert_tension("none", [0.1]) is None
        assert await upsert_tension("", [0.1]) is None
        assert await upsert_tension("x vs y", []) is None

    async def test_link_normalizes_bad_polarity(self):
        with patch.object(registry_mod, "db_execute", AsyncMock()) as ex:
            await link_source("s1", "t1", "sideways", "shrug")
        params = ex.call_args[0][1]
        assert params["polarity"] == "neutral"
        assert params["confidence"] == "medium"


# ---------------------------------------------------------------------------
# cast classification + assembly
# ---------------------------------------------------------------------------

TOP = lambda t1: json.dumps({"version": 1, "tags": [{"tier1": t1, "tier2": [], "src": "llm"}]})


def _db_for_cast(seed_links, candidates, depth_rows):
    """Wire find_cast's three queries in call order."""
    fetchrow = AsyncMock(return_value={"id": "seed", "title": "Seed", "topics": TOP("Technology & Computing")})
    query = AsyncMock(side_effect=[seed_links, candidates, depth_rows])
    return fetchrow, query


class TestFindCast:
    async def test_roles_classified_and_assembled(self):
        seed_links = [{"tension_id": "T1", "polarity": "side_a", "canonical": "x vs y"}]
        candidates = [
            {"id": "c-ant", "title": "Against", "topics": TOP("Technology & Computing"),
             "created_at": None, "tension_id": "T1", "polarity": "side_b", "confidence": "high"},
            {"id": "c-wild", "title": "Elsewhere", "topics": TOP("Careers"),
             "created_at": None, "tension_id": "T1", "polarity": "side_a", "confidence": "high"},
            {"id": "c-sup", "title": "Agrees", "topics": TOP("Technology & Computing"),
             "created_at": None, "tension_id": "T1", "polarity": "side_a", "confidence": "high"},
        ]
        depth_rows = [{"id": "c-depth", "title": "Same field"}]
        fr, q = _db_for_cast(seed_links, candidates, depth_rows)
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        roles = {e["source_id"]: e["role"] for e in out["cast"]}
        assert out["abstain"] is None
        assert roles["c-ant"] == "antagonist"
        assert roles["c-wild"] == "wildcard"
        assert roles["c-depth"] == "depth"
        assert out["cast"][0]["role"] == "antagonist"      # priority order

    async def test_neutral_seed_treats_sided_docs_as_antagonists(self):
        seed_links = [{"tension_id": "T1", "polarity": "neutral", "canonical": "x vs y"}]
        candidates = [{"id": "c1", "title": "Takes a side", "topics": TOP("Technology & Computing"),
                       "created_at": None, "tension_id": "T1", "polarity": "side_b", "confidence": "high"}]
        fr, q = _db_for_cast(seed_links, candidates, [])
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        assert out["cast"][0]["role"] == "antagonist"

    async def test_low_confidence_never_antagonist(self):
        seed_links = [{"tension_id": "T1", "polarity": "side_a", "canonical": "x vs y"}]
        candidates = [{"id": "c1", "title": "Weak", "topics": TOP("Careers"),
                       "created_at": None, "tension_id": "T1", "polarity": "side_b", "confidence": "low"}]
        fr, q = _db_for_cast(seed_links, candidates, [])
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        # demoted to wildcard (different bucket), not antagonist
        assert all(e["role"] != "antagonist" for e in out["cast"])

    async def test_abstains_without_contrast_or_wildcard(self):
        # only same-bucket supports + depth → abstain
        seed_links = [{"tension_id": "T1", "polarity": "side_a", "canonical": "x vs y"}]
        candidates = [{"id": "c1", "title": "Agrees", "topics": TOP("Technology & Computing"),
                       "created_at": None, "tension_id": "T1", "polarity": "side_a", "confidence": "high"}]
        fr, q = _db_for_cast(seed_links, candidates, [{"id": "d1", "title": "D"}])
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        assert out["abstain"] is not None
        assert out["cast"] == []

    async def test_cap_at_four(self):
        seed_links = [{"tension_id": "T1", "polarity": "side_a", "canonical": "x vs y"}]
        candidates = [
            {"id": f"a{i}", "title": f"A{i}", "topics": TOP("Careers"), "created_at": None,
             "tension_id": "T1", "polarity": "side_b", "confidence": "high"}
            for i in range(6)
        ]
        depth_rows = [{"id": f"d{i}", "title": f"D{i}"} for i in range(4)]
        fr, q = _db_for_cast(seed_links, candidates, depth_rows)
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        assert len(out["cast"]) <= 4

    async def test_seed_without_tensions_abstains(self):
        fr = AsyncMock(return_value={"id": "seed", "title": "Seed", "topics": TOP("Science")})
        q = AsyncMock(side_effect=[[], [], []])
        with patch.object(connect_mod, "db_fetchrow", fr), patch.object(connect_mod, "db_query", q):
            out = await find_cast("seed", "u1")
        assert out["abstain"] is not None


class TestPrimaryTier1:
    def test_dict_and_string_and_garbage(self):
        assert _primary_tier1({"tags": [{"tier1": "Science"}]}) == "Science"
        assert _primary_tier1(json.dumps({"tags": [{"tier1": "Sports"}]})) == "Sports"
        assert _primary_tier1(None) is None
        assert _primary_tier1("not json") is None
        assert _primary_tier1({"tags": []}) is None


# ---------------------------------------------------------------------------
# validate + angle
# ---------------------------------------------------------------------------

CAST = [
    {"source_id": "c1", "title": "Against", "role": "antagonist", "tension": "x vs y"},
    {"source_id": "c2", "title": "Elsewhere", "role": "wildcard", "tension": "x vs y"},
]
INFO_ROWS = [
    {"id": "seed", "title": "Seed", "summary": "seed says X"},
    {"id": "c1", "title": "Against", "summary": "argues not-X"},
    {"id": "c2", "title": "Elsewhere", "summary": "same fight, other field"},
]


class TestValidateAndAngle:
    async def test_rejection_pruned_and_angle_returned(self):
        verdict = json.dumps({"rejected": [2], "angle": "Seed says X; Against disagrees."})
        with patch.object(connect_mod, "db_query", AsyncMock(return_value=INFO_ROWS)), \
             patch.object(connect_mod, "_call_angle", return_value=verdict):
            kept, angle = await validate_and_angle("seed", CAST)
        assert [e["source_id"] for e in kept] == ["c1"]
        assert "disagrees" in angle

    async def test_garbage_output_keeps_cast_with_template_angle(self):
        with patch.object(connect_mod, "db_query", AsyncMock(return_value=INFO_ROWS)), \
             patch.object(connect_mod, "_call_angle", return_value="not json"):
            kept, angle = await validate_and_angle("seed", CAST)
        assert kept == CAST
        assert "Seed" in angle

    async def test_never_strips_below_viability(self):
        # LLM tries to reject BOTH contrast-bearing companions → keep original cast
        verdict = json.dumps({"rejected": [1, 2], "angle": "empty show"})
        with patch.object(connect_mod, "db_query", AsyncMock(return_value=INFO_ROWS)), \
             patch.object(connect_mod, "_call_angle", return_value=verdict):
            kept, _ = await validate_and_angle("seed", CAST)
        assert kept == CAST

    async def test_llm_exception_falls_back(self):
        with patch.object(connect_mod, "db_query", AsyncMock(return_value=INFO_ROWS)), \
             patch.object(connect_mod, "_call_angle", side_effect=RuntimeError("api down")):
            kept, angle = await validate_and_angle("seed", CAST)
        assert kept == CAST and angle
