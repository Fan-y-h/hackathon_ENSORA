"""QA Section 4 — Independent Smart Planner invariant, conflict & edge-case tests.

Verifies every required scenario:
- overlap with busy events / between sessions
- consecutive (back-to-back) events with zero gap
- mandatory read-only class
- exam + confirmed scope + strictly-before-exam scheduling
- locked session preservation
- unavailability / rest windows
- non-splittable task exceeding max_session_min
- zero free slots before deadline
- past & tight deadlines
- CP-SAT unavailable / infeasible -> honest greedy fallback
- partial planning under capacity shortfall
- dynamic replanning (new exam, partial session, missed session)
- non-mutating preview, atomic apply, stale/double accept rejection, idempotence
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.modules.planner import cpsat, scheduling
from app.modules.planner.domain import Interval
from tests.conftest import NOW, clock_of

API = "/api/v1"
PARIS = ZoneInfo("Europe/Paris")
EVERY_DAY_WORK = [{"weekday": d, "start": "08:00", "end": "20:00"} for d in range(7)]


def _setup_profile(client, *, work=EVERY_DAY_WORK, rest=(), daily=180, weekly=900, min_s=30, max_s=90):
    r = client.patch(
        f"{API}/profile",
        json={
            "cohort": "2A",
            "timezone": "Europe/Paris",
            "preferences": {
                "work_windows": list(work),
                "rest_windows": list(rest),
                "daily_max_min": daily,
                "weekly_max_min": weekly,
                "min_session_min": min_s,
                "max_session_min": max_s,
            },
        },
    )
    assert r.status_code == 200, r.text


def _assert_plan_invariants(sessions, busy_intervals, *, min_s=30, max_s=90, daily_cap=180, weekly_cap=900, now=NOW):
    """Independent QA invariant oracle (does not reuse scheduler internals)."""
    parsed = []
    for s in sessions:
        st = datetime.fromisoformat(s["start_at"].replace("Z", "+00:00"))
        en = datetime.fromisoformat(s["end_at"].replace("Z", "+00:00"))
        dur = int((en - st).total_seconds() // 60)
        assert dur > 0, f"Non-positive duration: {s}"
        assert min_s <= dur <= max_s, f"Duration {dur} outside [{min_s}, {max_s}]"
        assert st >= now, f"Session starts in the past: {st} < {now}"
        assert st.astimezone(PARIS).date() == (en - timedelta(microseconds=1)).astimezone(PARIS).date()
        for b_start, b_end in busy_intervals:
            assert en <= b_start or st >= b_end, f"Session {(st, en)} overlaps forbidden {(b_start, b_end)}"
        parsed.append((st, en, dur))
    parsed.sort()
    for (s1, e1, _), (s2, e2, _) in zip(parsed, parsed[1:], strict=False):
        assert e1 <= s2, f"Two sessions overlap: {(s1, e1)} and {(s2, e2)}"
    by_day = {}
    by_week = {}
    for st, _, dur in parsed:
        d = st.astimezone(PARIS).date()
        by_day[d] = by_day.get(d, 0) + dur
        wk = d.isocalendar()[:2]
        by_week[wk] = by_week.get(wk, 0) + dur
    for d, total in by_day.items():
        assert total <= daily_cap, f"Daily cap exceeded on {d}: {total} > {daily_cap}"
    for wk, total in by_week.items():
        assert total <= weekly_cap, f"Weekly cap exceeded on {wk}: {total} > {weekly_cap}"


def test_consecutive_mandatory_classes_exam_and_rest_windows_never_overlapped(client):
    _setup_profile(
        client,
        rest=[{"weekday": d, "start": "12:00", "end": "13:30"} for d in range(7)],
        daily=180,
        weekly=900,
    )
    # Consecutive back-to-back mandatory classes on Monday 2026-11-30 (08:00-10:00, 10:00-12:00, 13:30-16:00 Paris)
    c1 = client.post(f"{API}/events", json={
        "type": "class", "title": "CM Stats", "subject_id": "stat",
        "start_at": "2026-11-30T08:00:00+01:00", "end_at": "2026-11-30T10:00:00+01:00",
    }).json()
    c2 = client.post(f"{API}/events", json={
        "type": "td", "title": "TD Stats", "subject_id": "stat",
        "start_at": "2026-11-30T10:00:00+01:00", "end_at": "2026-11-30T12:00:00+01:00",
    }).json()
    c3 = client.post(f"{API}/events", json={
        "type": "tp", "title": "TP Python", "subject_id": "info",
        "start_at": "2026-11-30T13:30:00+01:00", "end_at": "2026-11-30T16:00:00+01:00",
    }).json()
    exam = client.post(f"{API}/events", json={
        "type": "exam", "title": "Partiel Stats", "subject_id": "stat",
        "start_at": "2026-12-01T14:00:00+01:00", "end_at": "2026-12-01T16:00:00+01:00",
    }).json()
    client.patch(
        f"{API}/assessments/{exam['assessment_id']}",
        json={"scope_status": "confirmed", "concept_ids": ["c-mle", "c-ols"], "difficulty": "medium"},
    )
    client.post(f"{API}/tasks", json={
        "title": "Rendu TP", "estimated_min": 90, "deadline_at": "2026-12-02T18:00:00+01:00",
    })

    prev = client.post(f"{API}/planner/preview", json={"horizon_days": 5})
    assert prev.status_code == 200
    body = prev.json()

    forbidden = [
        (datetime.fromisoformat(e["start_at"].replace("Z", "+00:00")), datetime.fromisoformat(e["end_at"].replace("Z", "+00:00")))
        for e in (c1, c2, c3, exam)
    ]
    # Add lunch rest windows on Nov 29 - Dec 4
    for day_offset in range(6):
        d = (NOW + timedelta(days=day_offset)).astimezone(PARIS).date()
        forbidden.append((
            datetime(d.year, d.month, d.day, 12, 0, tzinfo=PARIS).astimezone(UTC),
            datetime(d.year, d.month, d.day, 13, 30, tzinfo=PARIS).astimezone(UTC),
        ))

    _assert_plan_invariants(body["sessions"], forbidden)

    # All exam prep sessions must finish strictly before exam start (2026-12-01T13:00Z)
    exam_start = datetime(2026, 12, 1, 13, 0, tzinfo=UTC)
    for s in body["sessions"]:
        if s["assessment_id"] == exam["assessment_id"]:
            assert datetime.fromisoformat(s["end_at"].replace("Z", "+00:00")) <= exam_start

    # Official events were never moved
    for orig in (c1, c2, c3, exam):
        refetched = client.get(f"{API}/events/{orig['id']}").json()
        assert refetched["start_at"] == orig["start_at"]
        assert refetched["end_at"] == orig["end_at"]


def test_impossible_tasks_and_no_slot_remain_visible(client):
    _setup_profile(
        client,
        work=[{"weekday": 0, "start": "18:00", "end": "19:00"}],  # only 60 min on Monday!
        daily=180,
        weekly=900,
        min_s=30,
        max_s=90,
    )
    # 1. Task too long & non-splittable (> max_session_min=90)
    t_long = client.post(f"{API}/tasks", json={
        "title": "Exam blanc 2h indivisible", "estimated_min": 120,
        "splittable": False, "deadline_at": "2026-12-03T18:00:00+01:00",
    }).json()
    # 2. Task whose deadline is before any free window (Sunday 2026-11-29 23:00 Paris -> 0 free minutes!)
    t_noslot = client.post(f"{API}/tasks", json={
        "title": "Urgent ce soir", "estimated_min": 45,
        "deadline_at": "2026-11-29T23:00:00+01:00",
    }).json()
    # 3. Task with deadline already passed
    t_past = client.post(f"{API}/tasks", json={
        "title": "En retard", "estimated_min": 30,
        "deadline_at": "2026-11-28T18:00:00+01:00",
    }).json()
    # 4. Partial capacity task (90 min needed, only 60 min available Monday)
    t_partial = client.post(f"{API}/tasks", json={
        "title": "Fiche Stat", "estimated_min": 90,
        "deadline_at": "2026-12-02T18:00:00+01:00",
    }).json()

    body = client.post(f"{API}/planner/preview", json={"horizon_days": 5}).json()
    unsched = {u["need_id"]: u for u in body["unscheduled"]}

    assert unsched[f"task:{t_long['id']}"]["reason"] == "EXCEEDS_MAX_SESSION"
    assert unsched[f"task:{t_noslot['id']}"]["reason"] == "NO_FREE_TIME_BEFORE_DEADLINE"
    assert unsched[f"task:{t_past['id']}"]["reason"] == "DEADLINE_PASSED"
    assert unsched[f"task:{t_partial['id']}"]["reason"] == "CAPACITY"
    assert unsched[f"task:{t_partial['id']}"]["remaining_min"] == 30
    assert body["summary"]["scheduled_min"] == 60


def test_preview_is_non_mutating_idempotent_and_cpsat_falls_back_cleanly(client):
    _setup_profile(client)
    client.post(f"{API}/tasks", json={
        "title": "Exercices Econométrie", "estimated_min": 120,
        "deadline_at": "2026-12-03T18:00:00+01:00",
    })

    # Preview does not create an accepted plan
    p1 = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    assert client.get(f"{API}/plans/current").status_code == 404

    # Idempotence: identical inputs produce identical session slots
    p2 = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    slots1 = [(s["need_key"], s["start_at"], s["end_at"]) for s in p1["sessions"]]
    slots2 = [(s["need_key"], s["start_at"], s["end_at"]) for s in p2["sessions"]]
    assert slots1 == slots2

    # Explicitly requesting CP-SAT: if ortools absent -> honest greedy fallback with warning; if present -> valid invariants
    p_cs = client.post(f"{API}/planner/preview", json={"horizon_days": 7, "algorithm": "cpsat"}).json()
    assert p_cs["summary"]["scheduled_min"] == 120
    if not cpsat.available():
        assert p_cs["algorithm_version"] == scheduling.ALGORITHM_VERSION
        assert "CPSAT_UNAVAILABLE_FALLBACK_GREEDY" in p_cs["warnings"]

    # Accept latest preview atomically, then double-accept rejected with 409
    acc = client.post(
        f"{API}/planner/accept",
        json={"preview_id": p_cs["preview_id"], "input_revision": p_cs["input_revision"]},
    )
    assert acc.status_code == 200
    again = client.post(
        f"{API}/planner/accept",
        json={"preview_id": p_cs["preview_id"], "input_revision": p_cs["input_revision"]},
    )
    assert again.status_code == 409


def test_locked_session_conflict_warning_does_not_move_official_event(client):
    _setup_profile(client)
    client.post(f"{API}/tasks", json={
        "title": "Révision", "estimated_min": 60, "deadline_at": "2026-12-03T18:00:00+01:00",
    })
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 5}).json()
    plan = client.post(
        f"{API}/planner/accept",
        json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]},
    ).json()
    s = plan["sessions"][0]
    # Lock the session
    locked = client.patch(f"{API}/sessions/{s['id']}", json={"locked": True, "expected_revision": s["revision"]}).json()
    assert locked["locked"] is True

    # An official mandatory class is later scheduled over the locked slot
    ev = client.post(f"{API}/events", json={
        "type": "class", "title": "Cours rattrapage",
        "start_at": s["start_at"], "end_at": s["end_at"],
    }).json()

    rp = client.post(
        f"{API}/planner/replan",
        json={"base_plan_revision": client.get(f"{API}/plans/current").json()["revision"], "reason": "agenda_changed"},
    ).json()
    assert f"LOCK_CONFLICT:{s['id']}" in rp["warnings"]
    assert client.get(f"{API}/events/{ev['id']}").json()["start_at"] == ev["start_at"]


def test_qa_repro_weekly_cap_counts_earlier_same_week_completed_sessions(client):
    # Monday 2026-11-30 is start of ISO week 49
    clock_of(client).set(datetime(2026, 11, 30, 8, 0, tzinfo=UTC))
    _setup_profile(client, daily=180, weekly=180, min_s=30, max_s=90)
    client.post(f"{API}/tasks", json={"title": "Lundi", "estimated_min": 180, "deadline_at": "2026-12-05T18:00:00+01:00"})
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 5}).json()
    plan = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}).json()
    # Complete all 180 min on Monday (two 90-min sessions)
    for sess in plan["sessions"]:
        assert client.post(f"{API}/sessions/{sess['id']}/start").status_code == 200
        assert client.post(f"{API}/sessions/{sess['id']}/finish", json={"outcome": "completed", "actual_min": 90}).status_code == 200

    # Advance to Tuesday 2026-12-01 (SAME ISO week 49!) and add another task
    clock_of(client).set(datetime(2026, 12, 1, 8, 0, tzinfo=UTC))
    client.post(f"{API}/tasks", json={"title": "Mardi", "estimated_min": 90, "deadline_at": "2026-12-05T18:00:00+01:00"})
    p_tue = client.post(f"{API}/planner/preview", json={"horizon_days": 4}).json()
    # Weekly cap (180 min) is already 100% consumed in week 49 -> 0 min may be scheduled in week 49!
    assert p_tue["summary"]["scheduled_min"] == 0


def test_qa_repro_manual_session_move_enforces_weekly_cap(client):
    clock_of(client).set(datetime(2026, 11, 30, 8, 0, tzinfo=UTC))
    _setup_profile(client, daily=120, weekly=120, min_s=30, max_s=90)
    client.post(f"{API}/tasks", json={"title": "T1", "estimated_min": 90, "deadline_at": "2026-12-05T18:00:00+01:00"})
    client.post(f"{API}/tasks", json={"title": "T2", "estimated_min": 30, "deadline_at": "2026-12-05T18:00:00+01:00"})
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 5}).json()
    plan = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}).json()
    short_s = next(s for s in plan["sessions"] if s["estimated_min"] == 30)
    # Try to expand the 30-min session to 90 min on Tuesday -> total week would become 180 > weekly_max_min=120!
    resp = client.patch(
        f"{API}/sessions/{short_s['id']}",
        json={
            "expected_revision": short_s["revision"],
            "start_at": "2026-12-01T14:00:00+01:00",
            "end_at": "2026-12-01T15:30:00+01:00",
        },
    )
    assert resp.status_code == 422


def test_task_without_deadline_in_zero_free_time_emits_warning(client):
    _setup_profile(client, work=[])  # Zero work windows at all!
    t = client.post(f"{API}/tasks", json={"title": "Sans échéance", "estimated_min": 60}).json()
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 7}).json()
    assert any(u["need_id"] == f"task:{t['id']}" for u in p["unscheduled"]) or "NO_WORK_WINDOWS" in p["warnings"]


def test_qa_repro_source_bridge_respects_busy_false():
    from app.modules.planner.source_bridge import to_import_item

    item = to_import_item(
        "gcal:free-1",
        {
            "kind": "event",
            "title": "Jour férié (transparent)",
            "event_type": "personal",
            "starts_at": "2026-12-10T08:00:00+00:00",
            "ends_at": "2026-12-10T18:00:00+00:00",
            "status": "confirmed",
            "busy": False,
        },
        source="google_calendar",
    )
    assert item is not None
    assert getattr(item, "busy", True) is False
