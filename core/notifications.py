"""
core/notifications.py
Scheduled push notification senders.

Two daily jobs (both target IST via fixed UTC offsets):
  - send_listen_reminders()    14:30 UTC (8:00 PM IST) — unplayed episode nudge
  - send_reengagement_reminders()  05:30 UTC (11:00 AM IST) — new user with no links

Both are idempotent: notification_log deduplicates sends per (user, type, date).
"""

from __future__ import annotations

import asyncio
from datetime import date

from loguru import logger


async def _record_sent(conn, user_id: str, notification_type: str) -> None:
    await conn.execute(
        """
        INSERT INTO notification_log (user_id, type, sent_date)
        VALUES ($1, $2, CURRENT_DATE)
        ON CONFLICT (user_id, type, sent_date) DO NOTHING
        """,
        user_id,
        notification_type,
    )


async def _send_fcm(token: str, title: str, body: str, data: dict | None = None) -> None:
    from firebase_admin import messaging
    msg = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=data or {},
        token=token,
    )
    await asyncio.to_thread(messaging.send, msg)


async def send_listen_reminders() -> None:
    """
    Send a batched 'unplayed episodes' notification to users who have ≥1 ready
    episode they haven't listened to yet. Runs once per day per user.

    Unplayed = episode.status='ready' AND (last_played_at IS NULL OR last_played_at < episode.created_at)
    """
    from core.db.connection import get_db

    logger.info("[notifications] starting listen_reminder run")
    sent = 0
    errors = 0

    async with get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT
                u.id        AS user_id,
                u.fcm_token,
                COUNT(e.id) AS unplayed_count
            FROM users u
            JOIN episode e ON e.user_id = u.id
                AND e.status = 'ready'
                AND (e.last_played_at IS NULL OR e.last_played_at < e.created_at)
            WHERE u.fcm_token IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM notification_log nl
                  WHERE nl.user_id = u.id
                    AND nl.type = 'listen_reminder'
                    AND nl.sent_date = CURRENT_DATE
              )
            GROUP BY u.id, u.fcm_token
            HAVING COUNT(e.id) > 0
            """
        )

        for row in rows:
            user_id = row["user_id"]
            token = row["fcm_token"]
            count = row["unplayed_count"]

            title = "Your show is ready" if count == 1 else f"You have {count} shows waiting"
            body = "Tap to listen now." if count == 1 else "Tap to start listening."

            try:
                await _send_fcm(token, title, body, data={"type": "listen_reminder"})
                await _record_sent(conn, user_id, "listen_reminder")
                sent += 1
                logger.info(f"[notifications] listen_reminder sent user_id={user_id} unplayed={count}")
            except Exception as exc:
                errors += 1
                logger.warning(f"[notifications] listen_reminder failed user_id={user_id}: {exc}")

    logger.info(f"[notifications] listen_reminder done sent={sent} errors={errors}")


async def send_reengagement_reminders() -> None:
    """
    Send a daily 'get started' notification to users who signed up in the last 7 days
    but have never shared a link (zero sources). Stops as soon as they add one.
    """
    from core.db.connection import get_db

    logger.info("[notifications] starting reengagement run")
    sent = 0
    errors = 0

    async with get_db() as conn:
        rows = await conn.fetch(
            """
            SELECT u.id AS user_id, u.fcm_token
            FROM users u
            WHERE u.fcm_token IS NOT NULL
              AND u.created_at >= now() - interval '7 days'
              AND NOT EXISTS (
                  SELECT 1 FROM source s WHERE s.user_id = u.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM notification_log nl
                  WHERE nl.user_id = u.id
                    AND nl.type = 'reengagement'
                    AND nl.sent_date = CURRENT_DATE
              )
            """
        )

        for row in rows:
            user_id = row["user_id"]
            token = row["fcm_token"]

            try:
                await _send_fcm(
                    token,
                    title="Start building your show",
                    body="Share an article link to produce your first show",
                    data={"type": "reengagement"},
                )
                await _record_sent(conn, user_id, "reengagement")
                sent += 1
                logger.info(f"[notifications] reengagement sent user_id={user_id}")
            except Exception as exc:
                errors += 1
                logger.warning(f"[notifications] reengagement failed user_id={user_id}: {exc}")

    logger.info(f"[notifications] reengagement done sent={sent} errors={errors}")
