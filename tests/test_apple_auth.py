"""
tests/test_apple_auth.py
Sign in with Apple — name/email + no-email handling, end to end on the server side.

Mock-only: no DB, no network, no Firebase project, no deploy. Two groups:

1. `_resolve_token` provisioning — Apple "Hide My Email" / no-email accounts:
   provisions with a NULL email, returning users resolve by firebase_uid, relay
   addresses behave like normal emails, and the ON CONFLICT path COALESCEs so a
   later token that omits email/name never wipes a stored value.

2. `link_apple` (POST /auth/apple) — the name/email Apple gives the client on the
   FIRST authorization is forwarded here and persisted with COALESCE+NULLIF
   (fill-only), independent of the refresh-token exchange.
"""

from unittest.mock import AsyncMock, patch

import api.auth as auth
from api.auth import CurrentUser
from api.routes import auth as auth_routes
from api.routes.auth import AppleLinkRequest, link_apple


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _firebase(decoded):
    return patch.multiple(
        "core.firebase",
        is_initialized=lambda: True,
        verify_id_token=lambda _t: decoded,
    )


def _resolve_db(fetch_returns):
    calls: list[tuple[str, dict]] = []

    async def fake_execute(sql, params=None):
        calls.append((" ".join(sql.split()), params or {}))
        return "OK"

    ctx = patch.multiple(
        "api.auth",
        db_fetchrow=AsyncMock(side_effect=list(fetch_returns)),
        db_execute=AsyncMock(side_effect=fake_execute),
    )
    return calls, ctx


def _inserts(calls):
    return [c for c in calls if c[0].startswith("INSERT INTO users")]


def _updates(calls):
    return [c for c in calls if c[0].startswith("UPDATE users")]


# ---------------------------------------------------------------------------
# 1. provisioning — no-email / relay / COALESCE
# ---------------------------------------------------------------------------

async def test_no_email_new_user_provisions_with_null_email():
    calls, ctx = _resolve_db([None])  # firebase_uid miss; email block skipped
    with _firebase({"uid": "fb-noemail", "email": None, "name": "Arihant"}), ctx:
        user = await auth._resolve_token("Bearer tok")
    assert user.id and user.role == "user"
    ins = _inserts(calls)
    assert len(ins) == 1
    assert ins[0][1]["email"] is None       # NULL email accepted (nullable column)
    assert ins[0][1]["name"] == "Arihant"


async def test_no_email_returning_user_resolves_by_uid():
    calls, ctx = _resolve_db([{"id": "existing-id", "role": "user"}])
    with _firebase({"uid": "fb-1", "email": None}), ctx:
        user = await auth._resolve_token("Bearer tok")
    assert user.id == "existing-id"
    assert calls == []  # no writes; email never needed


async def test_hide_my_email_relay_behaves_like_normal_email():
    relay = "abc123def@privaterelay.appleid.com"
    calls, ctx = _resolve_db([None, None])  # uid miss, email miss
    with _firebase({"uid": "fb-relay", "email": relay, "name": "R"}), ctx:
        await auth._resolve_token("Bearer tok")
    assert _inserts(calls)[0][1]["email"] == relay


async def test_relink_by_email_when_uid_changes():
    calls, ctx = _resolve_db([None, {"id": "old-id", "role": "user"}])
    with _firebase({"uid": "fb-new", "email": "a@b.com", "name": "New"}), ctx:
        user = await auth._resolve_token("Bearer tok")
    assert user.id == "old-id"
    assert _updates(calls) and not _inserts(calls)


async def test_insert_conflict_coalesces_email_and_name():
    # a later token that omits email/name must not overwrite stored values
    calls, ctx = _resolve_db([None])
    with _firebase({"uid": "fb-x", "email": None, "name": None}), ctx:
        await auth._resolve_token("Bearer tok")
    sql = _inserts(calls)[0][0]
    assert "COALESCE(EXCLUDED.email, users.email)" in sql
    assert "COALESCE(EXCLUDED.name, users.name)" in sql


# ---------------------------------------------------------------------------
# 2. link_apple — name/email forwarding from first-auth
# ---------------------------------------------------------------------------

def _route_db():
    calls: list[tuple[str, dict]] = []

    async def fake_execute(sql, params=None):
        calls.append((" ".join(sql.split()), params or {}))
        return "OK"

    return calls, patch.object(auth_routes, "db_execute", new=AsyncMock(side_effect=fake_execute))


_USER = CurrentUser(id="user-1", role="user")


async def test_link_apple_persists_name_and_email_fill_only():
    calls, dbp = _route_db()
    with dbp, patch("core.apple.exchange_code", new=AsyncMock(return_value=(None, None))):
        out = await link_apple(
            AppleLinkRequest(authorization_code="code", name="Arihant Barjatya",
                             email="arihant@icloud.com"),
            user=_USER,
        )
    assert out is None  # 204
    name_updates = [c for c in calls if "name =" in c[0] and "EXCLUDED" not in c[0]]
    assert name_updates, "expected a name/email UPDATE"
    sql, params = name_updates[0]
    assert "COALESCE(NULLIF($name, ''), name)" in sql
    assert "COALESCE(NULLIF($email, ''), email)" in sql
    assert params["name"] == "Arihant Barjatya"
    assert params["email"] == "arihant@icloud.com"
    assert params["id"] == "user-1"


async def test_link_apple_skips_name_update_when_absent():
    # later sign-in: no name/email forwarded → no name/email UPDATE runs
    calls, dbp = _route_db()
    with dbp, patch("core.apple.exchange_code", new=AsyncMock(return_value=(None, None))):
        await link_apple(AppleLinkRequest(authorization_code="code"), user=_USER)
    assert not [c for c in calls if "NULLIF($name" in c[0]]


async def test_link_apple_stores_refresh_token_when_exchange_succeeds():
    calls, dbp = _route_db()
    with dbp, patch("core.apple.exchange_code", new=AsyncMock(return_value=("rt-123", "com.curia"))):
        await link_apple(AppleLinkRequest(authorization_code="code"), user=_USER)
    rt_updates = [c for c in calls if "apple_refresh_token" in c[0]]
    assert rt_updates and rt_updates[0][1]["rt"] == "rt-123"


async def test_link_apple_name_persists_even_if_exchange_raises():
    # name/email step is independent of the (best-effort) token exchange
    calls, dbp = _route_db()
    with dbp, patch("core.apple.exchange_code", new=AsyncMock(side_effect=RuntimeError("apple down"))):
        out = await link_apple(
            AppleLinkRequest(authorization_code="code", name="X", email="x@y.com"),
            user=_USER,
        )
    assert out is None
    assert [c for c in calls if "NULLIF($name" in c[0]], "name must persist despite exchange failure"
