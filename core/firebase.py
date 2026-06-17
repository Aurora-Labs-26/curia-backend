"""
core/firebase.py
Firebase Admin SDK initialization and token verification.
"""
from __future__ import annotations

import json
import os
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials
from loguru import logger


_app: Optional[firebase_admin.App] = None


def init_firebase() -> None:
    """Initialize Firebase Admin SDK. Call once at startup."""
    global _app
    if _app is not None:
        return

    # Priority 1: inline JSON via env var (preferred for cloud deployments like Railway)
    sa_json = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        try:
            sa_dict = json.loads(sa_json)
            cred = credentials.Certificate(sa_dict)
            _app = firebase_admin.initialize_app(cred)
            logger.info("[firebase] initialized with FIREBASE_SERVICE_ACCOUNT_JSON")
            return
        except Exception as e:
            logger.error(f"[firebase] failed to parse FIREBASE_SERVICE_ACCOUNT_JSON: {e}")

    # Priority 2: file path (local dev)
    cred_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if cred_path:
        cred = credentials.Certificate(cred_path)
        _app = firebase_admin.initialize_app(cred)
        logger.info(f"[firebase] initialized with service account: {cred_path}")
    else:
        # Falls back to FIREBASE_PROJECT_ID for emulator/testing
        project_id = os.getenv("FIREBASE_PROJECT_ID")
        if project_id:
            _app = firebase_admin.initialize_app(options={"projectId": project_id})
            logger.info(f"[firebase] initialized with project ID: {project_id}")
        else:
            logger.warning(
                "[firebase] No credentials found. Set FIREBASE_SERVICE_ACCOUNT_JSON, "
                "GOOGLE_APPLICATION_CREDENTIALS, or FIREBASE_PROJECT_ID. "
                "Auth will fall back to legacy token mode."
            )


def verify_id_token(id_token: str) -> dict:
    """
    Verify a Firebase ID token and return the decoded claims.
    Raises firebase_admin.auth.InvalidIdTokenError on failure.
    """
    if _app is None:
        raise RuntimeError("Firebase not initialized. Call init_firebase() first.")
    return firebase_auth.verify_id_token(id_token)


def is_initialized() -> bool:
    return _app is not None


def delete_user(firebase_uid: str) -> bool:
    """
    Delete a Firebase Auth user (account deletion). Best-effort and idempotent:
    returns True on success, False if Firebase is uninitialized, the uid is empty,
    or the user is already gone. Never raises — DB deletion must proceed regardless.
    """
    if _app is None or not firebase_uid:
        return False
    try:
        firebase_auth.delete_user(firebase_uid)
        logger.info(f"[firebase] deleted auth user {firebase_uid}")
        return True
    except firebase_auth.UserNotFoundError:
        return True  # already deleted — treat as success
    except Exception as exc:
        logger.warning(f"[firebase] delete_user({firebase_uid}) failed: {exc}")
        return False
