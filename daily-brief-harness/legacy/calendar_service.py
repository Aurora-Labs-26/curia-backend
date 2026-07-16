"""
Google Calendar integration — service account auth against a calendar shared
with the service account's email. Used to surface today's schedule in the
spoken intro segment.
"""
import datetime
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_service = None


def _get_service():
    global _service
    if _service is not None:
        return _service

    from app.config import settings
    if not settings.GOOGLE_SERVICE_ACCOUNT_FILE:
        return None

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    credentials = service_account.Credentials.from_service_account_file(
        settings.GOOGLE_SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    _service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return _service


def _format_time(iso_str: str) -> str:
    try:
        dt = datetime.datetime.fromisoformat(iso_str)
        return dt.strftime("%I:%M %p").lstrip("0")
    except ValueError:
        return "later today"  # all-day event with a date-only string


def get_today_summary() -> str:
    """
    Returns a short natural-language summary of today's events on the
    configured shared calendar, or "" if unavailable/not configured.
    """
    from app.config import settings

    service = _get_service()
    if service is None or not settings.GOOGLE_CALENDAR_ID:
        return ""

    start_of_day = datetime.datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end_of_day = start_of_day + datetime.timedelta(days=1)

    try:
        events_result = service.events().list(
            calendarId=settings.GOOGLE_CALENDAR_ID,
            timeMin=start_of_day.isoformat(),
            timeMax=end_of_day.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        ).execute()
    except Exception as e:
        logger.warning(f"Google Calendar fetch failed: {e}")
        return ""

    events = events_result.get("items", [])
    if not events:
        return "No meetings scheduled today"

    first = events[0]
    first_title = first.get("summary", "an event")
    first_time = _format_time(first["start"].get("dateTime", first["start"].get("date")))

    if len(events) == 1:
        return f"1 meeting today: {first_title} at {first_time}"
    return f"{len(events)} meetings today, starting with {first_title} at {first_time}"
