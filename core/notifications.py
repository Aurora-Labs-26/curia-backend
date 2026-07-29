"""
core/notifications.py
Scheduled push notification senders.

Two daily jobs (both target IST via fixed UTC offsets):
  - send_listen_reminders()    14:30 UTC (8:00 PM IST) — unplayed episode nudge
  - send_reengagement_reminders()  05:30 UTC (11:00 AM IST) — new user with no links

Plus one event-triggered send, called directly from worker/handlers/brief.py
once a specific user's brief finishes generating (not a batch scan — there's
nothing to scan for, the caller already knows exactly who and when):
  - send_brief_ready(user_id)

All are idempotent: notification_log deduplicates sends per (user, type, date).
"""

from __future__ import annotations

import asyncio
from datetime import date

from loguru import logger


async def _claim_send(conn, user_id: str, notification_type: str) -> bool:
    """
    Atomically claim today's send for (user, type). Returns True if WE won the
    claim. Claim-before-send makes concurrent workers (two ECS services both
    running the cron) race-safe: only one inserts, only the winner sends.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO notification_log (user_id, type, sent_date)
        VALUES ($1, $2, CURRENT_DATE)
        ON CONFLICT (user_id, type, sent_date) DO NOTHING
        RETURNING 1
        """,
        user_id,
        notification_type,
    )
    return row is not None


async def _release_claim(conn, user_id: str, notification_type: str) -> None:
    """Send failed after claiming — release so a later run can retry today."""
    await conn.execute(
        """
        DELETE FROM notification_log
        WHERE user_id = $1 AND type = $2 AND sent_date = CURRENT_DATE
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

            if not await _claim_send(conn, user_id, "listen_reminder"):
                continue  # another worker claimed this user today
            try:
                await _send_fcm(token, title, body, data={"type": "listen_reminder"})
                sent += 1
                logger.info(f"[notifications] listen_reminder sent user_id={user_id} unplayed={count}")
            except Exception as exc:
                errors += 1
                await _release_claim(conn, user_id, "listen_reminder")
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

            if not await _claim_send(conn, user_id, "reengagement"):
                continue  # another worker claimed this user today
            try:
                await _send_fcm(
                    token,
                    title="Start building your show",
                    body="Share an article link to produce your first show",
                    data={"type": "reengagement"},
                )
                sent += 1
                logger.info(f"[notifications] reengagement sent user_id={user_id}")
            except Exception as exc:
                errors += 1
                await _release_claim(conn, user_id, "reengagement")
                logger.warning(f"[notifications] reengagement failed user_id={user_id}: {exc}")

    logger.info(f"[notifications] reengagement done sent={sent} errors={errors}")


async def send_brief_ready(user_id: str) -> None:
    """Called directly from worker/handlers/brief.py right after a brief
    finishes generating (see brief/audio.py's docstring — audio is an
    enhancement, so this fires once the text manifest is ready regardless of
    whether the audio render succeeded). Per-user, not a batch scan: the
    caller already knows exactly who and when, unlike the two cron jobs
    above which have to discover their targets."""
    from core.db.connection import get_db

    async with get_db() as conn:
        row = await conn.fetchrow("SELECT fcm_token FROM users WHERE id = $1", user_id)
        if not row or not row["fcm_token"]:
            return
        if not await _claim_send(conn, user_id, "brief_ready"):
            return  # already sent today (retry/requeue) or another worker won the race
        try:
            await _send_fcm(
                row["fcm_token"],
                title="Your daily roundup is ready",
                body="Tap to listen now.",
                data={"type": "brief_ready"},
            )
            logger.info(f"[notifications] brief_ready sent user_id={user_id}")
        except Exception as exc:
            await _release_claim(conn, user_id, "brief_ready")
            logger.warning(f"[notifications] brief_ready failed user_id={user_id}: {exc}")
