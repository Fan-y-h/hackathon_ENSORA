"""Grades P1: rules/validation (T44), saved scenarios (T39), feedback → diagnostics + grade-triggered
replan (T45, R42), trends (R43)."""

from datetime import timedelta

from tests.conftest import NOW, clock_of
from tests.integration.test_grades_api import mark, setup_program

G = "/api/v1/grades"
API = "/api/v1"


def rules_body(nodes, verified=False, **kw):
    return {"verification_status": "verified" if verified else "unverified",
            "source_ref": "Règlement des études 2026 (démo)" if verified else None,
            "rules": [
                {"type": "compensation", "node_id": nodes["ue_ds"]["id"], "value": 10},
                {"type": "pass_threshold", "node_id": nodes["A"]["id"], "value": 10},
                {"type": "pass_threshold", "node_id": nodes["B"]["id"], "value": 10},
                {"type": "eliminatory_minimum", "node_id": nodes["B"]["id"], "value": 6},
            ], **kw}


def test_rules_versioning_and_official_status_only_when_verified(client):
    pid, nodes = setup_program(client)
    assert client.get(f"{G}/programs/{pid}/rules").json()["verification_status"] == "none"
    mark(client, nodes["A"]["id"], 14)
    mark(client, nodes["B"]["id"], 8)
    v1 = client.put(f"{G}/programs/{pid}/rules", json=rules_body(nodes)).json()
    assert v1["version"] == 1 and len(v1["rules"]) == 4
    val = {n["node_key"]: n for n in client.get(f"{G}/validation", params={"program_id": pid}).json()["nodes"]}
    assert val["B"]["computed_status"] == "validated_by_compensation"
    assert val["B"]["official_status"] == "unknown"  # unverified rules never report an official pass

    missing = client.put(f"{G}/programs/{pid}/rules", json={**rules_body(nodes, verified=True), "source_ref": ""})
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "missing_provenance"
    assert client.put(f"{G}/programs/{pid}/rules", json={**rules_body(nodes, verified=True), "expected_version": 0}).status_code == 409
    v2 = client.put(f"{G}/programs/{pid}/rules", json=rules_body(nodes, verified=True, expected_version=1)).json()
    assert v2["version"] == 2 and v2["verification_status"] == "verified"
    val = {n["node_key"]: n for n in client.get(f"{G}/validation", params={"program_id": pid}).json()["nodes"]}
    assert val["B"]["official_status"] == "validated" and val["B"]["via_compensation"] is True

    bad = client.put(f"{G}/programs/{pid}/rules", json={"rules": [{"type": "compensation", "node_id": nodes["A"]["id"], "value": 10}]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_rules"


def test_verified_risk_feeds_signals(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 12)
    before = {s["node_key"]: s for s in client.get(f"{G}/signals", params={"program_id": pid}).json()["signals"]}
    assert before["B"]["score"] is None  # no objective, no verified rules
    client.put(f"{G}/programs/{pid}/rules", json=rules_body(nodes, verified=True))
    after = {s["node_key"]: s for s in client.get(f"{G}/signals", params={"program_id": pid}).json()["signals"]}
    assert after["B"]["score"] is not None and "VERIFIED_RULE_RISK" in after["B"]["reason_codes"]


def test_saved_scenarios_never_touch_actual_marks(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    client.post(f"{G}/objectives", json={"program_id": pid, "node_id": nodes["ue_ds"]["id"], "target": 12})
    rev = client.get(f"{G}/programs").json()[0]["revision"]
    sc = client.post(f"{G}/scenarios", json={"program_id": pid, "name": "Pessimiste",
                                             "values": [{"node_id": nodes["B"]["id"], "value": 8},
                                                        {"node_id": nodes["C"]["id"], "value": 9}]}).json()
    assert sc["stale"] is False and len(sc["values"]) == 2
    assert client.get(f"{G}/programs").json()[0]["revision"] == rev  # scenarios don't bump the program
    ev = client.get(f"{G}/scenarios/{sc['id']}/evaluate").json()
    proj = {p["node_key"]: p for p in ev["projections"]}
    assert proj["ue_ds"]["projected"] == "10.8571429" and ev["dropped_values"] == []
    assert len(client.get(f"{G}/records", params={"program_id": pid}).json()) == 1

    mark(client, nodes["B"]["id"], 15)  # the real mark arrives
    ev2 = client.get(f"{G}/scenarios/{sc['id']}/evaluate").json()
    assert [v["node_id"] for v in ev2["dropped_values"]] == [nodes["B"]["id"]]
    assert "SCENARIO_VALUES_SUPERSEDED_BY_ACTUAL_MARKS" in ev2["warnings"]
    assert "SCENARIO_BASED_ON_OLDER_REVISION" in ev2["warnings"]
    assert client.get(f"{G}/scenarios", params={"program_id": pid}).json()[0]["stale"] is True
    renamed = client.patch(f"{G}/scenarios/{sc['id']}", json={"expected_revision": 1, "name": "Réaliste",
                                                             "values": [{"node_id": nodes["C"]["id"], "value": 11}]}).json()
    assert renamed["name"] == "Réaliste" and renamed["stale"] is False
    assert client.post(f"{G}/scenarios", json={"program_id": pid, "name": "x",
                                               "values": [{"node_id": nodes["A"]["id"], "value": 5}]}).status_code == 422
    assert client.delete(f"{G}/scenarios/{sc['id']}").status_code == 204


def test_trends(client):
    pid, nodes = setup_program(client)
    client.post(f"{G}/records", json={"node_id": nodes["A"]["id"], "value": 14, "observed_at": "2026-10-01T10:00:00Z"})
    client.post(f"{G}/records", json={"node_id": nodes["B"]["id"], "value": 7, "observed_at": "2026-11-01T10:00:00Z"})
    series = {s["node_key"]: s["points"] for s in client.get(f"{G}/trends", params={"program_id": pid}).json()["series"]}
    assert [p["provisional"] for p in series["ue_ds"]] == ["14", "11"]
    assert [p["coverage"] for p in series["ue_ds"]] == ["0.5714286", "1"]


def _profile(client):
    client.patch(f"{API}/profile", json={"cohort": "2A", "preferences": {
        "work_windows": [{"weekday": d, "start": "14:00", "end": "18:00"} for d in range(7)],
        "daily_max_min": 180}})


def test_low_grade_triggers_diagnostic_and_plan_outdated(client):
    _profile(client)
    pid, nodes = setup_program(client)
    client.post(f"{G}/objectives", json={"program_id": pid, "node_kind": "unit", "target": 12})
    p = client.post(f"{API}/planner/preview", json={}).json()
    client.post(f"{API}/planner/accept", json={"preview_id": p["preview_id"], "input_revision": p["input_revision"]})
    assert client.get(f"{API}/planner/status").json()["dirty"] is False

    rec = mark(client, nodes["B"]["id"], 7)  # below the UE objective of 12
    st = client.get(f"{API}/planner/status").json()
    assert st["dirty"] and st["changed_inputs"] == ["grades"] and st["suggested_reason"] == "grades_changed"
    rp = client.post(f"{API}/planner/replan", json={"base_plan_revision": st["plan_revision"], "reason": "grades_changed"}).json()
    diag = [s for s in rp["sessions"] if s["need_key"] == f"diagnostic:{rec['id']}"]
    assert diag and diag[0]["activity_type"] == "diagnostic" and diag[0]["subject_id"] == "subj-b"
    assert diag[0]["generation_spec"]["artifact_type"] == "quiz"
    assert client.get(f"{API}/mastery").json() == []  # a global mark never writes mastery

    # Reported difficulties replace the generic diagnostic with targeted remediation.
    fb = client.post(f"{G}/records/{rec['id']}/feedback", json={
        "flags": ["calculation", "formula_recall"], "concept_ids": ["c-int"], "corrected_document_id": "doc-1"}).json()
    assert [a["activity_type"] for a in fb["proposal"]] == ["practice", "active_recall"]
    assert any("consent" in n for n in fb["notes"])
    keys = {s["need_key"] for s in client.post(f"{API}/planner/preview", json={}).json()["sessions"]}
    assert f"feedback:{fb['id']}:calculation" in keys and f"diagnostic:{rec['id']}" not in keys
    client.patch(f"{G}/feedback/{fb['id']}", json={"status": "dismissed"})
    keys = {s["need_key"] for s in client.post(f"{API}/planner/preview", json={}).json()["sessions"]}
    assert not any(k.startswith(("feedback:", "diagnostic:")) for k in keys)
    assert len(client.get(f"{G}/feedback", params={"program_id": pid}).json()) == 1

    clock_of(client).set(NOW + timedelta(days=40))  # old marks stop generating diagnostics
    keys = {s["need_key"] for s in client.post(f"{API}/planner/preview", json={}).json()["sessions"]}
    assert not any(k.startswith("diagnostic:") for k in keys)
