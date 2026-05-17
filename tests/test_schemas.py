"""
tests/test_schemas.py
Pydantic schema validation — request/response shapes.
"""
import pytest
from pydantic import ValidationError
from api.schemas import (
    CreateSourceRequest,
    CreateEpisodeRequest,
    EpisodeSummary,
    SourceSummary,
    MeResponse,
    AuthUserResponse,
)


def test_create_source_requires_valid_url():
    req = CreateSourceRequest(url="https://example.com/article")
    assert str(req.url) == "https://example.com/article"


def test_create_source_rejects_invalid_url():
    with pytest.raises(ValidationError):
        CreateSourceRequest(url="not-a-url")


def test_create_episode_valid():
    req = CreateEpisodeRequest(show_name="clarity_engine")
    assert req.show_name == "clarity_engine"
    assert req.speaker is None
    assert req.length_minutes is None


def test_create_episode_with_speaker():
    req = CreateEpisodeRequest(show_name="narrative_drift", speaker="kenji")
    assert req.speaker == "kenji"


def test_create_episode_rejects_unknown_speaker():
    with pytest.raises(ValidationError, match="speaker"):
        CreateEpisodeRequest(show_name="narrative_drift", speaker="unknown_person")


def test_create_episode_allows_none_speaker():
    req = CreateEpisodeRequest(show_name="narrative_drift", speaker=None)
    assert req.speaker is None


def test_create_episode_length_bounds():
    req = CreateEpisodeRequest(show_name="narrative_drift", length_minutes=10)
    assert req.length_minutes == 10


def test_create_episode_rejects_length_too_short():
    with pytest.raises(ValidationError):
        CreateEpisodeRequest(show_name="narrative_drift", length_minutes=1)


def test_create_episode_rejects_length_too_long():
    with pytest.raises(ValidationError):
        CreateEpisodeRequest(show_name="narrative_drift", length_minutes=60)


def test_episode_summary_defaults():
    from datetime import datetime
    from uuid import uuid4
    ep = EpisodeSummary(id=uuid4(), status="queued", created_at=datetime.now())
    assert ep.quality_score is None
    assert ep.length_minutes is None
    assert ep.speaker_override is None


def test_source_summary_defaults():
    from datetime import datetime
    from uuid import uuid4
    s = SourceSummary(id=uuid4(), status="queued", created_at=datetime.now())
    assert s.covered_in == 0
    assert s.title is None


def test_auth_user_response():
    r = AuthUserResponse(id="u1", email="a@b.com", name="Test", role="user")
    assert r.avatar_url is None
    assert r.role == "user"


def test_me_response_defaults():
    r = MeResponse(id="u1")
    assert r.role == "user"
    assert r.email is None
