"""
tests/test_auth.py
Auth module — token resolution, role gating. No DB calls.
"""
import pytest
from unittest.mock import AsyncMock, patch
from api.auth import CurrentUser, _resolve_token


@pytest.mark.asyncio
async def test_missing_auth_header_raises_401():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await _resolve_token(None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_empty_bearer_raises_401():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await _resolve_token("Bearer ")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_malformed_header_raises_401():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await _resolve_token("Basic abc123")
    assert exc.value.status_code == 401


def test_current_user_is_qa():
    user = CurrentUser(id="u1", role="qa")
    assert user.is_qa is True


def test_current_user_is_not_qa():
    user = CurrentUser(id="u1", role="user")
    assert user.is_qa is False
