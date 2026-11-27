"""P1 features: FSRS + CP-SAT (T26), open grading + partial credit (T35), exam trainer errors (T24),
Focus Room (T23), notes + error notebook (T22), notifications (T25), schema upgrade helper."""

import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.modules.planner import cpsat
from tests.conftest import NOW, clock_of, make_app

API = "/api/v1"
WINDOWS = [{"weekday": d, "start": "14:00", "end": "18:00"} for d in range(7)]


def profile(client, **prefs):
    r = client.patch(f"{API}/profile", json={"cohort": "2A", "preferences": {"work_windows": WINDOWS, **prefs}})
    assert r.status_code == 200, r.text


def quiz(client, questions, **kw):
    r = client.post(f"{API}/quizzes", json={"title": "Q", "subject_id": "stat", "questions": questions, **kw})
    assert r.status_code == 201, r.text
    return r.json()


def answer_and_submit(client, q, responses, mode="practice"):
    a = client.post(f"{API}/quizzes/{q['id']}/attempts", json={"mode": mode}).json()
    client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [
        {"question_id": q["questions"][i]["id"], "response": r} for i, r in enumerate(responses)]})
    return a, client.post(f"{API}/attempts/{a['id']}/submit").json()


# ---- T26 FSRS ------------------------------------------------------------------------------
def test_fsrs_opt_in_with_heuristic_baseline(client):
    deck = client.post(f"{API}/decks", json={"title": "d"}).json()
    card = client.post(f"{API}/decks/{deck['id']}/cards", json={"front": "f", "back": "b", "concept_id": "c"}).json()
    r = client.post(f"{API}/cards/{card['id']}/review", json={"rating": "good"}).json()
    assert r["algorithm"].startswith("ema") and r["interval_days"] == 2  # default = heuristic

    profile(client, repetition_algorithm="fsrs", desired_retention=0.9)
    card2 = client.post(f"{API}/decks/{deck['id']}/cards", json={"front": "f2", "back": "b2"}).json()
    r1 = client.post(f"{API}/cards/{card2['id']}/review", json={"rating": "good"}).json()
    assert r1["algorithm"] == "fsrs-4.5-default-params" and r1["interval_days"] == 4  # S0(good)=3.71
    assert r1["baseline_interval_days"] == 2
    clock_of(client).set(NOW + timedelta(days=4))
    r2 = client.post(f"{API}/cards/{card2['id']}/review", json={"rating": "good"}).json()
    assert r2["interval_days"] > r1["interval_days"] and 0.85 < r2["retrievability_before"] < 0.95
    assert client.patch(f"{API}/profile", json={"preferences": {"desired_retention": 0.5}}).status_code == 422


# ---- T26 CP-SAT -----------------------------------------------------------------------------
@pytest.mark.skipif(not cpsat.available(), reason="ortools not installed")
def test_cpsat_planner_via_api_with_baseline(client):
    profile(client, daily_max_min=120)
    for i, minutes in enumerate((200, 150, 90)):
        client.post(f"{API}/tasks", json={"title": f"T{i}", "estimated_min": minutes,
                                          "deadline_at": "2026-12-03T18:00:00+01:00", "priority": 3 + i})
    greedy = client.post(f"{API}/planner/preview", json={"algorithm": "greedy"}).json()
    opt = client.post(f"{API}/planner/preview", json={"algorithm": "cpsat"}).json()
    assert greedy["algorithm_version"] == "greedy-v1"
    assert any(w.startswith("SOLVER:") for w in opt["warnings"])
    assert opt["summary"]["scheduled_min"] >= greedy["summary"]["scheduled_min"] - 15
    assert all(d["planned_min"] <= 120 for d in opt["summary"]["per_day"])
    ivs = sorted((s["start_at"], s["end_at"]) for s in opt["sessions"])
    assert all(a[1] <= b[0] for a, b in zip(ivs, ivs[1:], strict=False))
    plan = client.post(f"{API}/planner/accept", json={"preview_id": opt["preview_id"], "input_revision": opt["input_revision"]})
    assert plan.status_code == 200


# ---- T35 / T24 -------------------------------------------------------------------------------
MC = {"type": "multiple_choice", "prompt": "Lois continues", "options": [{"id": "n", "text": "N"}, {"id": "p", "text": "P"},
      {"id": "e", "text": "E"}], "answer": ["n", "e"], "concept_ids": ["c-laws"]}
OPEN = {"type": "open", "prompt": "Expliquer le TCL", "answer": "rubric", "concept_ids": ["c-tcl"], "points": 2}


def test_partial_credit_is_explicit_and_open_answers_graded_after_submit(client):
    exact = quiz(client, [MC])
    _, res = answer_and_submit(client, exact, [["n"]])
    assert res["score"] == 0  # exact set by default
    partial = quiz(client, [MC | {"partial_credit": True}, OPEN])
    a, res = answer_and_submit(client, partial, [["n", "p"], "Somme normalisée → N(0,1)"])
    assert res["score"] == 0 and res["pending_review"] == 1  # (1 correct − 1 wrong)/2 = 0
    a2, res2 = answer_and_submit(client, partial, [["n"], "..."])
    assert res2["score"] == 0.5

    open_q = partial["questions"][1]["id"]
    ai = client.post(f"{API}/attempts/{a['id']}/items/{open_q}/grade", json={"score": 0.8, "grader": "ai_provisional"}).json()
    assert ai["pending_review"] == 0 and ai["mastery_updates"] == []  # provisional AI: no evidence
    assert client.get(f"{API}/mastery", params={"concept_id": "c-tcl"}).json() == []
    t = client.post(f"{API}/attempts/{a['id']}/items/{open_q}/grade", json={"score": 0.5, "grader": "teacher", "feedback": "incomplet"}).json()
    assert t["score"] == 1.0 and t["mastery_updates"][0]["concept_id"] == "c-tcl"
    item = next(i for i in t["items"] if i["question_id"] == open_q)
    assert item["grader"] == "teacher" and item["feedback"] == "incomplet"
    assert client.post(f"{API}/attempts/{a['id']}/items/{open_q}/grade", json={"score": 1}).status_code == 409


def test_exam_mode_errors_recommendations_and_lock(client):
    exam = quiz(client, [{"type": "true_false", "prompt": "Var ≥ 0", "answer": True, "concept_ids": ["c-var"]}, MC],
                type="exam", time_limit_min=20)
    a = client.post(f"{API}/quizzes/{exam['id']}/attempts", json={"mode": "exam"}).json()
    lock = client.get(f"{API}/exam-mode").json()
    assert lock["active"] and "tutor" in lock["blocked_features"]
    client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [
        {"question_id": exam["questions"][0]["id"], "response": False}]})
    res = client.post(f"{API}/attempts/{a['id']}/submit").json()
    assert {r["concept_id"] for r in res["recommendations"]} == {"c-var", "c-laws"}
    assert client.get(f"{API}/exam-mode").json()["active"] is False
    errors = client.get(f"{API}/errors").json()
    assert {e["concept_id"] for e in errors} == {"c-var", "c-laws"} and all(e["source"] == "exam" for e in errors)

    # Same question failed again in a second exam → recurring error → planner consolidation need.
    a2 = client.post(f"{API}/quizzes/{exam['id']}/attempts", json={"mode": "exam"}).json()
    client.post(f"{API}/attempts/{a2['id']}/submit")
    rec = [e for e in client.get(f"{API}/errors").json() if e["recurring"]]
    assert len(rec) == 2 and rec[0]["occurrences"] == 2
    profile(client)
    keys = {s["need_key"] for s in client.post(f"{API}/planner/preview", json={}).json()["sessions"]}
    assert "errors:c-var:2" in keys
    eid = next(e["id"] for e in rec if e["concept_id"] == "c-var")
    client.patch(f"{API}/errors/{eid}", json={"resolved": True, "category": "careless", "remediation": "relire"})
    keys = {s["need_key"] for s in client.post(f"{API}/planner/preview", json={}).json()["sessions"]}
    assert "errors:c-var:2" not in keys


def test_practice_errors_logged_on_request_only(client):
    q = quiz(client, [{"type": "true_false", "prompt": "x", "answer": True, "concept_ids": ["c1"]}])
    a, _ = answer_and_submit(client, q, [False])
    assert client.get(f"{API}/errors").json() == []
    r = client.post(f"{API}/attempts/{a['id']}/errors", json={}).json()
    assert r["errors_logged"] == 1 and client.get(f"{API}/errors").json()[0]["source"] == "quiz"


# ---- T23 Focus Room ------------------------------------------------------------------------
def test_focus_room_state_machine_and_session_link(client):
    profile(client)
    client.post(f"{API}/tasks", json={"title": "Rapport", "estimated_min": 90, "deadline_at": "2026-12-03T18:00:00+01:00"})
    p = client.post(f"{API}/planner/preview", json={}).json()
    plan = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}).json()
    sid = plan["sessions"][0]["id"]
    clock = clock_of(client)
    clock.set(NOW + timedelta(hours=1))
    f = client.post(f"{API}/focus/start", json={"mode": "classic", "study_session_id": sid, "goal": "Intro"}).json()
    assert f["work_min"] == 25 and f["status"] == "working"
    assert client.post(f"{API}/focus/start", json={"mode": "free"}).status_code == 409
    fid = f["id"]
    clock.set(NOW + timedelta(hours=1, minutes=25))
    assert client.post(f"{API}/focus/{fid}/transition", json={"action": "break"}).json()["status"] == "break"
    clock.set(NOW + timedelta(hours=1, minutes=30))
    assert client.post(f"{API}/focus/{fid}/transition", json={"action": "pause"}).status_code == 409
    client.post(f"{API}/focus/{fid}/transition", json={"action": "work"})
    clock.set(NOW + timedelta(hours=1, minutes=40))
    paused = client.post(f"{API}/focus/{fid}/transition", json={"action": "pause"}).json()
    assert client.post(f"{API}/focus/{fid}/transition", json={"action": "pause"}).json()["revision"] == paused["revision"]
    clock.set(NOW + timedelta(hours=2))  # 20 min paused: not counted
    reload = client.get(f"{API}/focus/active").json()
    assert reload["id"] == fid and reload["active_min"] == 35 and reload["break_total_min"] == 5
    client.post(f"{API}/focus/{fid}/transition", json={"action": "resume"})
    client.post(f"{API}/focus/{fid}/transition", json={"action": "extend", "minutes": 10})
    clock.set(NOW + timedelta(hours=2, minutes=10))
    done = client.post(f"{API}/focus/{fid}/finish", json={"outcome": "partial", "reflection": {"objective_met": False, "difficulty": 3}}).json()
    assert done["active_min"] == 45 and done["work_min"] == 35
    s = next(x for x in client.get(f"{API}/plans/current").json()["sessions"] if x["id"] == sid)
    assert s["status"] == "partial" and s["actual_min"] == 45
    assert client.get(f"{API}/tasks").json()["items"][0]["remaining_min"] == 45
    assert client.get(f"{API}/focus/active").json() is None
    assert client.post(f"{API}/focus/start", json={"mode": "custom"}).status_code == 422


# ---- T22 notes -----------------------------------------------------------------------------
def test_notes_versions_search_and_isolation(client, as_bob):
    nb = client.post(f"{API}/notebooks", json={"title": "Stat", "subject_id": "stat"}).json()
    n = client.post(f"{API}/notes", json={"notebook_id": nb["id"], "title": "TCL", "markdown": "$\\bar X_n$",
                                          "concept_id": "c-tcl", "citations": [{"document_id": "doc-1", "excerpt": "..."}]}).json()
    assert n["revision"] == 1 and n["citations_resolved"] is False
    client.patch(f"{API}/notes/{n['id']}", json={"expected_revision": 1, "markdown": "Théorème central limite"})
    assert client.patch(f"{API}/notes/{n['id']}", json={"expected_revision": 1, "title": "x"}).status_code == 409
    versions = client.get(f"{API}/notes/{n['id']}/versions").json()
    assert [v["revision"] for v in versions] == [1, 2] and versions[0]["markdown"] == "$\\bar X_n$"
    assert [x["id"] for x in client.get(f"{API}/notes", params={"q": "central"}).json()] == [n["id"]]
    assert client.get(f"{API}/notebooks").json()[0]["note_count"] == 1
    assert client.get(f"{API}/notes/{n['id']}", headers=as_bob).status_code == 404
    assert client.post(f"{API}/notes", json={"notebook_id": nb["id"], "title": "x"}, headers=as_bob).status_code == 404
    assert client.delete(f"{API}/notes/{n['id']}").status_code == 204


# ---- T25 notifications ---------------------------------------------------------------------
def test_notifications_dedupe_settings_and_read_state(client):
    profile(client)
    client.post(f"{API}/events", json={"type": "exam", "title": "Exam Stat", "start_at": "2026-12-01T08:30:00+01:00",
                                       "end_at": "2026-12-01T10:30:00+01:00"})
    client.post(f"{API}/tasks", json={"title": "Rendu", "estimated_min": 30, "deadline_at": "2026-11-30T20:00:00+01:00"})
    first = client.get(f"{API}/notifications").json()
    types = sorted(n["type"] for n in first["items"])
    assert types == ["deadline_soon", "deadline_soon", "new_assessment"]
    assert client.get(f"{API}/notifications").json()["unread_count"] == 3  # idempotent refresh
    client.patch(f"{API}/notifications/{first['items'][0]['id']}", json={"read": True})
    assert client.get(f"{API}/notifications", params={"unread_only": True}).json()["unread_count"] == 2
    client.patch(f"{API}/profile", json={"preferences": {"notification_settings": {"task_overdue": False}}})
    clock_of(client).set(NOW + timedelta(days=2))
    types = [n["type"] for n in client.get(f"{API}/notifications").json()["items"]]
    assert "task_overdue" not in types
    assert client.post(f"{API}/notifications/read-all").json()["marked_read"] >= 2


# ---- schema upgrade helper -----------------------------------------------------------------
def test_existing_database_gains_new_columns(tmp_path):
    path = tmp_path / "old.db"
    url = f"sqlite:///{path.as_posix()}"
    app = make_app(url)
    TestClient(app).get(f"{API}/profile")
    app.state.db.engine.dispose()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE preferences DROP COLUMN planner_algorithm")  # simulate a pre-P1 database
    con.commit()
    con.close()
    app2 = make_app(url)
    body = TestClient(app2).get(f"{API}/profile").json()
    assert body["preferences"]["planner_algorithm"] == "greedy"
    app2.state.db.engine.dispose()
