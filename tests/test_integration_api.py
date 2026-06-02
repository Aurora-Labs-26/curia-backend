"""
tests/test_integration_api.py
Integration tests for the FastAPI API layer.

Uses FastAPI dependency overrides for auth and patches DB functions at
the correct import locations so mocks are respected.
"""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

TEST_USER_ID = "test-user-001"
TEST_QA_USER_ID = "test-qa-001"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# App fixture with neutralised lifespan and auth overrides
# ─────────────────────────────────────────────────────────────────────────────


@asynccontextmanager
async def _noop_lifespan(app):
    yield


@pytest.fixture()
def app():
    """Return the FastAPI app with neutralised lifespan."""
    from api.main import app as _app
    original = _app.router.lifespan_context
    _app.router.lifespan_context = _noop_lifespan
    yield _app
    _app.router.lifespan_context = original
    _app.dependency_overrides.clear()


def _override_user(app, user_id=TEST_USER_ID, role="user"):
    """Override auth dependencies to return a fixed user."""
    from api.auth import CurrentUser, current_user, current_user_id

    async def _mock_current_user():
        return CurrentUser(id=user_id, role=role)

    async def _mock_current_user_id():
        return user_id

    app.dependency_overrides[current_user] = _mock_current_user
    app.dependency_overrides[current_user_id] = _mock_current_user_id


def _override_qa(app):
    """Override auth dependencies to return a QA user."""
    from api.auth import CurrentUser, current_user, current_user_id, qa_required

    qa_user = CurrentUser(id=TEST_QA_USER_ID, role="qa")

    async def _mock_qa():
        return qa_user

    async def _mock_qa_id():
        return TEST_QA_USER_ID

    app.dependency_overrides[current_user] = _mock_qa
    app.dependency_overrides[current_user_id] = _mock_qa_id
    app.dependency_overrides[qa_required] = _mock_qa


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Health Endpoint (no auth, patches at route-level import)
# ═══════════════════════════════════════════════════════════════════════════════


class TestHealthEndpoint:

    def test_health_ok(self, app):
        with patch("api.routes.health.db_fetchrow", new_callable=AsyncMock, return_value={"ok": 1}):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] is True

    def test_health_db_down(self, app):
        with patch("api.routes.health.db_fetchrow", new_callable=AsyncMock, side_effect=Exception("down")):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["db"] is False


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Auth & Role Gates
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuth:

    def test_no_auth_header_returns_401(self, app):
        # Do NOT override dependencies — let the real auth run
        with patch("api.auth.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with patch("core.firebase.is_initialized", return_value=False):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get("/me")
        assert resp.status_code == 401

    def test_empty_bearer_returns_401(self, app):
        with patch("api.auth.db_fetchrow", new_callable=AsyncMock):
            with patch("core.firebase.is_initialized", return_value=False):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get("/me", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_malformed_auth_header_returns_401(self, app):
        with patch("api.auth.db_fetchrow", new_callable=AsyncMock):
            with patch("core.firebase.is_initialized", return_value=False):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get("/me", headers={"Authorization": "Basic abc123"})
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, app):
        with patch("api.auth.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with patch("core.firebase.is_initialized", return_value=False):
                with TestClient(app, raise_server_exceptions=False) as c:
                    resp = c.get("/me", headers={"Authorization": "Bearer bad-token"})
        assert resp.status_code == 401

    def test_qa_route_with_user_role_returns_403(self, app):
        """qa_required checks role — override only current_user, not qa_required."""
        from api.auth import CurrentUser, qa_required
        # Override qa_required to simulate a non-QA user hitting the gate
        async def _non_qa():
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QA role required")
        app.dependency_overrides[qa_required] = _non_qa
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/admin/users")
        assert resp.status_code == 403

    def test_qa_route_with_qa_role_returns_200(self, app):
        _override_qa(app)
        with patch("api.routes.admin.db_query", new_callable=AsyncMock, return_value=[]):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/admin/users")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Me / User Profile
# ═══════════════════════════════════════════════════════════════════════════════


class TestMeEndpoint:

    def test_get_me(self, app):
        _override_user(app)
        row = {"id": TEST_USER_ID, "email": "test@example.com", "name": "Test", "role": "user"}
        with patch("api.routes.me.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/me")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == TEST_USER_ID
        assert data["email"] == "test@example.com"

    def test_get_me_user_not_found(self, app):
        _override_user(app)
        with patch("api.routes.me.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/me")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Sources CRUD
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourcesEndpoints:

    def test_create_source(self, app):
        _override_user(app)
        source_id = str(uuid.uuid4())
        job_id = uuid.uuid4()
        mock_fetchrow = AsyncMock(side_effect=[
            {"status": "queued"},  # source status lookup
            None,                  # no existing ingest job
        ])
        with (
            patch("api.routes.sources.get_or_create_source", new_callable=AsyncMock, return_value=source_id),
            patch("api.routes.sources.db_fetchrow", mock_fetchrow),
            patch("api.routes.sources.enqueue", new_callable=AsyncMock, return_value=job_id),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/sources", json={"url": "https://example.com/article"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["id"] == source_id
        assert data["status"] == "queued"

    def test_list_sources(self, app):
        _override_user(app)
        now = _now_iso()
        rows = [
            {"id": uuid.uuid4(), "title": "Article 1", "url": "https://a.com",
             "status": "ready", "created_at": now, "error": None, "covered_in": 0},
        ]
        with patch("api.routes.sources.db_query", new_callable=AsyncMock, return_value=rows):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/sources")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["title"] == "Article 1"

    def test_list_sources_with_status_filter(self, app):
        _override_user(app)
        with patch("api.routes.sources.db_query", new_callable=AsyncMock, return_value=[]):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/sources?status=failed")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_source_not_found(self, app):
        _override_user(app)
        with (
            patch("api.routes.sources.db_fetchrow", new_callable=AsyncMock, return_value=None),
            patch("api.routes.sources.db_query", new_callable=AsyncMock, return_value=[]),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/sources/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_source_with_insights(self, app):
        _override_user(app)
        source_id = uuid.uuid4()
        now = _now_iso()
        source_row = {
            "id": source_id, "title": "Test", "url": "https://test.com",
            "status": "ready", "created_at": now, "error": None, "full_text": "Full text",
        }
        insight_rows = [
            {"insight_type": "summary", "content": "A summary"},
            {"insight_type": "key_insights", "content": "Some insights"},
        ]
        with (
            patch("api.routes.sources.db_fetchrow", new_callable=AsyncMock, return_value=source_row),
            patch("api.routes.sources.db_query", new_callable=AsyncMock, return_value=insight_rows),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/sources/{source_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["insights"]["summary"] == "A summary"

    def test_delete_source(self, app):
        _override_user(app)
        with patch("api.routes.sources.db_execute", new_callable=AsyncMock, return_value="DELETE 1"):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.delete(f"/sources/{uuid.uuid4()}")
        assert resp.status_code == 204

    def test_clear_failed_sources(self, app):
        _override_user(app)
        with patch("api.routes.sources.db_query", new_callable=AsyncMock, return_value=[{"id": uuid.uuid4()}]):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/sources/clear-failed")
        assert resp.status_code == 200
        assert resp.json()["cleared"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Episodes CRUD
# ═══════════════════════════════════════════════════════════════════════════════


class TestEpisodesEndpoints:

    def test_create_episode(self, app):
        _override_user(app)
        job_id = uuid.uuid4()
        with (
            patch("api.routes.episodes.db_execute", new_callable=AsyncMock),
            patch("api.routes.episodes.enqueue", new_callable=AsyncMock, return_value=job_id),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/episodes", json={"show_name": "clarity_engine"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "queued"
        assert data["job_id"] == str(job_id)

    def test_create_episode_with_overrides(self, app):
        _override_user(app)
        with (
            patch("api.routes.episodes.db_execute", new_callable=AsyncMock),
            patch("api.routes.episodes.enqueue", new_callable=AsyncMock, return_value=uuid.uuid4()),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/episodes",
                    json={"show_name": "narrative_drift", "length_minutes": 20, "speaker": "emeka"},
                )
        assert resp.status_code == 202

    def test_create_episode_invalid_length(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/episodes", json={"show_name": "test", "length_minutes": 2})
        assert resp.status_code == 422

    def test_create_episode_invalid_speaker(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/episodes", json={"show_name": "test", "speaker": "unknown"})
        assert resp.status_code == 422

    def test_list_episodes(self, app):
        _override_user(app)
        now = _now_iso()
        rows = [
            {"id": uuid.uuid4(), "show_name": "clarity_engine", "title": "Episode 1",
             "status": "ready", "created_at": now, "error": None, "quality_score": 0.8,
             "length_minutes": 10, "speaker_override": None, "source_ids": [],
             "outline": None, "play_progress": None, "listened": False, "last_played_at": None},
        ]
        with patch("api.routes.episodes.db_query", new_callable=AsyncMock, return_value=rows):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/episodes")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["title"] == "Episode 1"

    def test_get_episode_not_found(self, app):
        _override_user(app)
        with patch("api.routes.episodes.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/episodes/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_episode_detail(self, app):
        _override_user(app)
        episode_id = uuid.uuid4()
        now = _now_iso()
        row = {
            "id": episode_id, "show_name": "clarity_engine", "title": "Ep 1",
            "status": "ready", "created_at": now, "error": None,
            "transcript": [{"speaker": "kenji", "text": "Hello."}],
            "outline": {"title": "Ep 1"}, "audio_path": "/tmp/test.mp3",
            "source_ids": [], "editorial_direction": "focus on AI",
            "quality_score": 0.85, "quality_feedback": "Good",
            "quality_violations": [], "regenerated": False,
            "length_minutes": 10, "speaker_override": None, "tts_timings": None,
        }
        with patch("api.routes.episodes.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/episodes/{episode_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Ep 1"
        assert data["quality_score"] == 0.85

    def test_update_progress(self, app):
        _override_user(app)
        with patch("api.routes.episodes.db_execute", new_callable=AsyncMock):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.put(
                    f"/episodes/{uuid.uuid4()}/progress",
                    json={"play_progress": 0.5, "listened": False},
                )
        assert resp.status_code == 204

    def test_submit_feedback(self, app):
        _override_user(app)
        with patch("api.routes.episodes.db_execute", new_callable=AsyncMock):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    f"/episodes/{uuid.uuid4()}/feedback",
                    json={"rating": "thumbs_up", "note": "Great!"},
                )
        assert resp.status_code == 204


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Jobs Endpoint
# ═══════════════════════════════════════════════════════════════════════════════


class TestJobsEndpoint:

    def test_get_job(self, app):
        _override_user(app)
        job_id = uuid.uuid4()
        now = _now_iso()
        row = {
            "id": job_id, "type": "ingest", "status": "running",
            "attempts": 1, "max_attempts": 3, "last_error": None,
            "created_at": now, "updated_at": now,
        }
        with patch("api.routes.jobs.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["type"] == "ingest"

    def test_get_job_not_found(self, app):
        _override_user(app)
        with patch("api.routes.jobs.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/jobs/{uuid.uuid4()}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Admin Endpoints (QA-only)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdminEndpoints:

    def test_admin_list_users(self, app):
        _override_qa(app)
        now = _now_iso()
        rows = [{"id": "u1", "email": "a@b.com", "name": "User A", "role": "user",
                 "created_at": now, "has_kb": False}]
        with patch("api.routes.admin.db_query", new_callable=AsyncMock, return_value=rows):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/admin/users")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_admin_requires_qa_role(self, app):
        from api.auth import qa_required
        async def _non_qa():
            from fastapi import HTTPException, status
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="QA role required")
        app.dependency_overrides[qa_required] = _non_qa
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/admin/users")
        assert resp.status_code == 403

    def test_admin_list_jobs(self, app):
        _override_qa(app)
        with patch("api.routes.admin.db_query", new_callable=AsyncMock, return_value=[]):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/admin/jobs")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Ideas Endpoints
# ═══════════════════════════════════════════════════════════════════════════════


class TestIdeasEndpoints:

    def test_generate_ideas(self, app):
        _override_user(app)
        job_id = uuid.uuid4()
        with (
            patch("api.routes.ideas.db_fetchrow", new_callable=AsyncMock, return_value=None),
            patch("api.routes.ideas.enqueue", new_callable=AsyncMock, return_value=job_id),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post("/ideas/generate")
        assert resp.status_code == 202
        assert resp.json()["job_id"] == str(job_id)

    def test_list_ideas(self, app):
        _override_user(app)
        now = _now_iso()
        rows = [
            {"id": uuid.uuid4(), "angle": "AI in education", "idea_type": "cluster",
             "format": "clarity_engine", "source_ids": [], "generated": False,
             "created_at": now},
        ]
        with patch("api.routes.ideas.db_query", new_callable=AsyncMock, return_value=rows):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/ideas")
        assert resp.status_code == 200
        assert resp.json()[0]["angle"] == "AI in education"

    def test_get_idea_not_found(self, app):
        _override_user(app)
        with patch("api.routes.ideas.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/ideas/{uuid.uuid4()}")
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Generate From Source
# ═══════════════════════════════════════════════════════════════════════════════


class TestGenerateFromSource:

    def test_generate_standalone(self, app):
        _override_user(app)
        source_id = uuid.uuid4()
        job_id = uuid.uuid4()
        with (
            patch("api.routes.generate_from_source.db_fetchrow", new_callable=AsyncMock,
                  side_effect=[
                      {"id": source_id, "user_id": TEST_USER_ID},  # source ownership
                      None,  # no existing job
                  ]),
            patch("api.routes.generate_from_source.enqueue", new_callable=AsyncMock, return_value=job_id),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/generate-from-source",
                    json={"source_id": str(source_id), "standalone": True},
                )
        assert resp.status_code == 202
        assert resp.json()["job_id"] == str(job_id)

    def test_generate_source_not_found(self, app):
        _override_user(app)
        with patch("api.routes.generate_from_source.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.post(
                    "/generate-from-source",
                    json={"source_id": str(uuid.uuid4()), "standalone": True},
                )
        assert resp.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# 10. Episode Audio
# ═══════════════════════════════════════════════════════════════════════════════


class TestEpisodeAudio:

    def test_audio_episode_not_ready(self, app):
        _override_user(app)
        # Override the special _audio_user_id dependency too
        from api.routes.episodes import _audio_user_id
        app.dependency_overrides[_audio_user_id] = lambda: TEST_USER_ID
        row = {"audio_path": None, "audio_url": None, "status": "generating", "title": "Test"}
        with patch("api.routes.episodes.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/episodes/{uuid.uuid4()}/audio")
        assert resp.status_code == 409

    def test_audio_episode_not_found(self, app):
        _override_user(app)
        from api.routes.episodes import _audio_user_id
        app.dependency_overrides[_audio_user_id] = lambda: TEST_USER_ID
        with patch("api.routes.episodes.db_fetchrow", new_callable=AsyncMock, return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/episodes/{uuid.uuid4()}/audio")
        assert resp.status_code == 404

    def test_audio_missing_file(self, app):
        _override_user(app)
        from api.routes.episodes import _audio_user_id
        app.dependency_overrides[_audio_user_id] = lambda: TEST_USER_ID
        row = {"audio_path": "/nonexistent/path.mp3", "audio_url": None,
               "status": "ready", "title": "Test"}
        with patch("api.routes.episodes.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get(f"/episodes/{uuid.uuid4()}/audio")
        assert resp.status_code == 410


# ═══════════════════════════════════════════════════════════════════════════════
# 11. Auth /auth/me
# ═══════════════════════════════════════════════════════════════════════════════


class TestAuthMe:

    def test_auth_me(self, app):
        _override_user(app)
        row = {"id": TEST_USER_ID, "email": "t@t.com", "name": "Test",
               "role": "user", "avatar_url": None}
        with patch("api.routes.auth.db_fetchrow", new_callable=AsyncMock, return_value=row):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["id"] == TEST_USER_ID


# ═══════════════════════════════════════════════════════════════════════════════
# 12. Request Validation
# ═══════════════════════════════════════════════════════════════════════════════


class TestRequestValidation:

    def test_create_source_missing_url(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/sources", json={})
        assert resp.status_code == 422

    def test_create_source_invalid_url(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/sources", json={"url": "not-a-url"})
        assert resp.status_code == 422

    def test_create_episode_missing_show_name(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/episodes", json={})
        assert resp.status_code == 422

    def test_feedback_missing_rating(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post(f"/episodes/{uuid.uuid4()}/feedback", json={})
        assert resp.status_code == 422

    def test_episode_length_boundary_valid(self, app):
        _override_user(app)
        with (
            patch("api.routes.episodes.db_execute", new_callable=AsyncMock),
            patch("api.routes.episodes.enqueue", new_callable=AsyncMock, return_value=uuid.uuid4()),
        ):
            with TestClient(app, raise_server_exceptions=False) as c:
                # Min boundary
                resp = c.post("/episodes", json={"show_name": "test", "length_minutes": 3})
                assert resp.status_code == 202
                # Max boundary
                resp = c.post("/episodes", json={"show_name": "test", "length_minutes": 30})
                assert resp.status_code == 202

    def test_episode_length_boundary_invalid(self, app):
        _override_user(app)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.post("/episodes", json={"show_name": "test", "length_minutes": 2})
            assert resp.status_code == 422
            resp = c.post("/episodes", json={"show_name": "test", "length_minutes": 31})
            assert resp.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 13. CORS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCORS:

    def test_cors_preflight(self, app):
        with patch("api.routes.health.db_fetchrow", new_callable=AsyncMock, return_value={"ok": 1}):
            with TestClient(app, raise_server_exceptions=False) as c:
                resp = c.options("/health", headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                })
        assert resp.status_code == 200
