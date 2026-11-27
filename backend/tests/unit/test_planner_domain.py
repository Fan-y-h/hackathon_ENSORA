from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.modules.account.domain import (
    PlanningPreferences,
    WeeklyWindow,
    check_preferences,
)
from app.modules.planner.domain import (
    BusyBlock,
    Interval,
    compute_availability,
    default_busy,
    expand_weekly,
    local_day_interval,
    merge,
    subtract,
)

PARIS = ZoneInfo("Europe/Paris")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def iv(h1, m1, h2, m2, day=1) -> Interval:
    return Interval(utc(2026, 12, day, h1, m1), utc(2026, 12, day, h2, m2))


def prefs(**kw) -> PlanningPreferences:
    base = dict(
        timezone="Europe/Paris",
        work_windows=(),
        rest_windows=(),
        daily_max_min=240,
        weekly_max_min=1200,
        min_session_min=25,
        max_session_min=90,
        revision=1,
    )
    return PlanningPreferences(**(base | kw))


def test_interval_is_half_open_and_rejects_empty_or_naive():
    assert not iv(8, 0, 9, 0).overlaps(iv(9, 0, 10, 0))
    with pytest.raises(ValueError):
        iv(9, 0, 9, 0)
    with pytest.raises(ValueError):
        Interval(datetime(2026, 1, 1, 8), datetime(2026, 1, 1, 9))


def test_merge_and_subtract():
    assert merge([iv(8, 0, 9, 0), iv(9, 0, 10, 0), iv(12, 0, 13, 0)]) == [
        iv(8, 0, 10, 0),
        iv(12, 0, 13, 0),
    ]
    free = subtract([iv(8, 0, 12, 0)], [iv(9, 0, 9, 30), iv(10, 0, 11, 0), iv(11, 30, 13, 0)])
    assert free == [iv(8, 0, 9, 0), iv(9, 30, 10, 0), iv(11, 0, 11, 30)]
    assert subtract([iv(8, 0, 9, 0)], [iv(7, 0, 10, 0)]) == []


def test_weekly_window_follows_local_time_across_dst():
    # Monday 09:00–10:00 Paris: CEST (UTC+2) on 19 Oct, CET (UTC+1) on 26 Oct 2026.
    window = WeeklyWindow(weekday=0, start_min=9 * 60, end_min=10 * 60)
    out = expand_weekly([window], PARIS, Interval(utc(2026, 10, 18), utc(2026, 10, 28)))
    assert out == [
        Interval(utc(2026, 10, 19, 7), utc(2026, 10, 19, 8)),
        Interval(utc(2026, 10, 26, 8), utc(2026, 10, 26, 9)),
    ]


def test_all_day_interval_is_local_midnight_to_midnight():
    day = local_day_interval(date(2026, 12, 7), PARIS)
    assert day == Interval(utc(2026, 12, 6, 23), utc(2026, 12, 7, 23))


def test_availability_subtracts_rest_busy_past_and_short_gaps():
    # Tuesday 1 Dec 2026: work 14:00–19:00 Paris (13:00–18:00Z), rest 16:00–16:15 local.
    p = prefs(
        work_windows=(WeeklyWindow(1, 14 * 60, 19 * 60),),
        rest_windows=(WeeklyWindow(1, 16 * 60, 16 * 60 + 15),),
    )
    busy = [
        BusyBlock("sport", Interval(utc(2026, 12, 1, 16, 30), utc(2026, 12, 1, 17, 40))),
    ]
    now = utc(2026, 12, 1, 13, 30)  # 30 min of the window already in the past
    paris_day = local_day_interval(date(2026, 12, 1), PARIS)
    snap = compute_availability(p, busy, paris_day, now)
    # 13:30–15:00Z (90) | 15:15–16:30Z (75) | 17:40–18:00Z (20 < 25 → dropped)
    assert snap.free == [iv(13, 30, 15, 0), iv(15, 15, 16, 30)]
    assert [(d.date, d.free_min, d.plannable_min) for d in snap.days] == [
        (date(2026, 12, 1), 165, 165)
    ]


def test_availability_daily_cap_and_missing_windows_warning():
    p = prefs(work_windows=(WeeklyWindow(1, 8 * 60, 20 * 60),), daily_max_min=120)
    snap = compute_availability(p, [], Interval(utc(2026, 12, 1), utc(2026, 12, 2)), utc(2026, 1, 1))
    assert snap.days[0].free_min == 720 and snap.days[0].plannable_min == 120
    empty = compute_availability(prefs(), [], Interval(utc(2026, 12, 1), utc(2026, 12, 2)), utc(2026, 1, 1))
    assert empty.free == [] and "NO_WORK_WINDOWS" in empty.warnings


def test_deadlines_do_not_block_time_by_default():
    assert default_busy("deadline") is False
    assert default_busy("exam") is True and default_busy("personal") is True


def test_check_preferences_cross_field_rules():
    ok = check_preferences(
        work_windows=[WeeklyWindow(0, 540, 600)],
        rest_windows=[],
        daily_max_min=120,
        weekly_max_min=600,
        min_session_min=25,
        max_session_min=90,
    )
    assert ok == []
    errors = check_preferences(
        work_windows=[WeeklyWindow(0, 540, 600), WeeklyWindow(0, 570, 660)],
        rest_windows=[],
        daily_max_min=700,
        weekly_max_min=600,
        min_session_min=95,
        max_session_min=90,
    )
    codes = {e["code"] for e in errors}
    assert {"overlap", "daily_gt_weekly", "min_gt_max"} <= codes
