from datetime import timedelta

from fastapi.testclient import TestClient

from tests.conftest import NOW, clock_of, make_app

API = "/api/v1"
EVERY_DAY = [{"weekday": d, "start": "14:00", "end": "18:00"} for d in range(7)]  # 13:00–17:00Z


def setup_profile(client, daily=180, windows=EVERY_DAY, **extra):
    r = client.patch(f"{API}/profile", json={"cohort": "2A", "preferences": {
        "work_windows": windows, "daily_max_min": daily, "weekly_max_min": 1200,
        "min_session_min": 25, "max_session_min": 90, **extra}})
    assert r.status_code == 200, r.text


def task(client, title, minutes, deadline=None, **kw):
    body = {"title": title, "estimated_min": minutes, **kw}
    if deadline:
        body["deadline_at"] = deadline
    return client.post(f"{API}/tasks", json=body).json()


def preview(client, days=7):
    r = client.post(f"{API}/planner/preview", json={"horizon_days": days})
    assert r.status_code == 200, r.text
    return r.json()


def accept(client, p):
    r = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]})
    assert r.status_code == 200, r.text
    return r.json()


def test_preview_respects_windows_caps_and_has_no_overlap(client):
    setup_profile(client, daily=120)
    t = task(client, "Rapport ML", 300, "2026-12-03T23:00:00+01:00", priority=5)
    p = preview(client)
    sessions = p["sessions"]
    assert sessions and all(s["task_id"] == t["id"] for s in sessions)
    ivs = sorted((s["start_at"], s["end_at"]) for s in sessions)
    assert all(a[1] <= b[0] for a, b in zip(ivs, ivs[1:], strict=False))
    assert all(s["start_at"][11:16] >= "13:00" and s["end_at"][11:16] <= "17:00" for s in sessions)
    assert all(d["planned_min"] <= 120 for d in p["summary"]["per_day"])
    assert p["summary"]["scheduled_min"] == 300 and p["unscheduled"] == []
    assert sessions[0]["start_at"] >= "2026-11-29T13:00:00Z"  # never in the past
    assert p["diff"]["added"] and p["diff"]["removed"] == []


def test_infeasible_workload_is_reported_not_forced(client):
    setup_profile(client, daily=60)
    task(client, "Énorme", 600, "2026-12-01T18:00:00+01:00")
    p = preview(client)
    assert p["summary"]["scheduled_min"] == 180  # 3 days × 60
    assert p["unscheduled"][0]["remaining_min"] == 420 and p["unscheduled"][0]["reason"] == "CAPACITY"
    assert "INSUFFICIENT_CAPACITY" in p["warnings"]


def test_accept_is_atomic_and_rejects_stale_or_reused_preview(client):
    setup_profile(client)
    task(client, "T", 60, "2026-12-02T12:00:00+01:00")
    p = preview(client)
    plan = accept(client, p)
    assert plan["status"] == "accepted" and len(plan["sessions"]) == 1
    again = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]})
    assert again.status_code == 409 and again.json()["error"]["code"] == "preview_not_active"

    p2 = preview(client)
    task(client, "New task", 30)  # inputs change after the preview
    stale = client.post(f"{API}/planner/accept", json={"preview_id": p2["preview_id"], "input_revision": p2["input_revision"]})
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "stale_preview"
    assert client.get(f"{API}/plans/current").json()["id"] == plan["id"]


def test_assessment_requires_confirmed_scope_and_carries_generation_spec(client):
    setup_profile(client)
    ev = client.post(f"{API}/events", json={"type": "exam", "title": "Exam Stat", "subject_id": "stat",
                                            "start_at": "2026-12-03T08:30:00+01:00", "end_at": "2026-12-03T10:30:00+01:00"}).json()
    p = preview(client)
    assert f"SCOPE_UNCONFIRMED:{ev['assessment_id']}" in p["warnings"] and p["sessions"] == []
    client.patch(f"{API}/assessments/{ev['assessment_id']}",
                 json={"scope_status": "confirmed", "concept_ids": ["c-bias", "c-lln"], "difficulty": "hard"})
    p = preview(client)
    keys = {s["need_key"] for s in p["sessions"]}
    assert keys == {f"assessment:{ev['assessment_id']}:c-bias", f"assessment:{ev['assessment_id']}:c-lln"}
    s0 = p["sessions"][0]
    assert s0["activity_type"] == "discovery"  # unknown mastery
    assert s0["generation_spec"]["generate_on"] == "session_start"  # no eager generation
    assert all(s["end_at"] <= "2026-12-03T07:30:00Z" for s in p["sessions"])  # before the exam


def test_move_lock_and_replan_preserves_manual_decisions(client):
    setup_profile(client)
    task(client, "A", 60, "2026-12-04T12:00:00+01:00")
    plan = accept(client, preview(client))
    s = plan["sessions"][0]

    outside = client.patch(f"{API}/sessions/{s['id']}", json={"start_at": "2026-11-30T08:00:00+01:00", "end_at": "2026-11-30T09:00:00+01:00"})
    assert outside.status_code == 422 and outside.json()["error"]["details"][0]["code"] == "OUTSIDE_FREE_TIME"
    client.post(f"{API}/events", json={"type": "personal", "title": "Dentiste",
                                       "start_at": "2026-12-01T15:00:00+01:00", "end_at": "2026-12-01T16:00:00+01:00"})
    busy = client.patch(f"{API}/sessions/{s['id']}", json={"start_at": "2026-12-01T15:00:00+01:00", "end_at": "2026-12-01T16:00:00+01:00"})
    assert busy.status_code == 422

    moved = client.patch(f"{API}/sessions/{s['id']}", json={"start_at": "2026-12-01T16:00:00+01:00", "end_at": "2026-12-01T17:00:00+01:00"})
    assert moved.status_code == 200 and moved.json()["locked"] is True
    plan_rev = client.get(f"{API}/plans/current").json()["revision"]

    assert client.post(f"{API}/planner/replan", json={"base_plan_revision": plan_rev - 1}).status_code == 409
    rp = client.post(f"{API}/planner/replan", json={"base_plan_revision": plan_rev, "reason": "agenda_changed"}).json()
    assert rp["reason"] == "agenda_changed"
    assert [k["id"] for k in rp["kept_sessions"]] == [s["id"]]
    assert rp["sessions"] == []  # the locked 60 min already cover the task
    plan2 = accept(client, rp)
    assert [(x["id"], x["start_at"]) for x in plan2["sessions"]] == [(s["id"], "2026-12-01T15:00:00Z")]


def test_partial_session_reschedules_only_remaining_work(client):
    setup_profile(client)
    t = task(client, "Projet", 90, "2026-12-04T12:00:00+01:00")
    plan = accept(client, preview(client))
    s = plan["sessions"][0]
    clock_of(client).set(NOW + timedelta(hours=1, minutes=5))  # 14:05Z, during the session
    assert client.post(f"{API}/sessions/{s['id']}/start").json()["status"] == "in_progress"
    assert client.post(f"{API}/sessions/{plan['sessions'][0]['id']}/start").status_code == 409
    done = client.post(f"{API}/sessions/{s['id']}/finish", json={"outcome": "partial", "actual_min": 40,
                                                                  "reflection": {"difficulty": 4, "objective_met": False}}).json()
    assert done["status"] == "partial" and done["actual_min"] == 40
    tk = client.get(f"{API}/tasks").json()["items"][0]
    assert tk["remaining_min"] == 50 and tk["status"] == "in_progress"
    assert client.post(f"{API}/sessions/{s['id']}/finish", json={"outcome": "completed"}).status_code == 409

    rp = preview(client)
    assert sum(x["estimated_min"] for x in rp["sessions"] if x["task_id"] == t["id"]) == 50
    assert [k["id"] for k in rp["kept_sessions"]] == [s["id"]]
    plan2 = accept(client, rp)
    assert any(x["id"] == s["id"] and x["status"] == "partial" for x in plan2["sessions"])  # history preserved


def test_missed_sessions_are_rescheduled_and_marked_skipped(client):
    setup_profile(client)
    task(client, "T", 60, "2026-12-04T12:00:00+01:00")
    plan = accept(client, preview(client))
    s = plan["sessions"][0]
    clock_of(client).set(NOW + timedelta(days=1))
    p = preview(client)
    assert "MISSED_SESSIONS:1" in p["warnings"] and p["summary"]["scheduled_min"] == 60
    plan2 = accept(client, p)
    assert all(x["id"] != s["id"] for x in plan2["sessions"] if x["status"] == "planned")


def test_academic_objective_changes_priority_but_not_constraints(client):
    setup_profile(client, daily=90, windows=[{"weekday": 6, "start": "14:00", "end": "15:30"}])
    task(client, "Devoir X", 90, "2026-11-30T12:00:00+01:00", subject_id="subj-x", priority=3)
    task(client, "Devoir Y", 90, "2026-11-30T12:00:00+01:00", subject_id="subj-y", priority=3)
    first = preview(client)
    assert len(first["sessions"]) == 1 and first["unscheduled"][0]["remaining_min"] == 90
    winner = first["sessions"][0]["title"]

    loser_subject = "subj-y" if winner == "Devoir X" else "subj-x"
    g = "/api/v1/grades"
    pid = client.post(f"{g}/programs", json={"title": "P"}).json()["id"]
    nodes = client.put(f"{g}/programs/{pid}/curriculum", json={"nodes": [
        {"key": "ue", "kind": "unit", "title": "UE"},
        {"key": "s", "parent_key": "ue", "kind": "subject", "title": "S", "subject_id": loser_subject},
    ]}).json()["nodes"]
    client.post(f"{g}/objectives", json={"program_id": pid, "node_id": nodes[0]["id"], "target": 14})
    second = preview(client)
    assert second["input_revision"] != first["input_revision"]
    assert second["sessions"][0]["title"] != winner
    assert "ACADEMIC_SIGNAL" in second["sessions"][0]["reason"]
    assert second["summary"]["scheduled_min"] == 90  # same capacity, still capped


def test_planner_isolation(client, as_bob):
    setup_profile(client)
    task(client, "T", 60)
    p = preview(client)
    r = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}, headers=as_bob)
    assert r.status_code == 404
    plan = accept(client, p)
    sid = plan["sessions"][0]["id"]
    assert client.post(f"{API}/sessions/{sid}/start", headers=as_bob).status_code == 404
    assert client.get(f"{API}/plans/current", headers=as_bob).status_code == 404


def test_end_to_end_loop_and_restart(tmp_path):
    """Exam → confirmed scope → plan → session → wrong quiz → mastery → replan → restart."""
    url = f"sqlite:///{(tmp_path / 'e2e.db').as_posix()}"
    c = TestClient(make_app(url))
    setup_profile(c)
    ev = c.post(f"{API}/events", json={"type": "exam", "title": "Exam Stat", "subject_id": "stat",
                                       "start_at": "2026-12-04T08:30:00+01:00", "end_at": "2026-12-04T10:30:00+01:00"}).json()
    c.patch(f"{API}/assessments/{ev['assessment_id']}", json={"scope_status": "confirmed", "concept_ids": ["c-bias"]})
    plan = accept(c, preview(c))
    assert plan["sessions"][0]["activity_type"] == "discovery"
    s = plan["sessions"][0]
    clock_of(c).set(NOW + timedelta(hours=1, minutes=1))
    c.post(f"{API}/sessions/{s['id']}/start")

    quiz = c.post(f"{API}/quizzes", json={"title": "Biais", "subject_id": "stat", "questions": [
        {"type": "true_false", "prompt": "Biais = E[θ̂]−θ", "answer": True, "concept_ids": ["c-bias"]}]}).json()
    att = c.post(f"{API}/quizzes/{quiz['id']}/attempts", json={"session_id": s["id"]}).json()
    c.patch(f"{API}/attempts/{att['id']}/responses", json={"responses": [{"question_id": quiz["questions"][0]["id"], "response": False}]})
    res = c.post(f"{API}/attempts/{att['id']}/submit").json()
    assert res["mastery_updates"][0]["status"] == "learning"
    c.post(f"{API}/sessions/{s['id']}/finish", json={"outcome": "completed", "actual_min": 60})

    analytics = c.get(f"{API}/analytics", params={"start": "2026-11-29T00:00:00+01:00", "end": "2026-12-06T00:00:00+01:00"}).json()
    assert analytics["plan"]["inputs_changed_since_accept"] is True
    assert analytics["quizzes"]["attempts"] == 1 and analytics["mastery"]["by_status"]["learning"] == 1
    assert analytics["study"]["sessions"]["completed"] == 1

    current = c.get(f"{API}/plans/current").json()
    rp = c.post(f"{API}/planner/replan", json={"base_plan_revision": current["revision"], "reason": "mastery_changed"}).json()
    kinds = {x["need_key"].split(":")[0] for x in rp["sessions"]}
    assert kinds == {"review"}  # failed concept → review due tomorrow; 60 prep min already done
    review = rp["sessions"][0]
    assert review["start_at"] >= "2026-11-30T13:01:00Z" and review["concept_ids"] == ["c-bias"]
    assert [k["status"] for k in rp["kept_sessions"]] == ["completed"]
    accept(c, rp)
    c.app.state.db.engine.dispose()

    restarted = TestClient(make_app(url))
    clock_of(restarted).set(NOW + timedelta(hours=2))
    after = restarted.get(f"{API}/plans/current").json()
    assert any(x["status"] == "completed" for x in after["sessions"])
    assert restarted.get(f"{API}/mastery").json()[0]["concept_id"] == "c-bias"
    restarted.app.state.db.engine.dispose()
