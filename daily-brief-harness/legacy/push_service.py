"""
Firebase FCM push notifications for brief ready events.
"""

from __future__ import annotations

import asyncio
import logging
import os

logger = logging.getLogger(__name__)

_firebase_initialized = False


def _init_firebase() -> None:
    global _firebase_initialized
    if _firebase_initialized:
        return
    import json
    import firebase_admin
    from firebase_admin import credentials

    sa_json = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")
    if sa_json:
        cred = credentials.Certificate(json.loads(sa_json))
    else:
        cred_path = os.environ.get("FIREBASE_CREDENTIALS_PATH", "sa-key.json")
        cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred)
    _firebase_initialized = True


async def send_brief_ready(fcm_token: str, date: str) -> None:
    """Send 'your brief is ready' push notification."""
    if not fcm_token:
        return
    try:
        _init_firebase()
        from firebase_admin import messaging

        message = messaging.Message(
            notification=messaging.Notification(
                title="Your morning brief is ready",
                body="Tap to listen to today's news",
            ),
            data={"type": "daily_brief", "date": date},
            token=fcm_token,
        )
        await asyncio.to_thread(messaging.send, message)
        logger.info(f"[push] brief ready PN sent for date={date}")
    except Exception as exc:
        logger.warning(f"[push] FCM send failed: {exc}")
