"""Pure learning-profile rules (no FastAPI / SQLAlchemy imports).

Weekly windows are expressed in the user's local time (``timezone``); the planner converts
them to UTC per concrete date so DST is handled correctly.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import time
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY


@dataclass(frozen=True, order=True)
class WeeklyWindow:
    """Half-open local window ``[start, end)`` on ``weekday`` (0 = Monday)."""

    weekday: int
    start_min: int  # minutes since local midnight
    end_min: int  # may be 1440 ("24:00")

    @property
    def minutes(self) -> int:
        return self.end_min - self.start_min

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> WeeklyWindow:
        return WeeklyWindow(int(d["weekday"]), parse_hhmm(d["start"]), parse_hhmm(d["end"]))

    def start_time(self) -> time:
        return time(self.start_min // 60, self.start_min % 60)


@dataclass(frozen=True)
class PlanningPreferences:
    """Read-only view of the profile consumed by the planner (query interface)."""

    timezone: str
    work_windows: tuple[WeeklyWindow, ...]
    rest_windows: tuple[WeeklyWindow, ...]
    daily_max_min: int
    weekly_max_min: int
    min_session_min: int
    max_session_min: int
    revision: int
    planner_algorithm: str = "greedy"
    repetition_algorithm: str = "heuristic"
    desired_retention: float = 0.9
    notification_settings: dict = field(default_factory=dict)


def parse_hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    total = int(hours) * 60 + int(minutes)
    if not (0 <= int(minutes) < 60 and 0 <= total <= MINUTES_PER_DAY):
        raise ValueError(f"invalid time {value!r}")
    return total


def is_valid_timezone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def window_overlaps(windows: Iterable[WeeklyWindow]) -> list[tuple[WeeklyWindow, WeeklyWindow]]:
    ordered = sorted(windows)
    return [
        (a, b)
        for a, b in zip(ordered, ordered[1:], strict=False)
        if a.weekday == b.weekday and b.start_min < a.end_min
    ]


def check_preferences(
    *,
    work_windows: Sequence[WeeklyWindow],
    rest_windows: Sequence[WeeklyWindow],
    daily_max_min: int,
    weekly_max_min: int,
    min_session_min: int,
    max_session_min: int,
) -> list[dict[str, str]]:
    """Cross-field consistency checks on the *merged* preferences. Returns error list."""
    errors: list[dict[str, str]] = []

    def err(name: str, code: str, msg: str) -> None:
        errors.append({"field": name, "code": code, "message": msg})

    for name, windows in (("work_windows", work_windows), ("rest_windows", rest_windows)):
        for a, _ in window_overlaps(windows):
            err(name, "overlap", f"windows overlap on weekday {a.weekday}")

    if min_session_min > max_session_min:
        err("min_session_min", "min_gt_max", "min session length exceeds max session length")
    if daily_max_min > weekly_max_min:
        err("daily_max_min", "daily_gt_weekly", "daily limit exceeds weekly limit")
    if daily_max_min < min_session_min:
        err("daily_max_min", "below_min_session", "daily limit cannot fit one minimum session")

    weekly_capacity = sum(w.minutes for w in work_windows)
    if work_windows and weekly_capacity < min_session_min:
        err("work_windows", "too_short", "work windows cannot fit one minimum session")
    if work_windows and all(w.minutes < min_session_min for w in work_windows):
        err("work_windows", "too_short", "every work window is shorter than the minimum session")
    return errors


def is_onboarding_complete(cohort: str | None, work_windows: Sequence[Any]) -> bool:
    return cohort is not None and len(work_windows) > 0
