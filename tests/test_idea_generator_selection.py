"""
tests/test_idea_generator_selection.py
Post-clustering-removal behavior of intelligence/idea_generator.py:
cluster_sources yields singletons; eligibility gates on summary.
"""

import pytest

from intelligence.idea_generator import cluster_sources, has_complete_insights


class TestSingletonClusters:
    async def test_every_source_is_standalone(self):
        state = {"user_id": "u1", "sources": [
            {"id": "a", "title": "A", "insights": {}},
            {"id": "b", "title": "B", "insights": {}},
            {"id": "c", "title": "C", "insights": {}},
        ]}
        out = await cluster_sources(state)
        assert out["clusters"] == [["a"], ["b"], ["c"]]

    async def test_empty_archive(self):
        out = await cluster_sources({"user_id": "u1", "sources": []})
        assert out["clusters"] == []

    async def test_no_db_access_needed(self):
        # the removed clique builder hit source_primitive_embedding +
        # source_similarity; the singleton version must be pure
        state = {"user_id": "u1", "sources": [{"id": "x", "title": "X", "insights": {}}]}
        out = await cluster_sources(state)   # would raise if it touched the DB
        assert out["clusters"] == [["x"]]


class TestEligibilityGate:
    def test_summary_present_passes(self):
        assert has_complete_insights({"insights": {"summary": "an argument"}}) is True

    def test_no_summary_fails(self):
        assert has_complete_insights({"insights": {"core_tensions": "A vs B"}}) is False

    def test_removed_key_insights_no_longer_gates(self):
        # pre-2026-07-20 rows may carry key_insights but no summary is required
        assert has_complete_insights({"insights": {"key_insights": "old row"}}) is False

    def test_empty_insights_fails(self):
        assert has_complete_insights({"insights": {}}) is False
        assert has_complete_insights({}) is False
