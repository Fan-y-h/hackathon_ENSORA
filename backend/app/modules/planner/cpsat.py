"""Optional OR-Tools CP-SAT scheduler (docs/SMART_PLANNER.md § CP-SAT evolution, T26).

Same inputs/outputs as the greedy ``scheduling.schedule``. Time is discretized into
15-minute slots; each need gets a few optional chunks, each chunk is placed inside one free
segment (a free interval clipped to one local day). Hard constraints: no overlap, daily and
weekly caps, per-need daily spread, min/max session, deadline, earliest start. Objective:
maximize priority-weighted scheduled minutes, slightly preferring earlier slots and
previous slots (low churn). Bounded solve time; the caller compares with the greedy
baseline and falls back when the solver returns no better feasible solution.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.planner.domain import Interval, clip, local_day_interval, subtract
from app.modules.planner.scheduling import (
    Kept,
    Limits,
    Need,
    Placed,
    ScheduleResult,
    Unscheduled,
    _week_key,
    priority,
)

ALGORITHM_VERSION = "cpsat-v1"
SLOT_MIN = 15
MAX_CHUNKS_PER_NEED = 8


def available() -> bool:
    try:
        from ortools.sat.python import cp_model  # noqa: F401
    except ImportError:
        return False
    return True


def _align_up(dt: datetime, base: datetime) -> datetime:
    slots = math.ceil((dt - base).total_seconds() / 60 / SLOT_MIN)
    return base + timedelta(minutes=slots * SLOT_MIN)


def schedule_cpsat(
    needs: Sequence[Need],
    free: Sequence[Interval],
    kept: Sequence[Kept],
    limits: Limits,
    now: datetime,
    horizon: Interval,
    previous: dict[str, list[Interval]] | None = None,
    time_limit_s: float = 5.0,
) -> tuple[ScheduleResult | None, str]:
    from ortools.sat.python import cp_model

    if any(n.after for n in needs):
        return None, "PREREQUISITES_USE_GREEDY"
    tz = ZoneInfo(limits.timezone)
    previous = previous or {}
    if now >= horizon.end:
        return None, "EMPTY_HORIZON"
    base = _align_up(max(now, horizon.start), max(now, horizon.start).replace(minute=0, second=0, microsecond=0))
    window = Interval(base, horizon.end) if base < horizon.end else None
    if window is None:
        return None, "EMPTY_HORIZON"
    pad = timedelta(minutes=limits.break_min)
    avail = subtract(clip(free, window), [Interval(k.interval.start - pad, k.interval.end + pad) for k in kept])

    # Free segments clipped to local days and aligned to the slot grid.
    segments: list[tuple[int, int, object]] = []  # (start_slot, end_slot, local date)
    for iv in avail:
        day = iv.start.astimezone(tz).date()
        while True:
            day_iv = local_day_interval(day, tz)
            part = clip([iv], day_iv)
            for p in part:
                s = _align_up(p.start, base)
                s_slot = int((s - base).total_seconds() // 60 // SLOT_MIN)
                e_slot = int((p.end - base).total_seconds() // 60 // SLOT_MIN)
                if e_slot > s_slot:
                    segments.append((s_slot, e_slot, day))
            if day_iv.end >= iv.end:
                break
            day += timedelta(days=1)

    min_slots = math.ceil(limits.min_session_min / SLOT_MIN)
    max_slots = limits.max_session_min // SLOT_MIN
    if max_slots < min_slots or not segments:
        return None, "NO_SEGMENTS"

    def slot_of(dt: datetime | None, default: int) -> int:
        if dt is None:
            return default
        return int(math.floor((dt - base).total_seconds() / 60 / SLOT_MIN))

    total_slots = max(e for _, e, _ in segments)
    kept_day: dict = defaultdict(int)
    kept_week: dict = defaultdict(int)
    for k in kept:
        d = k.interval.start.astimezone(tz).date()
        kept_day[d] += k.counted_min
        kept_week[_week_key(d)] += k.counted_min

    model = cp_model.CpModel()
    feasible_min = {n.key: sum(e - s for s, e, _ in segments) * SLOT_MIN for n in needs}
    prios = {n.key: priority(n, now, feasible_min[n.key]) for n in needs}
    chunks: dict[str, list] = defaultdict(list)  # need → [(presence, size, start, seg_idx)]
    intervals = []
    day_terms: dict = defaultdict(list)
    week_terms: dict = defaultdict(list)
    need_day_terms: dict = defaultdict(list)
    objective = []
    prev_slots = {
        key: {slot_of(iv.start, -1) for iv in ivs} for key, ivs in previous.items()
    }

    for n in needs:
        if n.remaining_min <= 0 or (n.deadline is not None and n.deadline <= now):
            continue
        need_slots = math.ceil(max(n.remaining_min, limits.min_session_min) / SLOT_MIN)
        if not n.splittable and need_slots > max_slots:
            continue
        lo = max(0, slot_of(n.earliest, 0) if n.earliest and n.earliest > base else 0)
        if n.earliest and n.earliest > base:
            lo = math.ceil((n.earliest - base).total_seconds() / 60 / SLOT_MIN)
        hi = total_slots if n.deadline is None else min(total_slots, slot_of(n.deadline, total_slots))
        k_chunks = 1 if not n.splittable else min(MAX_CHUNKS_PER_NEED, math.ceil(need_slots / max(1, min_slots)))
        weight = max(1, int(round(prios[n.key] * 1000)))
        for c in range(k_chunks):
            for si, (s0, e0, day) in enumerate(segments):
                s_lo, s_hi = max(s0, lo), min(e0, hi)
                if s_hi - s_lo < min_slots:
                    continue
                pres = model.NewBoolVar(f"p_{n.key}_{c}_{si}")
                if n.splittable:
                    size = model.NewIntVar(0, min(max_slots, s_hi - s_lo), f"z_{n.key}_{c}_{si}")
                    model.Add(size >= min_slots).OnlyEnforceIf(pres)
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                else:
                    size = model.NewIntVar(0, need_slots, f"z_{n.key}_{c}_{si}")
                    model.Add(size == need_slots).OnlyEnforceIf(pres)
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                start = model.NewIntVar(s_lo, s_hi, f"s_{n.key}_{c}_{si}")
                end = model.NewIntVar(s_lo, s_hi, f"e_{n.key}_{c}_{si}")
                iv = model.NewOptionalIntervalVar(start, size, end, pres, f"i_{n.key}_{c}_{si}")
                intervals.append(iv)
                chunks[n.key].append((pres, size, start, si))
                day_terms[day].append(size)
                week_terms[_week_key(day)].append(size)
                need_day_terms[(n.key, day)].append(size)
                objective.append(weight * 100 * size)
                objective.append(-start)  # prefer earlier
                if s0 in prev_slots.get(n.key, set()):
                    stay = model.NewBoolVar(f"keep_{n.key}_{c}_{si}")
                    model.Add(start == s0).OnlyEnforceIf(stay)
                    model.AddImplication(stay, pres)
                    objective.append(50 * stay)
        if chunks[n.key]:
            model.Add(sum(sz for _, sz, _, _ in chunks[n.key]) <= need_slots)
            if not n.splittable:
                model.Add(sum(p for p, _, _, _ in chunks[n.key]) <= 1)
    if not intervals:
        return None, "NOTHING_PLACEABLE"
    model.AddNoOverlap(intervals)
    for day, terms in day_terms.items():
        room = max(0, limits.daily_max_min - kept_day[day]) // SLOT_MIN
        model.Add(sum(terms) <= room)
    for wk, terms in week_terms.items():
        room = max(0, limits.weekly_max_min - kept_week[wk]) // SLOT_MIN
        model.Add(sum(terms) <= room)
    for _, terms in need_day_terms.items():
        model.Add(sum(terms) <= max_slots)
    model.Maximize(sum(objective))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = 4
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, status_name

    placed: list[Placed] = []
    placed_min: dict[str, int] = defaultdict(int)
    for key, items in chunks.items():
        for pres, size, start, _ in items:
            if solver.Value(pres):
                s = base + timedelta(minutes=solver.Value(start) * SLOT_MIN)
                m = solver.Value(size) * SLOT_MIN
                placed.append(Placed(key, Interval(s, s + timedelta(minutes=m)), prios[key]))
                placed_min[key] += m
    placed.sort(key=lambda p: (p.interval, p.need_key))
    unscheduled = []
    for n in needs:
        rest = n.remaining_min - placed_min[n.key]
        if rest <= 0:
            continue
        if n.deadline is not None and n.deadline <= now:
            unscheduled.append(Unscheduled(n.key, n.remaining_min, "DEADLINE_PASSED"))
        elif not n.splittable and n.remaining_min > limits.max_session_min:
            unscheduled.append(Unscheduled(n.key, rest, "EXCEEDS_MAX_SESSION"))
        else:
            unscheduled.append(Unscheduled(n.key, rest, "CAPACITY" if n.report_unscheduled else "AFTER_HORIZON"))
    load: dict = defaultdict(int)
    for k in kept:
        load[k.interval.start.astimezone(tz).date()] += k.counted_min
    for p in placed:
        load[p.interval.start.astimezone(tz).date()] += p.minutes
    return ScheduleResult(placed, unscheduled, prios, [], dict(sorted(load.items()))), status_name


def weighted_coverage(result: ScheduleResult) -> float:
    return sum(p.minutes * result.priorities.get(p.need_key, 0.0) for p in result.placed)
