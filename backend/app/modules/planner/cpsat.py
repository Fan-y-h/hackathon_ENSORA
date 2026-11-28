"""Optional OR-Tools CP-SAT scheduler (docs/SMART_PLANNER.md § CP-SAT evolution, T26).

Same inputs/outputs as the greedy ``scheduling.schedule``. Time is discretized into
15-minute slots; each need gets optional chunks placed inside free segments (a free
interval clipped to one local day). Hard constraints: no overlap (with ``break_min``
padding), daily and weekly caps (including prior week completed work), min/max session,
deadline, earliest start, non-splittable atomicity, and prerequisite precedence.

Soft objective:
- maximize priority-weighted scheduled minutes;
- prefer earlier slots (active chunks only, zero noise on absent chunks);
- preserve previous slots (low churn / minimal disruption);
- reward spacing a multi-session need across distinct days and balancing subjects.

Bounded solve time; never reports INFEASIBLE/UNKNOWN/MODEL_INVALID as optimal or feasible.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, datetime, timedelta
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


def _has_cycle(needs: Sequence[Need]) -> bool:
    keys = {n.key for n in needs}
    adj = {n.key: [p for p in n.after if p in keys] for n in needs}
    state: dict[str, int] = {}  # 0=unvisited, 1=visiting, 2=done

    def dfs(u: str) -> bool:
        state[u] = 1
        for v in adj.get(u, ()):
            if state.get(v) == 1:
                return True
            if state.get(v, 0) == 0 and dfs(v):
                return True
        state[u] = 2
        return False

    return any(state.get(k, 0) == 0 and dfs(k) for k in keys)


def schedule_cpsat(
    needs: Sequence[Need],
    free: Sequence[Interval],
    kept: Sequence[Kept],
    limits: Limits,
    now: datetime,
    horizon: Interval,
    previous: dict[str, list[Interval]] | None = None,
    time_limit_s: float = 5.0,
    prior_week_min: dict[tuple[int, int], int] | None = None,
) -> tuple[ScheduleResult | None, str]:
    from ortools.sat.python import cp_model

    if _has_cycle(needs):
        return None, "PREREQUISITE_CYCLE"
    tz = ZoneInfo(limits.timezone)
    previous = previous or {}
    if now >= horizon.end:
        return None, "EMPTY_HORIZON"
    start_anchor = max(now, horizon.start)
    base = _align_up(start_anchor, start_anchor.replace(minute=0, second=0, microsecond=0))
    window = Interval(base, horizon.end) if base < horizon.end else None
    if window is None:
        return None, "EMPTY_HORIZON"
    pad = timedelta(minutes=limits.break_min)
    avail = subtract(clip(free, window), [Interval(k.interval.start - pad, k.interval.end + pad) for k in kept])

    # Free segments clipped to local days and aligned to the slot grid.
    segments: list[tuple[int, int, date]] = []  # (start_slot, end_slot, local date)
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
    break_slots = math.ceil(limits.break_min / SLOT_MIN) if limits.break_min > 0 else 0
    if max_slots < min_slots or not segments:
        return None, "NO_SEGMENTS"

    def slot_floor(dt: datetime | None, default: int) -> int:
        if dt is None:
            return default
        return int(math.floor((dt - base).total_seconds() / 60 / SLOT_MIN))

    def slot_ceil(dt: datetime | None, default: int) -> int:
        if dt is None:
            return default
        return int(math.ceil((dt - base).total_seconds() / 60 / SLOT_MIN))

    total_slots = max(e for _, e, _ in segments)
    kept_day: dict[date, int] = defaultdict(int)
    kept_week: dict[tuple[int, int], int] = defaultdict(int)
    for wk, mins in (prior_week_min or {}).items():
        kept_week[wk] += mins
    for k in kept:
        d = k.interval.start.astimezone(tz).date()
        kept_day[d] += k.counted_min
        kept_week[_week_key(d)] += k.counted_min

    def feasible_min_for(n: Need) -> int:
        lo_s = max(0, slot_ceil(n.earliest, 0)) if n.earliest and n.earliest > base else 0
        hi_s = total_slots if n.deadline is None else min(total_slots, slot_floor(n.deadline, total_slots))
        if hi_s <= lo_s:
            return 0
        by_day: dict[date, int] = defaultdict(int)
        for s0, e0, d in segments:
            s_lo, s_hi = max(s0, lo_s), min(e0, hi_s)
            if s_hi - s_lo >= min_slots:
                by_day[d] += (s_hi - s_lo) * SLOT_MIN
        return sum(min(m, max(0, limits.daily_max_min - kept_day[d])) for d, m in by_day.items())

    model = cp_model.CpModel()
    feasible_by_key = {n.key: feasible_min_for(n) for n in needs}
    prios = {n.key: priority(n, now, feasible_by_key[n.key]) for n in needs}
    keys = {n.key for n in needs}
    need_slots_map: dict[str, int] = {}
    chunks: dict[str, list] = defaultdict(list)  # need -> [(pres, size, start, end, day)]
    no_overlap_intervals = []
    day_terms: dict[date, list] = defaultdict(list)
    week_terms: dict[tuple[int, int], list] = defaultdict(list)
    need_day_terms: dict[tuple[str, date], list] = defaultdict(list)
    need_day_pres: dict[tuple[str, date], list] = defaultdict(list)
    subject_day_pres: dict[tuple[str, date], list] = defaultdict(list)
    objective = []
    prev_slots = {
        key: {slot_floor(iv.start, -1) for iv in ivs} for key, ivs in previous.items()
    }

    for n in needs:
        if n.remaining_min <= 0 or (n.deadline is not None and n.deadline <= now):
            continue
        need_slots = math.ceil(max(n.remaining_min, limits.min_session_min) / SLOT_MIN)
        if not n.splittable and need_slots > max_slots:
            continue
        need_slots_map[n.key] = need_slots
        lo = max(0, slot_ceil(n.earliest, 0)) if n.earliest and n.earliest > base else 0
        hi = total_slots if n.deadline is None else min(total_slots, slot_floor(n.deadline, total_slots))
        if hi - lo < min_slots:
            continue
        weight = max(1, int(round(prios[n.key] * 1000)))
        # At most 1 chunk per segment (2 only when segment is long enough for two spaced sessions)
        # to eliminate k! chunk symmetry while retaining urgent-day flexibility.
        for si, (s0, e0, day) in enumerate(segments):
            s_lo, s_hi = max(s0, lo), min(e0, hi)
            if s_hi - s_lo < min_slots:
                continue
            if not n.splittable and s_hi - s_lo < need_slots:
                continue
            seg_copies = 1
            if n.splittable and n.deadline is not None and n.deadline <= horizon.end:
                if s_hi - s_lo >= 2 * min_slots + max(break_slots, 1):
                    seg_copies = 2
            prev_starts_here = sorted(ps for ps in prev_slots.get(n.key, set()) if s_lo <= ps <= s_hi - min_slots)
            prev_end_var = None
            for c in range(seg_copies):
                pres = model.NewBoolVar(f"p_{n.key}_{si}_{c}")
                if n.splittable:
                    size = model.NewIntVar(0, min(max_slots, s_hi - s_lo), f"z_{n.key}_{si}_{c}")
                    model.Add(size >= min_slots).OnlyEnforceIf(pres)
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                else:
                    size = model.NewIntVar(0, need_slots, f"z_{n.key}_{si}_{c}")
                    model.Add(size == need_slots).OnlyEnforceIf(pres)
                    model.Add(size == 0).OnlyEnforceIf(pres.Not())
                start = model.NewIntVar(s_lo, s_hi, f"s_{n.key}_{si}_{c}")
                end = model.NewIntVar(s_lo, s_hi, f"e_{n.key}_{si}_{c}")
                model.Add(end == start + size)
                if break_slots > 0:
                    pad_size = model.NewIntVar(0, min(max_slots, s_hi - s_lo) + break_slots, f"pz_{n.key}_{si}_{c}")
                    pad_end = model.NewIntVar(s_lo, s_hi + break_slots, f"pe_{n.key}_{si}_{c}")
                    model.Add(pad_size == size + break_slots).OnlyEnforceIf(pres)
                    model.Add(pad_size == 0).OnlyEnforceIf(pres.Not())
                    model.Add(pad_end == start + pad_size)
                    iv = model.NewOptionalIntervalVar(start, pad_size, pad_end, pres, f"i_{n.key}_{si}_{c}")
                else:
                    iv = model.NewOptionalIntervalVar(start, size, end, pres, f"i_{n.key}_{si}_{c}")
                no_overlap_intervals.append(iv)
                chunks[n.key].append((pres, size, start, end, day))
                day_terms[day].append(size)
                week_terms[_week_key(day)].append(size)
                need_day_terms[(n.key, day)].append(size)
                need_day_pres[(n.key, day)].append(pres)
                if n.subject_id:
                    subject_day_pres[(n.subject_id, day)].append(pres)
                # Symmetry break when 2 copies exist in the same segment (and enforce min gap >= 15m).
                if prev_end_var is not None:
                    model.Add(start >= prev_end_var + max(break_slots, 1)).OnlyEnforceIf(pres)
                prev_end_var = end

                # Primary objective: priority-weighted scheduled slots.
                objective.append(weight * 1000 * size)
                # Secondary: prefer earlier start ONLY when chunk is active (zero noise when absent).
                early_pen = model.NewIntVar(0, total_slots, f"ep_{n.key}_{si}_{c}")
                model.Add(early_pen >= start - total_slots * (1 - pres))
                objective.append(-early_pen)
                # Sticky previous slot bonus (low churn during replan).
                if prev_starts_here and c == 0:
                    for pi, ps in enumerate(prev_starts_here):
                        stay = model.NewBoolVar(f"keep_{n.key}_{si}_{c}_{pi}")
                        model.Add(start == ps).OnlyEnforceIf(stay)
                        model.AddImplication(stay, pres)
                        objective.append(500 * stay)

        if chunks[n.key]:
            model.Add(sum(sz for _, sz, _, _, _ in chunks[n.key]) <= need_slots)
            if not n.splittable:
                model.Add(sum(p for p, _, _, _, _ in chunks[n.key]) <= 1)
            else:
                model.Add(sum(p for p, _, _, _, _ in chunks[n.key]) <= MAX_CHUNKS_PER_NEED)

    if not no_overlap_intervals:
        return None, "NOTHING_PLACEABLE"

    # Hard no-overlap (with break padding).
    model.AddNoOverlap(no_overlap_intervals)

    # Hard daily and weekly caps.
    for day, terms in day_terms.items():
        room = max(0, limits.daily_max_min - kept_day[day]) // SLOT_MIN
        model.Add(sum(terms) <= room)
    for wk, terms in week_terms.items():
        room = max(0, limits.weekly_max_min - kept_week[wk]) // SLOT_MIN
        model.Add(sum(terms) <= room)

    # Soft/hard per-need daily spread: limit to max_slots/day for non-urgent needs;
    # for urgent deadline needs, allow extra spaced sessions with soft spacing bonus across days.
    urgent_keys = {n.key for n in needs if n.deadline is not None and n.deadline <= horizon.end}
    for (nkey, day), terms in need_day_terms.items():
        if nkey not in urgent_keys:
            model.Add(sum(terms) <= max_slots)
        day_used = model.NewBoolVar(f"nd_{nkey}_{day.isoformat()}")
        model.Add(sum(need_day_pres[(nkey, day)]) >= 1).OnlyEnforceIf(day_used)
        model.Add(sum(need_day_pres[(nkey, day)]) == 0).OnlyEnforceIf(day_used.Not())
        objective.append(25 * day_used)  # reward spacing a need across distinct days

    # Soft subject balance: gentle penalty for stacking multiple sessions of the same subject on one day.
    for (subj, day), plist in subject_day_pres.items():
        if len(plist) > 1:
            excess = model.NewIntVar(0, len(plist) - 1, f"sb_{subj}_{day.isoformat()}")
            model.Add(excess >= sum(plist) - 1)
            objective.append(-15 * excess)

    # Hard prerequisite precedence: dependent chunks start after all prerequisite chunks end,
    # and require every in-scope prerequisite to be fully scheduled.
    prereq_full: dict[str, object] = {}
    prereq_max_end: dict[str, object] = {}
    referenced_prereqs = {p for n in needs for p in n.after if p in keys}
    for pkey in referenced_prereqs:
        full_var = model.NewBoolVar(f"full_{pkey}")
        p_chunks = chunks.get(pkey, [])
        p_need_slots = need_slots_map.get(pkey, 0)
        if not p_chunks or p_need_slots <= 0:
            model.Add(full_var == 0)
            max_end = model.NewIntVar(0, total_slots, f"mend_{pkey}")
            model.Add(max_end == total_slots)
        else:
            model.Add(sum(sz for _, sz, _, _, _ in p_chunks) >= p_need_slots).OnlyEnforceIf(full_var)
            model.Add(sum(sz for _, sz, _, _, _ in p_chunks) < p_need_slots).OnlyEnforceIf(full_var.Not())
            max_end = model.NewIntVar(0, total_slots, f"mend_{pkey}")
            for pres, _, _, end, _ in p_chunks:
                model.Add(max_end >= end).OnlyEnforceIf(pres)
        prereq_full[pkey] = full_var
        prereq_max_end[pkey] = max_end

    for n in needs:
        n_chunks = chunks.get(n.key, [])
        if not n_chunks:
            continue
        for pkey in n.after:
            if pkey not in keys:
                continue
            for pres, _, start, _, _ in n_chunks:
                model.AddImplication(pres, prereq_full[pkey])
                model.Add(start >= prereq_max_end[pkey]).OnlyEnforceIf(pres)

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
        for pres, size, start, _, _ in items:
            if solver.Value(pres):
                s = base + timedelta(minutes=solver.Value(start) * SLOT_MIN)
                m = solver.Value(size) * SLOT_MIN
                placed.append(Placed(key, Interval(s, s + timedelta(minutes=m)), prios[key]))
                placed_min[key] += m
    placed.sort(key=lambda p: (p.interval, p.need_key))
    incomplete = {n.key for n in needs if n.remaining_min - placed_min[n.key] > 0}
    unscheduled = []
    for n in needs:
        rest = n.remaining_min - placed_min[n.key]
        if rest <= 0:
            continue
        if n.deadline is not None and n.deadline <= now:
            unscheduled.append(Unscheduled(n.key, n.remaining_min, "DEADLINE_PASSED"))
        elif not n.splittable and n.remaining_min > limits.max_session_min:
            unscheduled.append(Unscheduled(n.key, rest, "EXCEEDS_MAX_SESSION"))
        elif any(p in incomplete for p in n.after if p in keys):
            unscheduled.append(Unscheduled(n.key, rest, "PREREQUISITE_NOT_SCHEDULED"))
        elif not n.report_unscheduled:
            unscheduled.append(Unscheduled(n.key, rest, "AFTER_HORIZON"))
        elif feasible_by_key[n.key] == 0:
            unscheduled.append(Unscheduled(n.key, rest, "NO_FREE_TIME_BEFORE_DEADLINE"))
        else:
            unscheduled.append(Unscheduled(n.key, rest, "CAPACITY"))
    load: dict[date, int] = defaultdict(int)
    for k in kept:
        load[k.interval.start.astimezone(tz).date()] += k.counted_min
    for p in placed:
        load[p.interval.start.astimezone(tz).date()] += p.minutes
    return ScheduleResult(placed, unscheduled, prios, [], dict(sorted(load.items()))), status_name


def weighted_coverage(result: ScheduleResult) -> float:
    return sum(p.minutes * result.priorities.get(p.need_key, 0.0) for p in result.placed)
