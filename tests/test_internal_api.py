"""
tests/test_internal_api.py
Tests for api/routes/internal.py — secret validation and request models.

Verifies:
  - _check_secret passes when secret matches
  - _check_secret passes when no secret is configured (open access)
  - _check_secret raises 403 when secret is wrong
  - _check_secret raises 403 when secret is missing but configured
  - SeedInterestsRequest validation
  - UserBriefContext model
"""

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException


class TestCheckSecret:

    def test_passes_when_secret_matches(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "my-secret-123")
        from api.routes.internal import _check_secret
        _check_secret("my-secret-123")

    def test_passes_when_no_secret_configured(self, monkeypatch):
        monkeypatch.delenv("INTERNAL_SECRET", raising=False)
        from api.routes.internal import _check_secret
        _check_secret(None)
        _check_secret("anything")

    def test_rejects_wrong_secret(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "correct")
        from api.routes.internal import _check_secret
        with pytest.raises(HTTPException) as exc_info:
            _check_secret("wrong")
        assert exc_info.value.status_code == 403

    def test_rejects_missing_header_when_secret_set(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "correct")
        from api.routes.internal import _check_secret
        with pytest.raises(HTTPException) as exc_info:
            _check_secret(None)
        assert exc_info.value.status_code == 403

    def test_rejects_empty_string(self, monkeypatch):
        monkeypatch.setenv("INTERNAL_SECRET", "correct")
        from api.routes.internal import _check_secret
        with pytest.raises(HTTPException) as exc_info:
            _check_secret("")
        assert exc_info.value.status_code == 403


class TestSeedInterestsRequest:

    def test_valid_request(self):
        from api.routes.internal import SeedInterestsRequest
        req = SeedInterestsRequest(
            user_id="user-123",
            interests=["AI", "climate"],
        )
        assert req.user_id == "user-123"
        assert req.interests == ["AI", "climate"]
        assert req.location_city == "Bangalore"

    def test_custom_location(self):
        from api.routes.internal import SeedInterestsRequest
        req = SeedInterestsRequest(
            user_id="u1",
            interests=["tech"],
            location_city="Mumbai",
        )
        assert req.location_city == "Mumbai"

    def test_empty_interests_allowed(self):
        from api.routes.internal import SeedInterestsRequest
        req = SeedInterestsRequest(user_id="u1", interests=[])
        assert req.interests == []


class TestUserBriefContext:

    def test_full_model(self):
        from api.routes.internal import UserBriefContext
        ctx = UserBriefContext(
            user_id="u1",
            name="Test User",
            interests=["AI", "music"],
            location_city="SF",
            brief_notify_time="08:00",
            brief_enabled=True,
            fcm_token="token-abc",
            recent_saves=[{"title": "Article", "url": "https://example.com"}],
        )
        assert ctx.user_id == "u1"
        assert len(ctx.recent_saves) == 1

    def test_minimal_model(self):
        from api.routes.internal import UserBriefContext
        ctx = UserBriefContext(
            user_id="u1",
            name=None,
            interests=[],
            location_city=None,
            brief_notify_time=None,
            brief_enabled=False,
            fcm_token=None,
            recent_saves=[],
        )
        assert not ctx.brief_enabled
        assert ctx.recent_saves == []
