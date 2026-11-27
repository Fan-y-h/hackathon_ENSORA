"""P2 + remaining Person-3 features through the API."""

from datetime import datetime, timedelta

from sqlalchemy import func, select

from app.modules.learning.models import Evidence
from app.modules.notifications import service as notif
from tests.conftest import NOW, clock_of
from tests.integration.test_grades_api import mark, setup_program

API = "/api/v1"
G = "/api/v1/grades"
WINDOWS = [{"weekday": d, "start": "14:00", "end": "18:00"} for d in range(7)]


def profile(client, prefs=None, **extra):
    r = client.patch(f"{API}/profile", json={"cohort": "2A", **extra,
                                             "preferences": {"work_windows": WINDOWS, **(prefs or {})}})
    assert r.status_code == 200, r.text
    return r.json()


def tf(concept, dimension="theory"):
    return {"type": "true_false", "prompt": f"{concept}?", "answer": True, "concept_ids": [concept], "dimension": dimension}


def run_quiz(client, questions, responses, **kw):
    q = client.post(f"{API}/quizzes", json={"title": "Q", "subject_id": "stat", "questions": questions, **kw}).json()
    a = client.post(f"{API}/quizzes/{q['id']}/attempts", json={}).json()
    client.patch(f"{API}/attempts/{a['id']}/responses", json={"responses": [
        {"question_id": q["questions"][i]["id"], "response": r} for i, r in enumerate(responses)]})
    return q, client.post(f"{API}/attempts/{a['id']}/submit").json()


def exam(client, concept="c1"):
    ev = client.post(f"{API}/events", json={"type": "exam", "title": "Exam", "subject_id": "stat",
                                            "start_at": "2026-12-05T08:30:00+01:00", "end_at": "2026-12-05T10:30:00+01:00"}).json()
    return ev


# ---- T33 -----------------------------------------------------------------------------------
def test_retention_autonomy_and_calibration(client):
    run_quiz(client, [tf("c1", "application")], [True])
    dims = {m["dimension"] for m in client.get(f"{API}/mastery", params={"concept_id": "c1"}).json()}
    assert dims == {"application", "autonomy"}  # unassisted application → autonomy, no retention yet
    clock_of(client).set(NOW + timedelta(days=3))
    run_quiz(client, [tf("c1", "application")], [False])
    dims = {m["dimension"] for m in client.get(f"{API}/mastery", params={"concept_id": "c1"}).json()}
    assert "retention" in dims  # delayed recall
    rep = client.get(f"{API}/learning/calibration").json()
    assert rep["samples"] == 1 and rep["sufficient"] is False  # first attempt had no prior prediction
    assert rep["by_kind"]["quiz"]["samples"] == 1


def test_mock_exam_from_bank_prefers_coverage_and_past_papers(client):
    client.post(f"{API}/quizzes", json={"title": "Annale 2025", "subject_id": "stat", "origin": "historical_exam",
                                        "questions": [tf("c1"), tf("c2")]})
    client.post(f"{API}/quizzes", json={"title": "TD", "subject_id": "stat", "questions": [tf("c1"), tf("c3"), tf("c1")]})
    mock = client.post(f"{API}/quizzes/mock", json={"subject_id": "stat", "question_count": 3, "time_limit_min": 30,
                                                     "concept_ids": ["c1", "c2", "c3", "c9"]}).json()
    assert mock["type"] == "exam" and mock["time_limit_min"] == 30 and mock["question_count"] == 3
    assert {c for q in mock["questions"] for c in q["concept_ids"]} == {"c1", "c2", "c3"}
    assert "c9" in mock["title"]
    assert "answer" not in str(mock)
    assert client.post(f"{API}/quizzes/mock", json={"subject_id": "none"}).status_code == 422


def test_readiness_heatmap_and_mastery_map(client):
    ev = exam(client)
    client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"scope_status": "confirmed", "concept_ids": ["c1", "c2"]})
    run_quiz(client, [tf("c1")], [True])
    r = client.get(f"{API}/analytics/readiness").json()["assessments"][0]
    assert r["readiness"]["estimate"] == 1.0 and r["readiness"]["coverage"] == 0.5 and r["readiness"]["known"] == 1
    assert r["weakest_concepts"][0]["concept_id"] == "c2" and r["weakest_concepts"][0]["score"] is None
    h = client.get(f"{API}/analytics/heatmap", params={"start": "2026-11-28T00:00:00+01:00", "end": "2026-12-02T00:00:00+01:00"}).json()
    assert h["days"][0]["evidence"] == 1 and h["regularity"]["days_in_range"] == 4
    mm = client.get(f"{API}/analytics/mastery-map").json()
    assert mm["concepts"][0]["concept_id"] == "c1" and "theory" in mm["concepts"][0]["dimensions"]


# ---- R12 / R01 / estimates -------------------------------------------------------------------
def test_prerequisites_order_planning_and_reject_cycles(client):
    profile(client)
    ev = exam(client)
    client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"scope_status": "confirmed", "concept_ids": ["a-base", "z-adv"]})
    bad = client.put(f"{API}/concepts/prerequisites", json={"edges": [
        {"prerequisite_id": "a", "dependent_id": "b"}, {"prerequisite_id": "b", "dependent_id": "a"}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "prerequisite_cycle"
    client.put(f"{API}/concepts/prerequisites", json={"edges": [{"prerequisite_id": "z-adv", "dependent_id": "a-base"},
                                                               {"prerequisite_id": "x-out", "dependent_id": "a-base"}]})
    p = client.post(f"{API}/planner/preview", json={}).json()
    order = [s["need_key"].split(":")[-1] for s in p["sessions"]]
    assert order.index("z-adv") < order.index("a-base")  # stable key order alone would put a-base first
    assert "PREREQUISITE_OUT_OF_SCOPE:x-out->a-base" in p["warnings"]
    assert any("AFTER_PREREQUISITES" in s["reason"] for s in p["sessions"])
    cp = client.post(f"{API}/planner/preview", json={"algorithm": "cpsat"}).json()
    assert any("PREREQUISITES_USE_GREEDY" in w for w in cp["warnings"]) or "CPSAT_UNAVAILABLE_FALLBACK_GREEDY" in cp["warnings"]


def test_self_assessment_shifts_ranking_prior_only(client):
    profile(client, self_assessment={"c-weak": 1, "c-strong": 5}, prefs={"daily_max_min": 60})
    assert client.patch(f"{API}/profile", json={"self_assessment": {"x": 9}}).status_code == 422
    ev = client.post(f"{API}/events", json={"type": "exam", "title": "Exam", "subject_id": "stat",
                                            "start_at": "2026-11-30T08:30:00+01:00", "end_at": "2026-11-30T10:30:00+01:00"}).json()
    client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"scope_status": "confirmed", "concept_ids": ["c-strong", "c-weak"]})
    p = client.post(f"{API}/planner/preview", json={}).json()
    assert p["sessions"][0]["need_key"].endswith("c-weak")
    assert client.get(f"{API}/mastery").json() == []  # declared confidence never becomes mastery


def test_learned_duration_factor_after_three_completed_sessions(client):
    profile(client)
    clock = clock_of(client)
    for i in range(3):
        t = client.post(f"{API}/tasks", json={"title": f"T{i}", "estimated_min": 60}).json()
        p = client.post(f"{API}/planner/preview", json={}).json()
        plan = client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]}).json()
        s = next(x for x in plan["sessions"] if x["task_id"] == t["id"] and x["status"] == "planned")
        clock.set(datetime.fromisoformat(s["start_at"].replace("Z", "+00:00")))
        client.post(f"{API}/sessions/{s['id']}/start")
        client.post(f"{API}/sessions/{s['id']}/finish", json={"outcome": "completed", "actual_min": int(s["estimated_min"] * 1.5),
                                                            "remaining_min": 0})
    ev = client.post(f"{API}/events", json={"type": "exam", "title": "E", "start_at": "2026-12-20T08:00:00+01:00",
                                            "end_at": "2026-12-20T10:00:00+01:00"}).json()
    client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"scope_status": "confirmed"})
    p = client.post(f"{API}/planner/preview", json={"horizon_days": 31}).json()
    assert "ESTIMATE_ADJUSTED:task_workx1.50" in p["warnings"]
    # Factor applies to activity types with enough history only (here: task_work), not to revision.
    assert sum(s["estimated_min"] for s in p["sessions"] if s["need_key"].startswith("assessment:")) == 180


# ---- T46 corrected paper -----------------------------------------------------------------------
def test_corrected_paper_confirmed_items_create_concept_evidence(client):
    pid, nodes = setup_program(client)
    rec = mark(client, nodes["A"]["id"], 12)
    body = {"items": [{"label": "Ex 1", "earned": 6, "max": 8, "concept_ids": ["c-int"]},
                      {"label": "Ex 2", "earned": 6, "max": 12, "concept_ids": ["c-deriv"], "dimension": "reasoning"}],
            "document_id": "doc-1"}
    draft = client.put(f"{G}/records/{rec['id']}/corrected-paper", json=body).json()
    assert draft["evidence_recorded"] == 0 and "NOT_CONFIRMED_NO_EVIDENCE" in draft["warnings"]
    assert draft["total_on_20"] == "12" and draft["consistent"] is True
    assert "DOCUMENT_KEPT_LOCAL_NO_AI_ANALYSIS" in draft["warnings"]
    done = client.put(f"{G}/records/{rec['id']}/corrected-paper", json={**body, "confirmed": True}).json()
    assert done["evidence_recorded"] == 2
    m = {(x["concept_id"], x["dimension"]): x["score"] for x in client.get(f"{API}/mastery").json()}
    assert m[("c-int", "application")] == 0.75 and m[("c-deriv", "reasoning")] == 0.5
    assert ("c-deriv", "autonomy") in m  # unassisted reasoning also evidences autonomy
    assert client.put(f"{G}/records/{rec['id']}/corrected-paper", json=body).status_code == 409
    with client.app.state.db.session_factory() as s:
        assert s.scalar(select(func.count()).select_from(Evidence).where(Evidence.activity_kind == "corrected_paper")) == 4  # + autonomy
    bad = {"items": [{"label": "x", "earned": 9, "max": 8}]}
    rec2 = mark(client, nodes["B"]["id"], 10)
    assert client.put(f"{G}/records/{rec2['id']}/corrected-paper", json=bad).status_code == 422
    inc = client.put(f"{G}/records/{rec2['id']}/corrected-paper", json={"items": [{"label": "x", "earned": 2, "max": 8}]}).json()
    assert inc["consistent"] is False and "ITEM_TOTAL_DIFFERS_FROM_RECORDED_MARK" in inc["warnings"]


def test_comment_analysis_is_heuristic_and_tentative(client):
    r = client.post(f"{G}/comments/analyze", json={"text": "Erreurs de calcul répétées, formule du TCL oubliée."}).json()
    assert r["ai_used"] is False and {f["flag"] for f in r["proposed_flags"]} == {"calculation", "formula_recall"}
    assert all(f["confirmed"] is False for f in r["proposed_flags"])


# ---- T47 ---------------------------------------------------------------------------------------
def test_forecast_and_time_allocation(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    client.post(f"{G}/objectives", json={"program_id": pid, "node_kind": "unit", "target": 12})
    f = client.post(f"{G}/forecast", json={"program_id": pid, "seed": 3}).json()
    assert f["calibrated"] is False and f["model"]["n_marks"] == 1 and any("Uncalibrated" in n for n in f["notes"])
    ue = {x["node_key"]: x for x in f["forecasts"]}["ue_ds"]
    assert 0 < ue["probability_reach_target"] < 1 and ue["deterministic_min"] <= ue["p50"] <= ue["deterministic_max"]
    assert client.post(f"{G}/forecast", json={"program_id": pid, "seed": 3}).json() == f  # deterministic
    a = client.post(f"{G}/time-allocation", json={"program_id": pid, "available_min": 600}).json()
    rows = {r["node_key"]: r for r in a["allocations"]}
    assert "A" not in rows  # fully graded
    assert sum(r["optimized_min"] for r in rows.values()) == 600 == sum(r["baseline_min"] for r in rows.values())
    assert any("no grade gain" in n for n in a["notes"])


# ---- T21 -------------------------------------------------------------------------------------
def test_decompose_then_apply_subtasks(client):
    prop = client.post(f"{API}/tasks/decompose", json={"text": "1. Lire l'énoncé\n2. Coder le modèle\n3. Rapport à rendre",
                                                     "total_estimated_min": 300}).json()
    assert prop["ai_used"] is False and len(prop["steps"]) == 3 and prop["steps"][2]["deliverable"]
    parent = client.post(f"{API}/tasks", json={"title": "Projet"}).json()
    subs = client.post(f"{API}/tasks/{parent['id']}/subtasks", json={"items": [
        {"title": s["title"], "estimated_min": s["estimated_min"]} for s in prop["steps"]]}).json()
    assert len(subs) == 3 and all(s["parent_task_id"] == parent["id"] for s in subs)


# ---- R30 -------------------------------------------------------------------------------------
def test_account_export_and_delete(client, as_bob):
    client.get(f"{API}/profile")
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    nb = client.post(f"{API}/notebooks", json={"title": "n"}).json()
    client.post(f"{API}/notes", json={"notebook_id": nb["id"], "title": "t", "markdown": "x"})
    client.post(f"{API}/tasks", json={"title": "bob task"}, headers=as_bob)
    data = client.post(f"{API}/account/export").json()
    t = data["tables"]
    assert {"users", "profiles", "grade_records", "academic_nodes", "notes", "note_versions"} <= set(t)
    assert "password_hash" not in t["users"][0] and "tasks" not in t  # bob's task is not exported
    assert client.request("DELETE", f"{API}/account", json={"confirm": "yes"}).status_code == 422
    assert client.request("DELETE", f"{API}/account", json={"confirm": "DELETE MY ACCOUNT"}).status_code == 204
    assert client.get(f"{G}/programs").json() == []  # a fresh empty local profile
    assert len(client.get(f"{API}/tasks", headers=as_bob).json()["items"]) == 1  # other user untouched


# ---- R28 email -------------------------------------------------------------------------------
def test_email_channel_opt_in_and_honest_status(client, monkeypatch):
    profile(client, prefs={"notification_settings": {"email_address": "etudiant@example.org"}})
    client.post(f"{API}/tasks", json={"title": "Rendu", "estimated_min": 30, "deadline_at": "2026-11-30T20:00:00+01:00"})
    n = client.get(f"{API}/notifications").json()
    assert n["items"][0]["email_status"] == "not_configured" and n["channels"] == ["in_app"]
    sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.example.org")
    monkeypatch.setenv("SMTP_FROM", "ensora@example.org")
    monkeypatch.setitem(notif.SENDER, "send", lambda to, subject, body: sent.append((to, subject)))
    client.post(f"{API}/tasks", json={"title": "Autre", "estimated_min": 30, "deadline_at": "2026-11-30T21:00:00+01:00"})
    n = client.get(f"{API}/notifications").json()
    assert sent == [("etudiant@example.org", "[Ensora+] Échéance dans moins de 48 h : Autre")]
    assert n["channels"] == ["in_app", "email"]
    assert client.patch(f"{API}/profile", json={"preferences": {"notification_settings": {"email_address": "nope"}}}).status_code == 422


# ---- catalog port (risk 7) --------------------------------------------------------------------
def test_catalog_port_rejects_malformed_references(client):
    bad = client.post(f"{API}/tasks", json={"title": "t", "subject_id": "bad id!"})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_subject_id"
    ev = exam(client)
    r = client.patch(f"{API}/assessments/{ev['assessment_id']}", json={"concept_ids": ["ok-1", "pas bon"]})
    assert r.status_code == 422 and r.json()["error"]["details"] == ["pas bon"]
    q = client.post(f"{API}/quizzes", json={"title": "q", "questions": [tf("x" * 40)]})
    assert q.status_code == 422 and q.json()["error"]["code"] == "invalid_concept_id"
