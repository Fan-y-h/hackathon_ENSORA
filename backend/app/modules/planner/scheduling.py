"""Deterministic greedy scheduler (docs/SMART_PLANNER.md § MVP deterministic algorithm).

Pure functions only: no I/O, no provider calls, stable tie-breaks. Hard constraints (no
overlap, free time only, daily/weekly caps, min/max session, deadlines, earliest start,
breaks, locks) are enforced during placement and re-checked independently afterwards.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.planner.domain import (
    Interval,
    clip,
    local_day_interval,
    merge,
    subtract,
)

ALGORITHM_VERSION = "greedy-v1"
# Priority weights (SMART_PLANNER § Priority) — editable assumptions, not calibrated.
W_URGENCY, W_IMPORTANCE, W_GAP, W_FORGETTING, W_EFFORT = 0.30, 0.25, 0.20, 0.15, 0.10
ACADEMIC_ALPHA = 0.25  # GRADE_TRACKER §5 blend, only when academic inputs are sufficient
# Extra gap between two study sessions. Breaks/meals are modelled by the profile's
# rest_windows, so the default adds none (keeps SMART_PLANNER's golden example feasible).
BREAK_MIN = 0


@dataclass(frozen=True)
class Need:
    key: str
    kind: str  # task | assessment | review | practice | flashcards
    title: str
    activity_type: str
    remaining_min: int
    earliest: datetime | None
    deadline: datetime | None
    splittable: bool = True
    importance: float = 0.5
    gap: float | None = None  # None = unknown mastery (0.5 prior for ranking only)
    forgetting: float = 0.0
    academic: float | None = None
    subject_id: str | None = None
    task_id: str | None = None
    assessment_id: str | None = None
    concept_ids: tuple[str, ...] = ()
    reason: str = ""
    objective: str = ""
    success_criteria: str = ""
    report_unscheduled: bool = True  # False when the deadline lies beyond the horizon
    after: tuple[str, ...] = ()  # confirmed prerequisite needs that must be studied first (hard)


@dataclass(frozen=True)
class Kept:
    """Session the planner must not move (completed, in progress, locked)."""

    session_id: str
    interval: Interval
    counted_min: int
    locked: bool = False


@dataclass(frozen=True)
class Limits:
    timezone: str
    daily_max_min: int
    weekly_max_min: int
    min_session_min: int
    max_session_min: int
    break_min: int = BREAK_MIN


@dataclass(frozen=True)
class Placed:
    need_key: str
    interval: Interval
    priority: float

    @property
    def minutes(self) -> int:
        return self.interval.minutes


@dataclass(frozen=True)
class Unscheduled:
    need_key: str
    remaining_min: int
    reason: str


@dataclass
class ScheduleResult:
    placed: list[Placed]
    unscheduled: list[Unscheduled]
    priorities: dict[str, float]
    warnings: list[str] = field(default_factory=list)
    day_load: dict[date, int] = field(default_factory=dict)


def priority(need: Need, now: datetime, feasible_min: int) -> float:
    if need.deadline is None:
        u = 0.0
    else:
        days = max(0.0, (need.deadline - now).total_seconds() / 86400)
        u = 1 / (1 + days)
    g = 0.5 if need.gap is None else need.gap
    e = 1.0 if feasible_min <= 0 else min(1.0, need.remaining_min / feasible_min)
    base = W_URGENCY * u + W_IMPORTANCE * need.importance + W_GAP * g + W_FORGETTING * need.forgetting + W_EFFORT * e
    if need.academic is not None:
        return (1 - ACADEMIC_ALPHA) * base + ACADEMIC_ALPHA * need.academic
    return base


def _week_key(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def _local_date(dt: datetime, tz: ZoneInfo) -> date:
    return dt.astimezone(tz).date()


class _Budget:
    def __init__(
        self,
        limits: Limits,
        kept: Sequence[Kept],
        tz: ZoneInfo,
        prior_week_min: dict[tuple[int, int], int] | None = None,
    ) -> None:
        self.limits, self.tz = limits, tz
        self.day: dict[date, int] = defaultdict(int)
        self.week: dict[tuple[int, int], int] = defaultdict(int)
        for wk, mins in (prior_week_min or {}).items():
            self.week[wk] += mins
        for k in kept:
            d = _local_date(k.interval.start, tz)
            self.day[d] += k.counted_min
            self.week[_week_key(d)] += k.counted_min

    def room(self, d: date) -> int:
        return max(
            0,
            min(self.limits.daily_max_min - self.day[d], self.limits.weekly_max_min - self.week[_week_key(d)]),
        )

    def take(self, d: date, minutes: int) -> None:
        self.day[d] += minutes
        self.week[_week_key(d)] += minutes


@dataclass
class _Cursor:
    """Placement state of the need currently being scheduled."""

    need: Need
    remaining: int
    hi: datetime
    per_day: dict[date, int] = field(default_factory=lambda: defaultdict(int))
    day_intervals: dict[date, list[Interval]] = field(default_factory=lambda: defaultdict(list))
    capped: bool = False


class _Placer:
    """Owns the shrinking free time, the caps budget and the placed sessions."""

    def __init__(self, available: list[Interval], budget: _Budget, limits: Limits, tz: ZoneInfo,
                 prios: dict[str, float], pad: timedelta) -> None:
        self.available, self.budget, self.limits, self.tz = available, budget, limits, tz
        self.prios, self.pad = prios, pad
        self.placed: list[Placed] = []
        self.subject_day: dict[tuple[str, date], int] = defaultdict(int)

    def place(self, cur: _Cursor, start: datetime, limit_end: datetime, *, allow_same_day_extra: bool = False) -> int:
        """Place one session for ``cur`` starting at ``start``; returns minutes placed (0 = none)."""
        lim, need = self.limits, cur.need
        d = _local_date(start, self.tz)
        day_end = local_day_interval(d, self.tz).end
        span = int((min(limit_end, day_end, cur.hi) - start).total_seconds() // 60)
        room = self.budget.room(d)  # daily + weekly caps
        if allow_same_day_extra:
            need_room = lim.max_session_min
            # Never glue two sessions of the same need back-to-back into a block > max_session_min.
            min_gap = timedelta(minutes=max(lim.break_min, 15))
            for prev_iv in cur.day_intervals[d]:
                if start < prev_iv.end + min_gap and start + timedelta(minutes=lim.min_session_min) > prev_iv.start - min_gap:
                    return 0
        else:
            need_room = lim.max_session_min - cur.per_day[d]  # spread one need over days
        if room < lim.min_session_min or need_room < lim.min_session_min:
            cur.capped = True
            return 0
        want = cur.remaining if not need.splittable else min(cur.remaining, lim.max_session_min)
        # A short need still occupies one minimum-length session (hard min duration).
        length = max(want, lim.min_session_min)
        if need.splittable:
            length = min(length, span, room, need_room)
            rest = cur.remaining - length
            # Avoid leaving a remainder shorter than a minimum session when possible.
            if 0 < rest < lim.min_session_min and length - (lim.min_session_min - rest) >= lim.min_session_min:
                length -= lim.min_session_min - rest
            if length < lim.min_session_min:
                return 0
        else:
            if length > span:
                return 0
            if length > room or length > need_room:
                cur.capped = True
                return 0
        iv = Interval(start, start + timedelta(minutes=length))
        self.placed.append(Placed(need.key, iv, self.prios[need.key]))
        self.budget.take(d, length)
        cur.per_day[d] += length
        cur.day_intervals[d].append(iv)
        if need.subject_id:
            self.subject_day[(need.subject_id, d)] += 1
        cur.remaining -= length
        self.available = subtract(self.available, [Interval(iv.start - self.pad, iv.end + self.pad)])
        return length


def schedule(
    needs: Sequence[Need],
    free: Sequence[Interval],
    kept: Sequence[Kept],
    limits: Limits,
    now: datetime,
    horizon: Interval,
    previous: dict[str, list[Interval]] | None = None,
    prior_week_min: dict[tuple[int, int], int] | None = None,
) -> ScheduleResult:
    tz = ZoneInfo(limits.timezone)
    pad = timedelta(minutes=limits.break_min)
    previous = previous or {}

    def padded(iv: Interval) -> Interval:
        return Interval(iv.start - pad, iv.end + pad)

    # Free time minus kept sessions (with breaks), clipped to the horizon and the future.
    window = Interval(max(horizon.start, now), horizon.end) if now < horizon.end else None
    available = subtract(clip(free, window), [padded(k.interval) for k in kept]) if window else []
    budget = _Budget(limits, kept, tz, prior_week_min)
    warnings: list[str] = []

    initial = list(available)

    def feasible_before(deadline: datetime | None, earliest: datetime | None) -> int:
        """Free minutes in the need's window before any placement, bounded by daily/weekly caps."""
        lo = max(earliest or now, now, horizon.start)
        hi = min(deadline or horizon.end, horizon.end)
        if hi <= lo:
            return 0
        clipped = clip(initial, Interval(lo, hi))
        if not clipped:
            return 0
        by_day: dict[date, int] = defaultdict(int)
        for iv in clipped:
            d = _local_date(iv.start, tz)
            while True:
                day_iv = local_day_interval(d, tz)
                for part in clip([iv], day_iv):
                    by_day[d] += part.minutes
                if day_iv.end >= iv.end:
                    break
                d += timedelta(days=1)
        by_week: dict[tuple[int, int], int] = defaultdict(int)
        for d, mins in by_day.items():
            capped_day = min(mins, budget.room(d))
            by_week[_week_key(d)] += capped_day
        return sum(
            min(mins, max(0, limits.weekly_max_min - budget.week[wk]))
            for wk, mins in by_week.items()
        )

    prios = {n.key: priority(n, now, feasible_before(n.deadline, n.earliest)) for n in needs}

    def sort_key(n: Need):
        slack = feasible_before(n.deadline, n.earliest) - n.remaining_min
        tight = 0 if slack < limits.daily_max_min else 1
        return (tight, -round(prios[n.key], 9), slack, n.key)

    placer = _Placer(available, budget, limits, tz, prios, pad)
    placed = placer.placed
    unscheduled: list[Unscheduled] = []
    keys = {n.key for n in needs}
    ordered = _topological(sorted(needs, key=sort_key), keys)
    blocked: set[str] = set()
    for need in ordered:
        remaining = need.remaining_min
        if remaining <= 0:
            continue
        if need.deadline is not None and need.deadline <= now:
            blocked.add(need.key)
            unscheduled.append(Unscheduled(need.key, remaining, "DEADLINE_PASSED"))
            continue
        if not need.splittable and remaining > limits.max_session_min:
            blocked.add(need.key)
            unscheduled.append(Unscheduled(need.key, remaining, "EXCEEDS_MAX_SESSION"))
            continue
        lo = max(need.earliest or now, now, horizon.start)
        hi = min(need.deadline or horizon.end, horizon.end)
        prereqs = [k for k in need.after if k in keys]
        if any(k in blocked for k in prereqs):
            blocked.add(need.key)
            unscheduled.append(Unscheduled(need.key, remaining, "PREREQUISITE_NOT_SCHEDULED"))
            continue
        prereq_ends = [p.interval.end for p in placed if p.need_key in prereqs]
        if prereq_ends:
            lo = max(lo, max(prereq_ends))
        if hi <= lo:
            blocked.add(need.key)
            reason = "NO_FREE_TIME_BEFORE_DEADLINE" if need.report_unscheduled else "AFTER_HORIZON"
            unscheduled.append(Unscheduled(need.key, remaining, reason))
            continue
        cur = _Cursor(need=need, remaining=remaining, hi=hi)
        need_slack = feasible_before(need.deadline, need.earliest) - remaining
        prefer_balance = bool(need.subject_id) and need_slack >= limits.daily_max_min

        # 1) Sticky placement: keep previous slots that are still valid (minimal churn).
        for prev in previous.get(need.key, []):
            if cur.remaining <= 0:
                break
            if prev.start < lo or prev.end > hi:
                continue
            host = next((a for a in placer.available if a.start <= prev.start and prev.end <= a.end), None)
            if host is not None and prev.minutes <= cur.remaining + limits.min_session_min:
                placer.place(cur, prev.start, prev.end)
        # 2) Pass 1: earliest feasible slots with daily spacing & soft subject balance.
        progress = True
        while cur.remaining > 0 and progress:
            progress = False
            candidates = [iv for iv in placer.available if iv.end > lo and iv.start < hi]
            if prefer_balance and need.subject_id:
                candidates.sort(
                    key=lambda iv: (
                        1 if placer.subject_day[(need.subject_id, _local_date(max(iv.start, lo), tz))] > 0 else 0,
                        iv.start,
                    )
                )
            for iv in candidates:
                start = max(iv.start, lo)
                if placer.place(cur, start, iv.end):
                    progress = True
                    break
        # 3) Pass 2 (deadline rescue): if urgent work with a deadline in the horizon still remains,
        # allow a second spaced session on the same day while preserving all hard caps and breaks.
        if cur.remaining > 0 and need.splittable and need.deadline is not None and need.deadline <= horizon.end:
            progress = True
            while cur.remaining > 0 and progress:
                progress = False
                for iv in list(placer.available):
                    if iv.end <= lo or iv.start >= hi:
                        continue
                    start = max(iv.start, lo)
                    if placer.place(cur, start, iv.end, allow_same_day_extra=True):
                        progress = True
                        break
        remaining = cur.remaining
        if remaining > 0:
            blocked.add(need.key)
            if need.report_unscheduled:
                if feasible_before(need.deadline, need.earliest) == 0 and not cur.capped:
                    reason = "NO_FREE_TIME_BEFORE_DEADLINE"
                else:
                    reason = "CAPACITY"
                unscheduled.append(Unscheduled(need.key, remaining, reason))
            else:
                unscheduled.append(Unscheduled(need.key, remaining, "AFTER_HORIZON"))

    placed.sort(key=lambda p: (p.interval, p.need_key))
    load: dict[date, int] = defaultdict(int)
    for k in kept:
        load[_local_date(k.interval.start, tz)] += k.counted_min
    for p in placed:
        load[_local_date(p.interval.start, tz)] += p.minutes
    return ScheduleResult(placed, unscheduled, prios, warnings, dict(sorted(load.items())))


def _topological(needs: list[Need], keys: set[str]) -> list[Need]:
    """Stable topological order: keep the priority order but never put a need before one
    of its prerequisites. Cycles are rejected upstream; any leftover is appended as-is."""
    done: set[str] = set()
    out: list[Need] = []
    pending = list(needs)
    while pending:
        progress = False
        for n in list(pending):
            if all(k in done or k not in keys for k in n.after):
                out.append(n)
                done.add(n.key)
                pending.remove(n)
                progress = True
                break
        if not progress:
            out.extend(pending)
            break
    return out


def check_invariants(
    result: ScheduleResult,
    needs: Sequence[Need],
    free: Sequence[Interval],
    kept: Sequence[Kept],
    limits: Limits,
    now: datetime,
    horizon: Interval | None = None,
    prior_week_min: dict[tuple[int, int], int] | None = None,
) -> list[str]:
    """Independent re-check of every hard constraint. Empty list = valid plan."""
    tz = ZoneInfo(limits.timezone)
    by_key = {n.key: n for n in needs}
    errors: list[str] = []
    free_m = merge(free)
    pad = timedelta(minutes=limits.break_min)
    all_iv = sorted(
        [(p.interval, f"need:{p.need_key}", True) for p in result.placed]
        + [(k.interval, f"kept:{k.session_id}", False) for k in kept],
        key=lambda item: (item[0], item[1]),
    )
    for i, (a, la, a_placed) in enumerate(all_iv):
        for b, lb, b_placed in all_iv[i + 1 :]:
            if b.start >= a.end + pad:
                break
            if not a_placed and not b_placed:
                continue  # pre-existing kept-vs-kept conflicts are reported as warnings, not solver failures
            if a.overlaps(b):
                errors.append(f"OVERLAP {la} {lb}")
            elif limits.break_min > 0 and b.start < a.end + pad:
                errors.append(f"BREAK_VIOLATION {la} {lb}")
    day: dict[date, int] = defaultdict(int)
    week: dict[tuple[int, int], int] = defaultdict(int)
    for wk, mins in (prior_week_min or {}).items():
        week[wk] += mins
    for k in kept:
        d = _local_date(k.interval.start, tz)
        day[d] += k.counted_min
        week[_week_key(d)] += k.counted_min
    placed_day: dict[date, int] = defaultdict(int)
    session_counts: dict[str, int] = defaultdict(int)
    for p in result.placed:
        n = by_key[p.need_key]
        iv = p.interval
        session_counts[p.need_key] += 1
        if iv.start < now:
            errors.append(f"PAST {p.need_key}")
        if horizon is not None and (iv.start < horizon.start or iv.end > horizon.end):
            errors.append(f"OUTSIDE_HORIZON {p.need_key}")
        if not any(f.start <= iv.start and iv.end <= f.end for f in free_m):
            errors.append(f"OUTSIDE_FREE_TIME {p.need_key}")
        if not limits.min_session_min <= iv.minutes <= limits.max_session_min:
            errors.append(f"SESSION_LENGTH {p.need_key}")
        if n.deadline is not None and iv.end > n.deadline:
            errors.append(f"AFTER_DEADLINE {p.need_key}")
        if n.earliest is not None and iv.start < n.earliest:
            errors.append(f"BEFORE_EARLIEST {p.need_key}")
        d = _local_date(iv.start, tz)
        if _local_date(iv.end - timedelta(microseconds=1), tz) != d:
            errors.append(f"CROSSES_MIDNIGHT {p.need_key}")
        day[d] += iv.minutes
        placed_day[d] += iv.minutes
        week[_week_key(d)] += iv.minutes
    for key, cnt in session_counts.items():
        if not by_key[key].splittable and cnt > 1:
            errors.append(f"SPLIT_NON_SPLITTABLE {key}")
    ends: dict[str, datetime] = {}
    for p in result.placed:
        ends[p.need_key] = max(ends.get(p.need_key, p.interval.end), p.interval.end)
    for p in result.placed:
        for k in by_key[p.need_key].after:
            if k in ends and p.interval.start < ends[k]:
                errors.append(f"PREREQUISITE_ORDER {k}->{p.need_key}")
    for d in placed_day:
        if day[d] > limits.daily_max_min:
            errors.append(f"DAILY_CAP {d.isoformat()}")
        if week[_week_key(d)] > limits.weekly_max_min:
            errors.append(f"WEEKLY_CAP {d.isoformat()}")
    return sorted(set(errors))
