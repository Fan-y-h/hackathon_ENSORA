from datetime import UTC, datetime, timedelta

from app.modules.planner.domain import Interval
from app.modules.planner.scheduling import (
    Kept,
    Limits,
    Need,
    check_invariants,
    schedule,
)

NOW = datetime(2026, 12, 1, 8, 0, tzinfo=UTC)  # Tuesday 09:00 Paris
HORIZON = Interval(NOW, NOW + timedelta(days=7))
LIMITS = Limits("Europe/Paris", daily_max_min=240, weekly_max_min=1200, min_session_min=25, max_session_min=90)


def at(day, h, m=0):
    return datetime(2026, 12, day, h, m, tzinfo=UTC)


def need(key, minutes, deadline=None, **kw):
    return Need(key=key, kind="assessment", title=key, activity_type="practice", remaining_min=minutes,
                earliest=None, deadline=deadline, **kw)


def run(needs, free, kept=(), limits=LIMITS, previous=None):
    res = schedule(needs, free, list(kept), limits, NOW, HORIZON, previous)
    assert check_invariants(res, needs, free, list(kept), limits, NOW) == []
    return res


def minutes_for(res, key):
    return sum(p.minutes for p in res.placed if p.need_key == key)


def test_golden_example_feasible_then_capacity_shortfall():
    exam = at(3, 8)
    today = Interval(at(1, 16), at(1, 17, 30))  # 90 min
    tomorrow = Interval(at(2, 16), at(2, 17))  # 60 min
    needs = [need("learn", 120, exam), need("review", 30, exam)]
    res = run(needs, [today, tomorrow])
    assert res.unscheduled == []
    assert minutes_for(res, "learn") + minutes_for(res, "review") >= 150

    # A 60-minute personal busy event removes tomorrow's window: 60 min unscheduled.
    res2 = run(needs, [today])
    assert sum(u.remaining_min for u in res2.unscheduled) == 60
    assert {u.reason for u in res2.unscheduled} == {"CAPACITY"}


def test_daily_cap_is_never_exceeded():
    free = [Interval(at(d, 7), at(d, 20)) for d in range(1, 8)]
    limits = Limits("Europe/Paris", 120, 1200, 25, 90)
    res = run([need("big", 600, at(5, 20)), need("other", 200, at(6, 20))], free, limits=limits)
    per_day = res.day_load
    assert max(per_day.values()) <= 120
    assert sum(u.remaining_min for u in res.unscheduled) > 0  # 800 min cannot fit before deadlines


def test_deadline_respected_and_past_deadline_reported():
    free = [Interval(at(1, 9), at(1, 18)), Interval(at(4, 9), at(4, 18))]
    res = run([need("due-wed", 60, at(2, 0)), need("late", 30, at(1, 7))], free)
    assert all(p.interval.end <= at(2, 0) for p in res.placed if p.need_key == "due-wed")
    assert [(u.need_key, u.reason) for u in res.unscheduled] == [("late", "DEADLINE_PASSED")]


def test_kept_sessions_block_time_and_count_towards_caps():
    free = [Interval(at(1, 9), at(1, 12))]
    kept = [Kept("s1", Interval(at(1, 9), at(1, 10)), 60, locked=True)]
    limits = Limits("Europe/Paris", 90, 1200, 25, 90, break_min=10)
    res = run([need("n", 120, at(1, 23))], free, kept, limits)
    assert minutes_for(res, "n") == 30  # 90 cap − 60 already locked
    assert all(p.interval.start >= at(1, 10, 10) for p in res.placed)  # configured break after kept


def test_non_splittable_task_needs_one_contiguous_slot():
    free = [Interval(at(1, 9), at(1, 9, 50)), Interval(at(1, 14), at(1, 15, 30))]
    res = run([need("block", 80, at(1, 23), splittable=False)], free)
    assert [(p.interval.start, p.minutes) for p in res.placed] == [(at(1, 14), 80)]
    res = run([need("huge", 200, None, splittable=False)], free)
    assert res.unscheduled[0].reason == "EXCEEDS_MAX_SESSION"


def test_short_need_uses_one_minimum_session():
    res = run([need("tiny", 10, None)], [Interval(at(1, 9), at(1, 12))])
    assert [p.minutes for p in res.placed] == [25]


def test_deterministic_and_sticky_previous_slots():
    free = [Interval(at(d, 9), at(d, 18)) for d in range(1, 6)]
    needs = [need("a", 120, at(5, 18)), need("b", 90, at(4, 18), importance=0.9)]
    first, second = run(needs, free), run(needs, free)
    assert first.placed == second.placed
    prev = {"a": [Interval(at(3, 14), at(3, 15, 30))]}
    sticky = run(needs, free, previous=prev)
    assert any(p.need_key == "a" and p.interval.start == at(3, 14) for p in sticky.placed)


def test_academic_signal_changes_rank_not_constraints():
    free = [Interval(at(1, 9), at(1, 10, 30))]  # room for a single 90-min session
    plain = [need("x", 90, at(6, 0), subject_id="x"), need("y", 90, at(6, 0), subject_id="y")]
    res = run(plain, free)
    assert [p.need_key for p in res.placed] == ["x"]  # stable key tie-break
    boosted = [plain[0], need("y", 90, at(6, 0), subject_id="y", academic=1.0)]
    res = run(boosted, free)
    assert [p.need_key for p in res.placed] == ["y"]
    assert sum(p.minutes for p in res.placed) == 90


# ---- T26: CP-SAT adapter -------------------------------------------------------------------
import pytest  # noqa: E402

from app.modules.planner import cpsat  # noqa: E402

needs_cpsat = pytest.mark.skipif(not cpsat.available(), reason="ortools not installed")


@needs_cpsat
def test_cpsat_respects_all_hard_constraints_and_beats_or_matches_greedy():
    free = [Interval(at(d, 13), at(d, 17)) for d in range(1, 8)]
    limits = Limits("Europe/Paris", 150, 600, 30, 90)
    needs = [need("a", 240, at(4, 17)), need("b", 200, at(6, 17), importance=0.9),
             need("c", 300, None, report_unscheduled=False), need("d", 60, at(2, 17), splittable=False)]
    res, status = cpsat.schedule_cpsat(needs, free, [], limits, NOW, HORIZON, time_limit_s=5)
    assert status in ("OPTIMAL", "FEASIBLE") and res is not None
    assert check_invariants(res, needs, free, [], limits, NOW) == []
    greedy = run(needs, free, limits=limits)
    assert cpsat.weighted_coverage(res) >= cpsat.weighted_coverage(greedy) * 0.95
    assert sum(p.minutes for p in res.placed if p.need_key == "d") in (0, 60)  # non-splittable: all or nothing


@needs_cpsat
def test_cpsat_golden_example():
    exam = at(3, 8)
    needs = [need("learn", 120, exam), need("review", 30, exam)]
    res, _ = cpsat.schedule_cpsat(needs, [Interval(at(1, 16), at(1, 17, 30)), Interval(at(2, 16), at(2, 17))],
                                  [], LIMITS, NOW, HORIZON)
    assert res is not None and res.unscheduled == []
