"""Shared timezone handling for availability and calendar snapshot reconciliation."""
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def interval_time(instant, all_day, timezone_name):
    if instant:
        return datetime.fromisoformat(instant).astimezone(UTC).isoformat(timespec="microseconds")
    if all_day:
        try:
            tz = ZoneInfo(timezone_name or "Europe/Paris")
        except (ValueError, ZoneInfoNotFoundError):
            tz = ZoneInfo("Europe/Paris")
        date = datetime.fromisoformat(all_day).date()
        return datetime.combine(date, time.min, tz).astimezone(UTC).isoformat(timespec="microseconds")
    return None
