"""Pure agenda / availability logic (no FastAPI or SQLAlchemy imports).

Intervals are half-open ``[start, end)`` aware UTC datetimes. Weekly windows are expanded
per local calendar date in the user's timezone and only then converted to UTC, so DST
transitions shift UTC times correctly (docs/SMART_PLANNER.md § Inputs).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.modules.account.domain import PlanningPreferences, WeeklyWindow

EVENT_TYPES = ("class", "td", "tp", "exam", "presentation", "deadline", "personal", "other")
ASSESSMENT_EVENT_TYPES = frozenset({"exam", "presentation"})
NON_BUSY_BY_DEFAULT = frozenset({"deadline"})
EVENT_STATUSES = ("confirmed", "tentative", "cancelled")
MAX_EVENT_DURATION = timedelta(days=14)
MAX_AVAILABILITY_RANGE = timedelta(days=31)


@dataclass(frozen=True, order=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("intervals require aware datetimes")
        if self.end <= self.start:
            raise ValueError("interval end must be after start")

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)

    def overlaps(self, other: Interval) -> bool:
        return self.start < other.end and other.start < self.end


def default_busy(event_type: str) -> bool:
    return event_type not in NON_BUSY_BY_DEFAULT


def creates_assessment(event_type: str) -> bool:
    return event_type in ASSESSMENT_EVENT_TYPES


def merge(intervals: Iterable[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for iv in sorted(intervals):
        if out and iv.start <= out[-1].end:
            if iv.end > out[-1].end:
                out[-1] = Interval(out[-1].start, iv.end)
        else:
            out.append(iv)
    return out


def subtract(base: Iterable[Interval], remove: Iterable[Interval]) -> list[Interval]:
    removed = merge(remove)
    result: list[Interval] = []
    for iv in merge(base):
        pieces = [iv]
        for r in removed:
            if r.start >= iv.end:
                break
            next_pieces: list[Interval] = []
            for p in pieces:
                if not p.overlaps(r):
                    next_pieces.append(p)
                    continue
                if p.start < r.start:
                    next_pieces.append(Interval(p.start, r.start))
                if r.end < p.end:
                    next_pieces.append(Interval(r.end, p.end))
            pieces = next_pieces
        result.extend(pieces)
    return result


def clip(intervals: Iterable[Interval], window: Interval) -> list[Interval]:
    out = []
    for iv in intervals:
        s, e = max(iv.start, window.start), min(iv.end, window.end)
        if s < e:
            out.append(Interval(s, e))
    return out


def local_day_interval(day: date, tz: ZoneInfo) -> Interval:
    start = datetime.combine(day, time(0), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    return Interval(start.astimezone(UTC), end.astimezone(UTC))


def _local_dates(start: datetime, end: datetime, tz: ZoneInfo) -> list[date]:
    first = start.astimezone(tz).date() - timedelta(days=1)
    last = end.astimezone(tz).date() + timedelta(days=1)
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _wall_clock(day: date, minute_of_day: int, tz: ZoneInfo) -> datetime:
    """UTC instant of local wall-clock ``minute_of_day`` (0–1440) on ``day``."""
    if minute_of_day == 1440:
        local = datetime.combine(day + timedelta(days=1), time(0), tzinfo=tz)
    else:
        local = datetime.combine(day, time(minute_of_day // 60, minute_of_day % 60), tzinfo=tz)
    return local.astimezone(UTC)


def expand_weekly(
    windows: Sequence[WeeklyWindow], tz: ZoneInfo, range_: Interval
) -> list[Interval]:
    """Concrete UTC intervals for each weekly local window inside ``range_``."""
    out: list[Interval] = []
    for day in _local_dates(range_.start, range_.end, tz):
        for w in windows:
            if w.weekday != day.weekday():
                continue
            # Build from wall-clock time so DST days keep the declared local hours.
            su = _wall_clock(day, w.start_min, tz)
            eu = _wall_clock(day, w.end_min, tz)
            if su < eu:
                out.extend(clip([Interval(su, eu)], range_))
    return merge(out)


@dataclass(frozen=True)
class BusyBlock:
    event_id: str
    interval: Interval


@dataclass(frozen=True)
class DayLoad:
    date: date
    free_min: int
    daily_max_min: int

    @property
    def plannable_min(self) -> int:
        return min(self.free_min, self.daily_max_min)


@dataclass(frozen=True)
class AvailabilitySnapshot:
    range: Interval
    timezone: str
    free: list[Interval]
    busy: list[BusyBlock]
    days: list[DayLoad]
    warnings: list[str] = field(default_factory=list)


def compute_availability(
    prefs: PlanningPreferences,
    busy: Sequence[BusyBlock],
    range_: Interval,
    now: datetime,
) -> AvailabilitySnapshot:
    """Free study time = work windows − rest windows − busy events − past − short gaps."""
    if range_.end - range_.start > MAX_AVAILABILITY_RANGE:
        raise ValueError("availability range is limited to 31 days")
    tz = ZoneInfo(prefs.timezone)
    warnings: list[str] = []
    if not prefs.work_windows:
        warnings.append("NO_WORK_WINDOWS")

    work = expand_weekly(prefs.work_windows, tz, range_)
    rest = expand_weekly(prefs.rest_windows, tz, range_)
    past: list[Interval] = []
    if now > range_.start:
        past.append(Interval(range_.start, min(now, range_.end)))
    free = subtract(work, [*rest, *(b.interval for b in busy), *past])
    # A gap shorter than a minimum session cannot host any study session.
    free = [iv for iv in free if iv.minutes >= prefs.min_session_min]

    days: list[DayLoad] = []
    for day in _local_dates(range_.start, range_.end, tz)[1:-1]:
        day_iv = local_day_interval(day, tz)
        if not day_iv.overlaps(range_):
            continue
        minutes = sum(iv.minutes for iv in clip(free, day_iv))
        days.append(DayLoad(day, minutes, prefs.daily_max_min))

    return AvailabilitySnapshot(
        range=range_,
        timezone=prefs.timezone,
        free=free,
        busy=sorted(busy, key=lambda b: (b.interval, b.event_id)),
        days=days,
        warnings=warnings,
    )


def event_interval(
    start_at: datetime | None,
    end_at: datetime | None,
    all_day_date: date | None,
    tz: ZoneInfo,
) -> Interval:
    if all_day_date is not None:
        return local_day_interval(all_day_date, tz)
    assert start_at is not None and end_at is not None
    return Interval(start_at, end_at)
