"""
tests/test_episodes_offline.py — offline-first enablers on the episode
routes, mirroring the brief's: client_ts replay guard on progress, ETag/304
on list + detail. DB layer mocked at the route module's imports.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

EP = "12345678-1234-1234-1234-123456789012"


def _client():
    from api.auth import current_user_id
    from api.routes.episodes import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user_id] = lambda: "u1"
    return TestClient(app)


def _episode_row(**over):
    row = {"id": uuid.UUID(EP), "show_name": "s", "title": "t", "status": "ready",
           "created_at": "2026-08-12T00:00:00Z", "error": None, "transcript": None,
           "outline": None, "audio_path": None, "source_ids": [],
           "editorial_direction": None, "quality_score": None,
           "quality_feedback": None, "quality_violations": [],
           "regenerated": False, "length_minutes": None, "speaker_override": None,
           "tts_timings": None, "bgm_plan": None}
    row.update(over)
    return row


class TestEpisodeProgressReplayGuard:
    def test_client_ts_guarded_update_stores_client_time(self):
        with patch("api.routes.episodes.db_execute", AsyncMock()) as ex:
            r = _client().put(f"/episodes/{EP}/progress", json={
                "play_progress": 0.6, "client_ts": "2026-08-12T09:00:00Z"})
        assert r.status_code == 204
        sql = ex.await_args.args[0]
        assert "last_played_at IS NULL OR last_played_at < $client_ts" in sql
        assert "last_played_at = $client_ts" in sql
        assert "NOW()" not in sql

    def test_without_client_ts_keeps_blind_overwrite(self):
        with patch("api.routes.episodes.db_execute", AsyncMock()) as ex:
            r = _client().put(f"/episodes/{EP}/progress", json={"play_progress": 0.6})
        assert r.status_code == 204
        assert "NOW()" in ex.await_args.args[0]

    def test_garbage_client_ts_is_422(self):
        with patch("api.routes.episodes.db_execute", AsyncMock()) as ex:
            r = _client().put(f"/episodes/{EP}/progress", json={
                "play_progress": 0.6, "client_ts": "not-a-time"})
        assert r.status_code == 422
        ex.assert_not_awaited()

    def test_stale_guarded_update_is_still_204(self):
        """Zero rows matched (stale event) must not error — the outbox drops
        any 2xx and must never retry a superseded update forever."""
        with patch("api.routes.episodes.db_execute", AsyncMock(return_value="UPDATE 0")):
            r = _client().put(f"/episodes/{EP}/progress", json={
                "play_progress": 0.1, "client_ts": "2026-08-12T08:00:00Z"})
        assert r.status_code == 204


class TestEpisodeEtags:
    def _detail_mocks(self):
        return patch("api.routes.episodes.db_fetchrow",
                     AsyncMock(return_value=_episode_row()))

    def test_detail_sets_etag_and_304s(self):
        with self._detail_mocks():
            c = _client()
            r1 = c.get(f"/episodes/{EP}")
            etag = r1.headers.get("etag")
            assert r1.status_code == 200 and etag
            r2 = c.get(f"/episodes/{EP}", headers={"If-None-Match": etag})
        assert r2.status_code == 304 and r2.content == b""

    def test_detail_changed_content_changes_etag(self):
        with patch("api.routes.episodes.db_fetchrow",
                   AsyncMock(return_value=_episode_row())) as fr:
            c = _client()
            e1 = c.get(f"/episodes/{EP}").headers["etag"]
            fr.return_value = _episode_row(title="different title")
            r = c.get(f"/episodes/{EP}", headers={"If-None-Match": e1})
        assert r.status_code == 200
        assert r.headers["etag"] != e1

    def test_list_sets_etag_and_304s(self):
        rows = [{"id": uuid.UUID(EP), "show_name": "s", "title": "t",
                 "status": "ready", "created_at": "2026-08-12T00:00:00Z",
                 "error": None, "quality_score": None, "length_minutes": None,
                 "speaker_override": None, "source_ids": [], "outline": None,
                 "play_progress": None, "listened": False,
                 "last_played_at": None, "show_idea_id": None}]
        with patch("api.routes.episodes.db_query", AsyncMock(return_value=rows)):
            c = _client()
            r1 = c.get("/episodes")
            etag = r1.headers.get("etag")
            assert r1.status_code == 200 and etag
            r2 = c.get("/episodes", headers={"If-None-Match": etag})
        assert r2.status_code == 304
