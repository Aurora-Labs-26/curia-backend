"""
scripts/test_notifications.py
Manually trigger notification sends for testing.

Usage:
    python -m scripts.test_notifications --type listen_reminder
    python -m scripts.test_notifications --type reengagement
    python -m scripts.test_notifications --type all

Runs against the real DB and FCM — actual notifications will be sent.
Clears today's notification_log entries first so dedup doesn't block the send.
"""

import argparse
import asyncio
import os

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))


async def _clear_log(notification_type: str) -> None:
    from core.db.connection import get_db
    async with get_db() as conn:
        rows = await conn.fetch(
            "DELETE FROM notification_log WHERE type = $1 AND sent_date = CURRENT_DATE RETURNING id",
            notification_type,
        )
        print(f"[test] cleared {len(rows)} existing log entries for type={notification_type}")


async def run(notification_type: str) -> None:
    from core.firebase import init_firebase
    from core.notifications import send_listen_reminders, send_reengagement_reminders

    init_firebase()

    types = ["listen_reminder", "reengagement"] if notification_type == "all" else [notification_type]

    for t in types:
        print(f"\n[test] === {t} ===")
        await _clear_log(t)
        if t == "listen_reminder":
            await send_listen_reminders()
        elif t == "reengagement":
            await send_reengagement_reminders()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--type",
        choices=["listen_reminder", "reengagement", "all"],
        default="all",
        help="Which notification to test",
    )
    args = parser.parse_args()
    asyncio.run(run(args.type))


if __name__ == "__main__":
    main()
