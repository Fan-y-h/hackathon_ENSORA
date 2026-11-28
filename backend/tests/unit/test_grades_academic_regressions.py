"""Comprehensive regression tests for Grade Tracker, Pamplemousse bridge & Academic Optimizer."""

from __future__ import annotations

from decimal import Decimal as D

from fastapi.testclient import TestClient

from app.modules.grades import domain, validation_rules
from app.modules.grades.source_results import build_results, import_results_into_program
from tests.integration.test_grades_api import mark, setup_program

G = "/api/v1/grades"
API = "/api/v1"


def test_exact_rational_arithmetic_eliminates_repeating_decimal_drift():
    """3 equal leaves (1/3 each) all graded 10/20 must equal EXACTLY 10/20 (never 9.99999... < 10)."""
    tree = domain.build_tree([
        domain.NodeSpec("ue", None, "unit", "UE"),
        domain.NodeSpec("a", "ue", "subject", "A", D(1)),
        domain.NodeSpec("b", "ue", "subject", "B", D(1)),
        domain.NodeSpec("c", "ue", "subject", "C", D(1)),
    ])
    marks = {"a": D(10), "b": D(10), "c": D(10)}
    summ = domain.summarize(tree, marks, "ue")
    assert summ.final == D(10)

    req = domain.required(tree, marks, domain.Objective("o1", "ue", D(10)))
    assert req.feasibility == "achieved"
    assert req.required_mean is None

    evals = validation_rules.evaluate(tree, marks, [validation_rules.Rule("pass_threshold", "ue", D(10))])
    assert evals["ue"].computed_status == "validated"


def test_prompt_golden_case_decimal_coefficients_10_6667():
    """Subject A coef 2 mark 13, Subject B coef 1.5 unknown, UE target 12 -> required B = 10.6667."""
    tree = domain.build_tree([
        domain.NodeSpec("ue", None, "unit", "UE Stat"),
        domain.NodeSpec("A", "ue", "subject", "Matière A", D("2")),
        domain.NodeSpec("B", "ue", "subject", "Matière B", D("1.5")),
    ])
    req = domain.required(tree, {"A": D(13)}, domain.Objective("obj", "ue", D(12)))
    assert req.feasibility == "feasible"
    assert domain.q(req.required_mean) == D("10.6666667")
    assert round(req.required_mean, 4) == D("10.6667")  # type: ignore[arg-type]


def test_full_hierarchy_subdivided_subject_and_weight_modification(client: TestClient):
    """Year -> Semester -> UE -> Subject (coef 3) -> Final 50%, Midterm 25%, 8 Quizzes 25%."""
    p = client.post(f"{G}/programs", json={"title": "ENSAI 2A", "academic_year": "2026-2027", "cohort": "2A"}).json()
    nodes = [
        {"key": "y26", "kind": "year", "title": "2026-2027"},
        {"key": "s1", "parent_key": "y26", "kind": "semester", "title": "S1", "weight": "1"},
        {"key": "ue1", "parent_key": "s1", "kind": "unit", "title": "UE Data", "weight": "5", "ects": "6"},
        {"key": "ml", "parent_key": "ue1", "kind": "subject", "title": "ML", "weight": "3", "ects": "4", "weight_mode": "fraction"},
        {"key": "final", "parent_key": "ml", "kind": "assessment", "title": "Examen final", "weight": "0.5"},
        {"key": "mid", "parent_key": "ml", "kind": "assessment", "title": "Midterm", "weight": "0.25"},
        {"key": "qgrp", "parent_key": "ml", "kind": "assessment_group", "title": "Quiz", "weight": "0.25", "weight_mode": "fraction"},
        *[{"key": f"q{i}", "parent_key": "qgrp", "kind": "assessment", "title": f"Quiz {i}", "weight": "0.125"} for i in range(1, 9)],
        {"key": "stat", "parent_key": "ue1", "kind": "subject", "title": "Stat", "weight": "2", "ects": "2"},
    ]
    cur = client.put(f"{G}/programs/{p['id']}/curriculum", json={"nodes": nodes})
    assert cur.status_code == 200, cur.text
    by_key = {n["key"]: n for n in cur.json()["nodes"]}
    # Academic coefficient (3) is separate from ECTS (4); each quiz = 0.25 * 0.125 = 0.03125 of ML.
    assert by_key["ml"]["weight"] == "3" and by_key["ml"]["ects"] == "4"
    assert by_key["q1"]["weight_in_parent"] == "0.125"
    # ML is 3/5 = 0.6 of UE1, so each quiz is 0.6 * 0.03125 = 0.01875 of the year!
    assert by_key["q1"]["weight_in_root"] == "0.01875"


def test_unknown_coefficient_never_silently_replaced_and_manual_override_persists(client: TestClient):
    p = client.post(f"{G}/programs", json={"title": "Pamplemousse"}).json()
    cur = client.put(
        f"{G}/programs/{p['id']}/curriculum",
        json={
            "nodes": [
                {"key": "ue", "kind": "unit", "title": "UE"},
                {"key": "A", "parent_key": "ue", "kind": "subject", "title": "A", "weight": "2", "ects": "3", "weight_provenance": "official"},
                {"key": "B", "parent_key": "ue", "kind": "subject", "title": "B", "weight": None, "ects": "2", "weight_provenance": "unknown"},
            ]
        },
    )
    assert cur.status_code == 200, cur.text
    by_key = {n["key"]: n for n in cur.json()["nodes"]}
    assert by_key["B"]["weight"] is None and by_key["B"]["weight_status"] == "unknown" and by_key["B"]["ects"] == "2"
    assert by_key["A"]["weight_in_parent"] is None  # cannot normalize while sibling B is unknown!

    mark(client, by_key["A"]["id"], "14")
    client.post(f"{G}/objectives", json={"program_id": p["id"], "node_id": by_key["ue"]["id"], "target": 12})

    # Summary must NOT invent a fake UE average treating B's unknown coef as 0 or 1.
    summ = {n["node_key"]: n for n in client.get(f"{G}/summary", params={"program_id": p["id"]}).json()["nodes"]}
    assert summ["ue"]["weight_complete"] is False
    assert summ["ue"]["provisional"] is None
    assert summ["ue"]["unknown_weight_keys"] == ["B"]
    assert summ["A"]["provisional"] == "14"  # A itself is still computed accurately!

    req = client.post(f"{G}/required", json={"program_id": p["id"]}).json()["requirements"][0]
    assert req["feasibility"] == "unknown" and req["required_mean"] is None and "MISSING_COEFFICIENT" in req["reason_codes"]

    # Student persistently enters the missing coefficient manually (provenance becomes "manual").
    patched = client.patch(f"{G}/nodes/{by_key['B']['id']}", json={"weight": "1.5"})
    assert patched.status_code == 200, patched.text
    assert patched.json()["weight"] == "1.5"
    assert patched.json()["weight_status"] == "known"
    assert patched.json()["weight_provenance"] == "manual"

    # Now UE summary and required mark compute deterministically!
    summ2 = {n["node_key"]: n for n in client.get(f"{G}/summary", params={"program_id": p["id"]}).json()["nodes"]}
    assert summ2["ue"]["weight_complete"] is True and summ2["ue"]["provisional"] == "14"
    req2 = client.post(f"{G}/required", json={"program_id": p["id"]}).json()["requirements"][0]
    assert req2["feasibility"] == "feasible"
    # (12 * 3.5 - 2 * 14) / 1.5 = 14 / 1.5 = 9.3333333
    assert req2["required_mean"] == "9.3333333"


def test_absences_dispenses_and_retakes(client: TestClient):
    pid, nodes = setup_program(client)
    # A (coef 4) = 12, B (coef 3) = dispensed -> UE mean is solely A = 12 (coverage 1, final 12)!
    mark(client, nodes["A"]["id"], "12")
    disp = client.post(f"{G}/records", json={"node_id": nodes["B"]["id"], "grade_status": "dispensed"})
    assert disp.status_code == 201, disp.text
    summ = {n["node_key"]: n for n in client.get(f"{G}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["ue_ds"]["final"] == "12" and summ["ue_ds"]["coverage"] == "1"

    # Change B to unjustified absence (ABI) -> counts as 0!
    client.patch(f"{G}/records/{disp.json()['id']}", json={"grade_status": "absent_unjustified"})
    summ2 = {n["node_key"]: n for n in client.get(f"{G}/summary", params={"program_id": pid}).json()["nodes"]}
    # (4*12 + 3*0)/7 = 48/7 = 6.8571429
    assert summ2["ue_ds"]["final"] == "6.8571429"

    # Student passes retake (rattrapage) in B with 14/20 -> replaces ABI while keeping original record!
    retake = client.post(f"{G}/records", json={"node_id": nodes["B"]["id"], "kind": "retake", "value": "14"})
    assert retake.status_code == 201, retake.text
    summ3 = {n["node_key"]: n for n in client.get(f"{G}/summary", params={"program_id": pid}).json()["nodes"]}
    # (4*12 + 3*14)/7 = 90/7 = 12.8571429
    assert summ3["ue_ds"]["final"] == "12.8571429"
    # Original actual record is still preserved in history.
    recs = [r for r in client.get(f"{G}/records", params={"program_id": pid}).json() if r["node_key"] == "B"]
    assert {r["kind"] for r in recs} == {"actual", "retake"}


def test_compensation_impossible_when_group_bound_max_below_threshold():
    tree = domain.build_tree([
        domain.NodeSpec("ue", None, "unit", "UE"),
        domain.NodeSpec("A", "ue", "subject", "A", D(5)),
        domain.NodeSpec("B", "ue", "subject", "B", D(1)),
    ])
    rules = [
        validation_rules.Rule("compensation", "ue", D(10)),
        validation_rules.Rule("pass_threshold", "A", D(10)),
        validation_rules.Rule("pass_threshold", "B", D(10)),
    ]
    # A=4 (coef 5), B ungraded (coef 1). Max UE average even with B=20 is (20+20)/6 = 6.67 < 10!
    evals = validation_rules.evaluate(tree, {"A": D(4)}, rules)
    assert evals["A"].computed_status == "not_validated"
    assert "COMPENSATION_IMPOSSIBLE" in evals["A"].reason_codes
    assert "COMPENSATION_STILL_POSSIBLE" not in evals["A"].reason_codes


def test_pamplemousse_bridge_imports_coefficients_preserves_manual_edits_and_feeds_optimizer(client: TestClient):
    records = {
        "y": {"kind": "academic_year", "title": "2026-2027", "parent_key": None},
        "s1": {"kind": "semester", "title": "S1", "parent_key": "y"},
        "ue1": {"kind": "teaching_unit", "title": "UE2-01 Stat", "ects": "6", "parent_key": "s1"},
        "ue1:c": {"kind": "coefficient", "level": "teaching_unit", "weight": None, "parent_key": "ue1"},
        "sub1": {"kind": "subject", "title": "Régression", "code": "2ASTA02", "ects": "4", "parent_key": "ue1"},
        "sub1:c": {"kind": "coefficient", "level": "subject", "weight": "4", "parent_key": "sub1"},
        "sub1:g": {"kind": "grade", "value": "13", "maximum": "20", "grade_kind": "official_aggregate", "parent_key": "sub1"},
        "sub2": {"kind": "subject", "title": "Séries temporelles", "code": "2ASTA05", "ects": "2", "parent_key": "ue1"},
        "sub2:c": {"kind": "coefficient", "level": "subject", "weight": "3", "parent_key": "sub2"},
        "asmt_cc": {"kind": "assessment", "title": "CC", "parent_key": "sub2"},
        "asmt_cc:c": {"kind": "coefficient", "level": "assessment", "weight": "0.5", "parent_key": "asmt_cc"},
        "asmt_q1": {"kind": "assessment", "title": "Quiz 1", "parent_key": "asmt_cc"},
        "asmt_q1:c": {"kind": "coefficient", "level": "assessment", "weight": "1", "parent_key": "asmt_q1"},
        "asmt_ex": {"kind": "assessment", "title": "Examen", "parent_key": "sub2"},
        "asmt_ex:c": {"kind": "coefficient", "level": "assessment", "weight": "0.5", "parent_key": "asmt_ex"},
    }
    results = build_results(records)
    # Nested assessments are NOT flattened!
    sub2_res = results.semesters[0].units[0].subjects[1]
    assert len(sub2_res.assessments) == 2
    assert len(sub2_res.assessments[0].children) == 1
    assert results.semesters[0].units[0].ects == 6.0 and results.semesters[0].units[0].coefficient is None

    # Import into Grade Tracker database.
    with client.app.state.db.session_factory() as db:
        from app.core.identity import ensure_user

        uid = ensure_user(db, "alice@test.local", "Alice").id
        report = import_results_into_program(db, uid, results)
        db.commit()
    assert report.created_nodes >= 7 and report.created_records == 1
    assert len(report.unknown_coefficient_nodes) == 2  # UE and semester weights are unknown

    # Official data is immutable through the manual API.
    (official,) = client.get(f"{G}/records", params={"program_id": report.program_id}).json()
    assert official["provenance"] == "pamplemousse"
    for response in (
        client.patch(f"{G}/records/{official['id']}", json={"value": "14"}),
        client.delete(f"{G}/records/{official['id']}"),
        client.patch(f"{G}/nodes/{official['node_id']}", json={"scale_max": "100"}),
        client.post(f"{G}/records", json={"node_id": official["node_id"], "kind": "retake", "value": "14"}),
    ):
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "official_grade_read_only"

    # Manually enter UE1 coefficient persistently.
    cur = client.get(f"{G}/programs/{report.program_id}/curriculum").json()
    ue_node = next(n for n in cur["nodes"] if n["kind"] == "unit")
    assert ue_node["weight"] is None and ue_node["weight_status"] == "unknown" and ue_node["ects"] == "6"
    client.patch(f"{G}/nodes/{ue_node['id']}", json={"weight": "5"})

    # Re-importing Pamplemousse is idempotent and PRESERVES the manual UE coefficient!
    with client.app.state.db.session_factory() as db:
        from app.core.identity import ensure_user

        uid = ensure_user(db, "alice@test.local", "Alice").id
        report2 = import_results_into_program(db, uid, results)
        db.commit()
    assert report2.created_nodes == 0 and report2.preserved_manual_nodes == 1
    cur2 = client.get(f"{G}/programs/{report.program_id}/curriculum").json()
    ue_node2 = next(n for n in cur2["nodes"] if n["kind"] == "unit")
    assert ue_node2["weight"] == "5" and ue_node2["weight_provenance"] == "manual"

    # Set UE objective = 12 -> required mark on 2ASTA05 = 10.6666667 and AcademicSignal feeds planner!
    client.post(f"{G}/objectives", json={"program_id": report.program_id, "node_id": ue_node2["id"], "target": 12})
    req = client.post(f"{G}/required", json={"program_id": report.program_id}).json()["requirements"][0]
    assert req["required_mean"] == "10.6666667" and req["feasibility"] == "feasible"
    sigs = {s["subject_id"]: s for s in client.get(f"{G}/signals", params={"program_id": report.program_id}).json()["signals"]}
    assert sigs["2ASTA05"]["score"] == "1" and sigs["2ASTA02"]["score"] == "0"

    # Academic Optimizer time allocation includes explanation & disclaimer (not a guarantee).
    alloc = client.post(f"{G}/time-allocation", json={"program_id": report.program_id, "available_min": 180}).json()
    assert alloc["allocations"][0]["subject_id"] == "2ASTA05"
    assert alloc["allocations"][0]["optimized_min"] == 180
    assert "explanation" in alloc["allocations"][0]
    assert any("guarantee" in n.lower() for n in alloc["notes"])
