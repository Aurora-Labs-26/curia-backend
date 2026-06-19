"""
tests/test_firebase_unit.py
Unit tests for core/firebase.py — Firebase init, token verify, user delete.
Firebase Admin SDK is fully mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

import core.firebase as fb


@pytest.fixture(autouse=True)
def _reset_app():
    """Reset the module-level _app before each test."""
    original = fb._app
    fb._app = None
    yield
    fb._app = original


# ---------------------------------------------------------------------------
# is_initialized
# ---------------------------------------------------------------------------


class TestIsInitialized:
    def test_false_by_default(self):
        assert fb.is_initialized() is False

    def test_true_when_app_set(self):
        fb._app = MagicMock()
        assert fb.is_initialized() is True


# ---------------------------------------------------------------------------
# verify_id_token
# ---------------------------------------------------------------------------


class TestVerifyIdToken:
    def test_raises_when_not_initialized(self):
        with pytest.raises(RuntimeError, match="Firebase not initialized"):
            fb.verify_id_token("some-token")

    def test_delegates_to_firebase_auth(self):
        fb._app = MagicMock()
        with patch.object(fb.firebase_auth, "verify_id_token", return_value={"uid": "u1"}) as mock_verify:
            claims = fb.verify_id_token("tok-123")
        assert claims["uid"] == "u1"
        mock_verify.assert_called_once_with("tok-123")


# ---------------------------------------------------------------------------
# delete_user
# ---------------------------------------------------------------------------


class TestDeleteUser:
    def test_returns_false_when_not_initialized(self):
        assert fb.delete_user("uid-1") is False

    def test_returns_false_for_empty_uid(self):
        fb._app = MagicMock()
        assert fb.delete_user("") is False

    def test_returns_false_for_none_uid(self):
        fb._app = MagicMock()
        assert fb.delete_user(None) is False

    def test_success_returns_true(self):
        fb._app = MagicMock()
        with patch.object(fb.firebase_auth, "delete_user") as mock_del:
            assert fb.delete_user("uid-1") is True
        mock_del.assert_called_once_with("uid-1")

    def test_user_not_found_returns_true(self):
        fb._app = MagicMock()
        with patch.object(fb.firebase_auth, "delete_user", side_effect=fb.firebase_auth.UserNotFoundError("gone")):
            assert fb.delete_user("uid-gone") is True

    def test_other_exception_returns_false(self):
        fb._app = MagicMock()
        with patch.object(fb.firebase_auth, "delete_user", side_effect=RuntimeError("oops")):
            assert fb.delete_user("uid-err") is False


# ---------------------------------------------------------------------------
# init_firebase
# ---------------------------------------------------------------------------


class TestInitFirebase:
    def test_idempotent_when_already_initialized(self):
        fb._app = MagicMock()
        # Should not call initialize_app again
        with patch.object(fb.firebase_admin, "initialize_app") as mock_init:
            fb.init_firebase()
        mock_init.assert_not_called()

    @patch.dict("os.environ", {"FIREBASE_SERVICE_ACCOUNT_JSON": '{"type":"service_account","project_id":"p"}'})
    def test_init_from_json_env(self):
        mock_app = MagicMock()
        with patch.object(fb.credentials, "Certificate") as mock_cert, \
             patch.object(fb.firebase_admin, "initialize_app", return_value=mock_app):
            fb.init_firebase()
        assert fb._app is mock_app
        mock_cert.assert_called_once()

    @patch.dict("os.environ", {"FIREBASE_SERVICE_ACCOUNT_JSON": "not-json"})
    def test_init_from_bad_json_falls_through(self):
        """Invalid JSON should log error and continue to next priority."""
        with patch.dict("os.environ", {"GOOGLE_APPLICATION_CREDENTIALS": "", "FIREBASE_PROJECT_ID": ""}, clear=False):
            fb.init_firebase()
        assert fb._app is None

    @patch.dict("os.environ", {
        "FIREBASE_SERVICE_ACCOUNT_JSON": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "/path/to/creds.json",
    }, clear=False)
    def test_init_from_file_path(self):
        mock_app = MagicMock()
        with patch.object(fb.credentials, "Certificate") as mock_cert, \
             patch.object(fb.firebase_admin, "initialize_app", return_value=mock_app):
            fb.init_firebase()
        assert fb._app is mock_app
        mock_cert.assert_called_once_with("/path/to/creds.json")

    @patch.dict("os.environ", {
        "FIREBASE_SERVICE_ACCOUNT_JSON": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
        "FIREBASE_PROJECT_ID": "my-project",
    }, clear=False)
    def test_init_from_project_id(self):
        mock_app = MagicMock()
        with patch.object(fb.firebase_admin, "initialize_app", return_value=mock_app):
            fb.init_firebase()
        assert fb._app is mock_app

    @patch.dict("os.environ", {
        "FIREBASE_SERVICE_ACCOUNT_JSON": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
        "FIREBASE_PROJECT_ID": "",
    }, clear=False)
    def test_init_no_credentials_leaves_none(self):
        fb.init_firebase()
        assert fb._app is None
