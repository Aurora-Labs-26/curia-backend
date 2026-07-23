"""
tests/test_brief_scoring.py — TDD for brief/scoring.py after the port from
MiniLM/torch onto core.embeddings. Embeddings are patched with deterministic
vectors so clustering/ranking behavior is asserted for real, offline.
"""

import numpy as np
from unittest.mock import AsyncMock, patch

import pytest

from brief import scoring


def _fake_embedder(vector_map):
    """texts → stacked vectors by exact-prefix lookup (default = unique axis)."""
    async def fake(texts):
        out = []
        for i, t in enumerate(texts):
            for prefix, vec in vector_map.items():
                if t.startswith(prefix):
                    out.append(vec); break
            else:
                v = [0.0] * 4; v[i % 4] = 1.0
                out.append(v)
        return np.array(out, dtype=float)
    return fake


def art(title, topic="Tech", url=None, desc=""):
    return {"title": title, "description": desc, "source": "src",
            "url": url or f"https://x.com/{title.replace(' ', '-')}",
            "topic": topic, "published_date": "2026-07-23"}


class TestCos:
    def test_identical_vectors_are_1(self):
        m = scoring._cos([[1, 0], [0, 1]], [[1, 0], [0, 1]])
        assert m[0][0] == pytest.approx(1.0)
        assert m[0][1] == pytest.approx(0.0)

    def test_zero_vector_safe(self):
        m = scoring._cos([[0, 0]], [[1, 0]])
        assert m[0][0] == pytest.approx(0.0)


class TestRankArticles:
    async def test_same_story_clusters_to_one_representative(self):
        # two near-identical vectors → one cluster; third orthogonal survives
        vm = {"Fed cuts rates": [1, 0, 0, 0], "Fed slashes rates": [0.99, 0.14, 0, 0],
              "New whale species": [0, 0, 1, 0]}
        anchors = {"Business": np.array([[1, 0, 0, 0]]), "Science & Health": np.array([[0, 0, 1, 0]])}
        with patch.object(scoring, "_embed_texts", _fake_embedder(vm)), \
             patch.object(scoring, "_get_topic_embeddings", AsyncMock(return_value=anchors)):
            result = await scoring.rank_articles(
                [art("Fed cuts rates"), art("Fed slashes rates"), art("New whale species")],
                interests=["Business", "Science & Health"], keep_fraction=1.0,
            )
        ranked = result["ranked"]
        titles = [r["title"] for r in ranked]
        assert len(ranked) == 2                       # dupes collapsed
        assert any(t.startswith("Fed") for t in titles)
        fed = next(r for r in ranked if r["title"].startswith("Fed"))
        assert fed["cluster_size"] == 2
        assert fed["cluster_alternates"], "alternate outlet URL should be retained"

    async def test_interest_gating_scores_by_matching_anchor(self):
        vm = {"Fed cuts rates": [1, 0, 0, 0]}
        anchors = {"Business": np.array([[1, 0, 0, 0]]), "Sports": np.array([[0, 1, 0, 0]])}
        with patch.object(scoring, "_embed_texts", _fake_embedder(vm)), \
             patch.object(scoring, "_get_topic_embeddings", AsyncMock(return_value=anchors)):
            result = await scoring.rank_articles(
                [art("Fed cuts rates")], interests=["Business"], keep_fraction=1.0)
        assert result["ranked"][0]["scores"]["topicSimilarity"] == pytest.approx(10.0)

    async def test_local_articles_always_kept(self):
        vm = {}
        anchors = {"Tech": np.array([[1, 0, 0, 0]])}
        arts = [art(f"story {i}") for i in range(4)] + [art("mumbai flood", topic="Local: Mumbai")]
        with patch.object(scoring, "_embed_texts", _fake_embedder(vm)), \
             patch.object(scoring, "_get_topic_embeddings", AsyncMock(return_value=anchors)):
            result = await scoring.rank_articles(arts, interests=["Tech"], keep_fraction=0.5)
        local = [r for r in result["ranked"] if r["is_local"]]
        assert local and all(r.get("kept", True) for r in local), "local must survive the keep cut"

    async def test_failed_embeddings_degrade_safely(self):
        async def all_zero(texts):
            return np.zeros((len(texts), 4))
        anchors = {"Tech": np.array([[1, 0, 0, 0]])}
        with patch.object(scoring, "_embed_texts", all_zero), \
             patch.object(scoring, "_get_topic_embeddings", AsyncMock(return_value=anchors)):
            result = await scoring.rank_articles(
                [art("a"), art("b")], interests=["Tech"], keep_fraction=1.0)
        assert len(result["ranked"]) == 2             # zero-vecs never cluster


class TestAnchorWarmup:
    async def test_anchors_cached_once(self):
        scoring._topic_embeddings = None
        calls = {"n": 0}
        async def fake(texts):
            calls["n"] += 1
            return np.ones((len(texts), 4))
        with patch.object(scoring, "_embed_texts", fake):
            a1 = await scoring._get_topic_embeddings()
            a2 = await scoring._get_topic_embeddings()
        assert a1 is a2
        assert calls["n"] == len(scoring.TOPIC_ANCHORS)
        scoring._topic_embeddings = None
