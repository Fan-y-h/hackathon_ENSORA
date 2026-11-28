"""Comprehensive regression tests for Smart Planner optimization, conflicts, replanning & idempotency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.account.domain import PlanningPreferences, WeeklyWindow
from app.modules.planner import cpsat
from app.modules.planner.domain import BusyBlock, Interval, compute_availability, detect_conflicts
from app.modules.planner.models import Event, Plan, StudySession
from app.modules.planner.scheduling import Kept, Limits, Need, ScheduleResult, check_invariants, schedule
from app.modules.planner.source_bridge import to_import_item
from tests.conftest import NOW, clock_of

API = "/api/v1"
EVERY_DAY = [{"weekday": d, "start": "14:00", "end": "18:00"} for d in range(7)]  # 13:00-17:00Z


def utc(day: int, h: int, m: int = 0) -> datetime:
    return datetime(2026, 12, day, h, m, tzinfo=UTC)


def setup_profile(client: TestClient, daily: int = 180, weekly: int = 1200, windows=EVERY_DAY, **extra) -> None:
    r = client.patch(
        f"{API}/profile",
        json={
            "cohort": "2A",
            "preferences": {
                "work_windows": windows,
                "daily_max_min": daily,
                "weekly_max_min": weekly,
                "min_session_min": 25,
                "max_session_min": 90,
                **extra,
            },
        },
    )
    assert r.status_code == 200, r.text


def test_consecutive_events_are_not_conflicts_but_overlapping_official_events_are_reported():
    consecutive = [
        BusyBlock("cm1", Interval(utc(1, 8, 0), utc(1, 10, 0))),
        BusyBlock("td1", Interval(utc(1, 10, 0), utc(1, 12, 0))),
    ]
    assert detect_conflicts(consecutive) == []

    overlapping = [
        BusyBlock("cm1", Interval(utc(1, 8, 0), utc(1, 10, 0))),
        BusyBlock("exam1", Interval(utc(1, 9, 30), utc(1, 11, 30))),
    ]
    conflicts = detect_conflicts(overlapping)
    assert len(conflicts) == 1
    assert conflicts[0].event_id_a == "cm1" and conflicts[0].event_id_b == "exam1"
    assert conflicts[0].overlap == Interval(utc(1, 9, 30), utc(1, 10, 0))

    prefs = PlanningPreferences(
        timezone="Europe/Paris",
        work_windows=(WeeklyWindow(1, 8 * 60, 18 * 60),),
        rest_windows=(),
        daily_max_min=240,
        weekly_max_min=1200,
        min_session_min=25,
        max_session_min=90,
        revision=1,
    )
    snap = compute_availability(prefs, overlapping, Interval(utc(1, 7, 0), utc(1, 18, 0)), utc(1, 7, 0))
    assert len(snap.busy) == 2  # neither official event deleted
    assert len(snap.conflicts) == 1
    assert "EVENT_CONFLICT:cm1:exam1" in snap.warnings
    # No free time inside either conflicting event.
    assert all(not iv.overlaps(b.interval) for iv in snap.free for b in overlapping)


def test_official_event_conflict_reported_in_preview_without_deleting_courses(client: TestClient):
    setup_profile(client)
    e1 = client.post(
        f"{API}/events",
        json={"type": "class", "title": "CM Économétrie", "start_at": "2026-12-01T14:00:00+01:00", "end_at": "2026-12-01T16:00:00+01:00"},
    ).json()
    e2 = client.post(
        f"{API}/events",
        json={"type": "exam", "title": "Partiel Stat", "start_at": "2026-12-01T15:00:00+01:00", "end_at": "2026-12-01T17:00:00+01:00"},
    ).json()
    client.post(f"{API}/tasks", json={"title": "Devoir", "estimated_min": 60, "deadline_at": "2026-12-03T18:00:00+01:00"})

    p = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    assert len(p["conflicts"]) == 1
    assert {p["conflicts"][0]["event_id_a"], p["conflicts"][0]["event_id_b"]} == {e1["id"], e2["id"]}
    assert p["conflicts"][0]["minutes"] == 60
    assert any("EVENT_CONFLICT:" in w for w in p["warnings"])
    assert any("aucun cours officiel" in r.lower() for r in p["recommendations"])
    # Both events still exist intact in the agenda.
    evs = client.get(f"{API}/events", params={"start": "2026-12-01T00:00:00Z", "end": "2026-12-02T00:00:00Z"}).json()["items"]
    assert {e["id"] for e in evs} == {e1["id"], e2["id"]}
    # No generated session overlaps either official event.
    for s in p["sessions"]:
        s_iv = Interval(datetime.fromisoformat(s["start_at"]), datetime.fromisoformat(s["end_at"]))
        assert not s_iv.overlaps(Interval(datetime.fromisoformat(e1["start_at"]), datetime.fromisoformat(e1["end_at"])))
        assert not s_iv.overlaps(Interval(datetime.fromisoformat(e2["start_at"]), datetime.fromisoformat(e2["end_at"])))


def test_source_bridge_preserves_busy_false_and_imports_moodle_assignments():
    # Google Calendar all-day transparent event (busy=False) must NOT become busy=True.
    free_ev = to_import_item(
        "gcal-holiday",
        {"kind": "event", "title": "Férié", "event_type": "personal", "all_day_start": "2026-12-02", "busy": False},
        "google_calendar",
    )
    assert free_ev is not None and free_ev.busy is False and free_ev.all_day_date is not None

    # Moodle assignment with deadline_at becomes a non-busy deadline event.
    assign = to_import_item(
        "moodle:course:42:assignment:7",
        {"kind": "assignment", "title": "Rendu TP1 2ASTA02", "deadline_at": "2026-12-04T23:59:00Z"},
        "moodle",
    )
    assert assign is not None and assign.type == "deadline" and assign.busy is False and assign.subject_id == "2ASTA02"

    # Zero-duration deadline event is normalized to [start, start+1m) instead of crashing.
    pt = to_import_item(
        "dl-1",
        {"kind": "event", "title": "Rendu", "event_type": "deadline", "starts_at": "2026-12-04T23:59:00Z", "ends_at": "2026-12-04T23:59:00Z"},
        "moodle",
    )
    assert pt is not None and pt.end_at == pt.start_at + timedelta(minutes=1)


def test_multiday_calendar_events_block_the_full_local_span_through_dst():
    item = to_import_item(
        "travel",
        {
            "kind": "event", "title": "Travel", "event_type": "personal", "busy": True,
            "all_day_start": "2026-10-24", "all_day_end": "2026-10-27", "original_timezone": "Europe/Paris",
        },
        "google_calendar",
    )
    assert item is not None and item.all_day_date is None and item.busy is True
    assert item.start_at.isoformat() == "2026-10-23T22:00:00+00:00"
    assert item.end_at.isoformat() == "2026-10-26T23:00:00+00:00"


def test_break_min_enforced_and_checked_by_invariants():
    limits = Limits("Europe/Paris", daily_max_min=240, weekly_max_min=1200, min_session_min=30, max_session_min=60, break_min=15)
    free = [Interval(utc(1, 13, 0), utc(1, 17, 0))]
    needs = [
        Need("a", "task", "A", "task_work", 60, None, utc(2, 0), subject_id="s1"),
        Need("b", "task", "B", "task_work", 60, None, utc(2, 0), subject_id="s2"),
    ]
    res = schedule(needs, free, [], limits, utc(1, 13, 0), Interval(utc(1, 13, 0), utc(3, 0)))
    assert len(res.placed) == 2
    assert res.placed[1].interval.start - res.placed[0].interval.end >= timedelta(minutes=15)
    assert check_invariants(res, needs, free, [], limits, utc(1, 13, 0)) == []

    # Independently catch a break violation if a broken schedule places sessions 5 min apart.
    bad = ScheduleResult(
        placed=[
            res.placed[0],
            type(res.placed[1])(
                res.placed[1].need_key,
                Interval(res.placed[0].interval.end + timedelta(minutes=5), res.placed[0].interval.end + timedelta(minutes=65)),
                res.placed[1].priority,
            ),
        ],
        unscheduled=[],
        priorities=res.priorities,
    )
    errs = check_invariants(bad, needs, free, [], limits, utc(1, 13, 0))
    assert any(e.startswith("BREAK_VIOLATION") for e in errs)


def test_urgent_deadline_rescue_places_second_spaced_session_same_day():
    # Single day before deadline, 240 min daily cap, 90 min max session, 150 min urgent task.
    limits = Limits("Europe/Paris", daily_max_min=240, weekly_max_min=1200, min_session_min=25, max_session_min=90, break_min=0)
    free = [Interval(utc(1, 9, 0), utc(1, 12, 0)), Interval(utc(1, 14, 0), utc(1, 17, 0))]
    needs = [Need("urgent", "task", "Urgent", "task_work", 150, None, utc(1, 20, 0))]
    res = schedule(needs, free, [], limits, utc(1, 8, 0), Interval(utc(1, 8, 0), utc(5, 0)))
    assert res.unscheduled == []
    assert sum(p.minutes for p in res.placed) == 150
    assert len(res.placed) == 2
    assert res.placed[1].interval.start - res.placed[0].interval.end >= timedelta(minutes=15)
    assert check_invariants(res, needs, free, [], limits, utc(1, 8, 0)) == []


def test_no_work_windows_and_partial_plan_return_deficit_and_recommendations(client: TestClient):
    # No work windows configured at all -> 0 scheduled, full deficit, clear recommendations.
    setup_profile(client, daily=120, windows=[])
    client.post(f"{API}/tasks", json={"title": "Projet", "estimated_min": 120, "deadline_at": "2026-12-03T18:00:00+01:00"})
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    assert p["sessions"] == []
    assert p["summary"]["deficit_min"] == 120 and p["summary"]["unscheduled_min"] == 120
    assert p["unscheduled"][0]["reason"] == "NO_FREE_TIME_BEFORE_DEADLINE"
    assert p["unscheduled"][0]["explanation"]
    assert "NO_WORK_WINDOWS" in p["warnings"]
    assert len(p["recommendations"]) >= 2


def test_cpsat_infeasible_and_unknown_fall_back_to_valid_greedy_never_claimed_optimal(client: TestClient):
    setup_profile(client, daily=120)
    client.post(f"{API}/tasks", json={"title": "Devoir", "estimated_min": 60, "deadline_at": "2026-12-03T18:00:00+01:00"})

    for fake_status in ("INFEASIBLE", "UNKNOWN"):
        with patch.object(cpsat, "available", return_value=True), patch.object(
            cpsat, "schedule_cpsat", return_value=(None, fake_status)
        ):
            p = client.post(f"{API}/planner/preview", json={"horizon_days": 7, "algorithm": "cpsat"}).json()
            assert p["solver_status"] == "FALLBACK_GREEDY"
            assert p["solver_status"] != "OPTIMAL"
            assert p["algorithm_version"] == "greedy-v1"
            assert p["summary"]["scheduled_min"] == 60
            assert f"SOLVER:{fake_status};FALLBACK_GREEDY_BASELINE" in p["warnings"]
            assert any("greedy" in r.lower() for r in p["recommendations"])


def test_equally_covered_work_keeps_cp_sat_when_internal_slot_priorities_differ(client: TestClient):
    setup_profile(client, daily=120)
    client.post(f"{API}/tasks", json={"title": "Devoir", "estimated_min": 60, "deadline_at": "2026-12-03T18:00:00+01:00"})

    def equally_good_with_different_scores(*args, **kwargs):
        result = schedule(*args, **kwargs)
        result.priorities = {key: value / 2 for key, value in result.priorities.items()}
        return result, "OPTIMAL"

    with patch.object(cpsat, "available", return_value=True), patch.object(
        cpsat, "schedule_cpsat", side_effect=equally_good_with_different_scores,
    ):
        response = client.post(f"{API}/planner/preview", json={"horizon_days": 7, "algorithm": "cpsat"})
    assert response.status_code == 200
    result = response.json()
    assert result["summary"]["scheduled_min"] == 60
    assert result["solver_status"] == "OPTIMAL"
    assert result["algorithm_version"] == cpsat.ALGORITHM_VERSION


def test_preview_does_not_mutate_persisted_plan_and_accept_is_idempotent(client: TestClient):
    setup_profile(client, daily=180)
    client.post(f"{API}/tasks", json={"title": "Devoir", "estimated_min": 90, "deadline_at": "2026-12-03T18:00:00+01:00"})

    # Preview before any accepted plan must leave StudySession table empty.
    p1 = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    with client.app.state.db.session_factory() as s:
        assert list(s.scalars(select(StudySession))) == []
        assert list(s.scalars(select(Plan).where(Plan.status == "accepted"))) == []

    # Accept creates sessions once.
    acc = client.post(f"{API}/planner/accept", json={"preview_id": p1["preview_id"], "input_revision": p1["input_revision"]})
    assert acc.status_code == 200
    initial_sessions = acc.json()["sessions"]
    assert len(initial_sessions) == 1

    # Repeated accept of same preview is rejected (409) and creates ZERO duplicates.
    dup = client.post(f"{API}/planner/accept", json={"preview_id": p1["preview_id"], "input_revision": p1["input_revision"]})
    assert dup.status_code == 409
    assert len(client.get(f"{API}/plans/current").json()["sessions"]) == 1

    # Another preview does NOT touch the accepted plan or its sessions.
    p2 = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    assert p2["diff"]["added"] == [] and p2["diff"]["moved"] == [] and p2["diff"]["removed"] == []
    assert p2["diff"]["unchanged"] == 1
    assert client.get(f"{API}/plans/current").json()["sessions"] == initial_sessions

    # Accepting p2 still produces exactly 1 session (no duplicates).
    acc2 = client.post(f"{API}/planner/accept", json={"preview_id": p2["preview_id"], "input_revision": p2["input_revision"]})
    assert acc2.status_code == 200
    assert len(acc2.json()["sessions"]) == 1


def test_weekly_cap_counts_completed_sessions_earlier_in_same_week_and_blocks_move(client: TestClient):
    # NOW is Sunday 2026-11-29 13:00Z; advance to Monday 2026-11-30 12:00Z, complete 90 min,
    # then advance to Wednesday 2026-12-02 12:00Z with weekly_max_min=150: only 60 min left for the week!
    setup_profile(client, daily=120, weekly=150)
    clock_of(client).set(datetime(2026, 11, 30, 12, 0, tzinfo=UTC))
    client.post(f"{API}/tasks", json={"title": "T1", "estimated_min": 90, "deadline_at": "2026-12-05T18:00:00+01:00"})
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    plan = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}).json()
    s1 = plan["sessions"][0]
    clock_of(client).set(datetime(2026, 11, 30, 13, 5, tzinfo=UTC))
    client.post(f"{API}/sessions/{s1['id']}/start")
    client.post(f"{API}/sessions/{s1['id']}/finish", json={"outcome": "completed", "actual_min": 90})

    # Jump to Wednesday (same ISO week, 2 days later) and add a 120-min task due Friday.
    clock_of(client).set(datetime(2026, 12, 2, 12, 0, tzinfo=UTC))
    client.post(f"{API}/tasks", json={"title": "T2", "estimated_min": 120, "deadline_at": "2026-12-04T20:00:00+01:00"})
    p2 = client.post(f"{API}/planner/preview", json={"horizon_days": 5}).json()
    # Only 150 - 90 = 60 min left in this ISO week!
    assert p2["summary"]["scheduled_min"] == 60
    assert p2["summary"]["deficit_min"] == 60

    # Accept and try to extend/move session to 90 min -> rejected with WEEKLY_CAP!
    plan2 = client.post(f"{API}/planner/accept", json={"preview_id": p2["preview_id"], "input_revision": p2["input_revision"]}).json()
    planned = [s for s in plan2["sessions"] if s["status"] == "planned"][0]
    bad_move = client.patch(
        f"{API}/sessions/{planned['id']}",
        json={"start_at": "2026-12-03T14:00:00+01:00", "end_at": "2026-12-03T15:30:00+01:00"},
    )
    assert bad_move.status_code == 422
    assert any(d["code"] == "WEEKLY_CAP" for d in bad_move.json()["error"]["details"])
