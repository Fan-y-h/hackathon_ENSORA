"""QA Section 5 — Independent mathematical verification of Grade Tracker & Academic Optimizer.

Verifies:
- Reference case: Subject 1 coef 2 with 13/20, Subject 2 coef 1.5 missing, target 12 -> 10.6666667/20 (10.6667 rounded 4dp)
- Weighted means, decimal coefficients, deep UE/subject/assessment hierarchies
- Missing vs 0, custom coefficients, non-/20 scales (/10, /100, /50)
- Objectives, required marks, impossible & guaranteed targets
- Non-mutating simulations & saved scenarios (official marks untouched)
- Documented validation rules (pass_threshold, eliminatory_minimum, compensation, verification provenance)
"""

from __future__ import annotations

from decimal import Decimal as D, ROUND_HALF_UP


from app.modules.grades.domain import NodeSpec, Objective, build_tree, q, required, summarize
from app.modules.grades.validation_rules import Rule, evaluate

API = "/api/v1/grades"


def test_reference_case_decimal_coefficients_domain_and_api(client):
    """Exact prompt reference case:
    Subject A coef 2 with 13/20; Subject B coef 1.5 without grade.
    Target mean = 12/20 -> required mark in Subject B = (12 * 3.5 - 2 * 13) / 1.5 = 16 / 1.5 = 10.6666667/20.
    """
    # 1. Pure domain verification
    tree = build_tree([
        NodeSpec("sem", None, "semester", "Semestre 1"),
        NodeSpec("ue1", "sem", "unit", "UE 1", D("3.5")),
        NodeSpec("sub1", "ue1", "subject", "Matière 1", D("2")),
        NodeSpec("sub2", "ue1", "subject", "Matière 2", D("1.5")),
    ])
    summ = summarize(tree, {"sub1": D("13")}, "ue1")
    assert summ.provisional == D("13")
    assert summ.final is None
    assert q(summ.coverage) == q(D("2") / D("3.5"))

    req = required(tree, {"sub1": D("13")}, Objective("obj-ref", "ue1", D("12")))
    assert req.feasibility == "feasible"
    assert req.required_mean is not None
    assert req.required_mean.quantize(D("0.0001"), rounding=ROUND_HALF_UP) == D("10.6667")
    assert q(req.required_mean) == D("10.6666667")

    # Verify inverse: plugging exact required_mean back yields exact 12.0000000
    check = summarize(tree, {"sub1": D("13"), "sub2": req.required_mean}, "ue1")
    assert q(check.final) == D("12.0000000")

    # 2. Full HTTP API verification
    prog = client.post(f"{API}/programs", json={"title": "2A Ref", "academic_year": "2026-2027", "cohort": "2A"}).json()
    pid = prog["id"]
    cur = client.put(
        f"{API}/programs/{pid}/curriculum",
        json={
            "expected_revision": 1,
            "nodes": [
                {"key": "sem", "kind": "semester", "title": "S1"},
                {"key": "ue1", "parent_key": "sem", "kind": "unit", "title": "UE 1", "weight": "3.5"},
                {"key": "sub1", "parent_key": "ue1", "kind": "subject", "title": "Matière 1", "weight": "2"},
                {"key": "sub2", "parent_key": "ue1", "kind": "subject", "title": "Matière 2", "weight": "1.5"},
            ],
        },
    )
    assert cur.status_code == 200, cur.text
    nodes = {n["key"]: n for n in cur.json()["nodes"]}
    assert client.post(f"{API}/records", json={"node_id": nodes["sub1"]["id"], "value": "13"}).status_code == 201
    assert client.post(f"{API}/objectives", json={"program_id": pid, "node_id": nodes["ue1"]["id"], "target": "12"}).status_code == 201

    api_req = client.post(f"{API}/required", json={"program_id": pid}).json()
    assert api_req["requirements"][0]["required_mean"] == "10.6666667"
    assert api_req["requirements"][0]["feasibility"] == "feasible"
    assert api_req["leaves"][0]["node_key"] == "sub2"
    assert api_req["leaves"][0]["needed"] == "10.6666667"


def test_hierarchy_custom_coefficients_non20_scales_and_impossible_target(client):
    prog = client.post(f"{API}/programs", json={"title": "2A Full"}).json()
    pid = prog["id"]
    tree_nodes = [
        {"key": "y", "kind": "year", "title": "2026-2027"},
        {"key": "s1", "parent_key": "y", "kind": "semester", "title": "S1", "weight": "1"},
        {"key": "ue1", "parent_key": "s1", "kind": "unit", "title": "UE Stat", "weight": "3.5"},
        {"key": "m1", "parent_key": "ue1", "kind": "subject", "title": "Stat", "weight": "2"},
        {"key": "m1_cc", "parent_key": "m1", "kind": "assessment", "title": "CC /10", "weight": "0.4", "scale_max": "10"},
        {"key": "m1_ex", "parent_key": "m1", "kind": "assessment", "title": "Exam /100", "weight": "0.6", "scale_max": "100"},
        {"key": "m2", "parent_key": "ue1", "kind": "subject", "title": "Info", "weight": "1.5", "scale_max": "50"},
    ]
    cur = client.put(f"{API}/programs/{pid}/curriculum", json={"expected_revision": 1, "nodes": tree_nodes}).json()
    nodes = {n["key"]: n for n in cur["nodes"]}

    # CC = 7/10 -> 14/20; Exam = 65/100 -> 13/20 => m1 = 0.4*14 + 0.6*13 = 13.4/20
    assert client.post(f"{API}/records", json={"node_id": nodes["m1_cc"]["id"], "value": "7"}).status_code == 201
    assert client.post(f"{API}/records", json={"node_id": nodes["m1_ex"]["id"], "value": "65"}).status_code == 201
    summ = {n["node_key"]: n for n in client.get(f"{API}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["m1"]["final"] == "13.4"
    assert summ["ue1"]["provisional"] == "13.4"
    assert summ["ue1"]["final"] is None

    # Impossible objective: target 18/20 in UE1 with m1=13.4 (coef 2) and m2 (coef 1.5)
    # Required = (18*3.5 - 13.4*2) / 1.5 = (63 - 26.8) / 1.5 = 24.1333333 > 20 -> impossible
    client.post(f"{API}/objectives", json={"program_id": pid, "node_id": nodes["ue1"]["id"], "target": "18"})
    req = client.post(f"{API}/required", json={"program_id": pid}).json()["requirements"][0]
    assert req["feasibility"] == "impossible"
    assert "ABOVE_SCALE_MAXIMUM" in req["reason_codes"]

    # Simulation on m2 (scale /50: 40/50 = 16/20) must NEVER mutate official records or revision
    rev_before = client.get(f"{API}/programs").json()[0]["revision"]
    sim = client.post(
        f"{API}/simulate",
        json={"program_id": pid, "values": [{"node_id": nodes["m2"]["id"], "value": "40"}]},
    ).json()
    proj = {p["node_key"]: p for p in sim["projections"]}
    # Expected UE1 = (2*13.4 + 1.5*16)/3.5 = (26.8 + 24)/3.5 = 50.8/3.5 = 14.5142857
    assert proj["ue1"]["projected"] == "14.5142857"
    assert client.get(f"{API}/programs").json()[0]["revision"] == rev_before
    assert len(client.get(f"{API}/records", params={"program_id": pid}).json()) == 2

    # Custom coefficient update: change m2 weight from 1.5 to 3.0
    updated_tree = [dict(n, weight="3.0") if n["key"] == "m2" else n for n in tree_nodes]
    cur2 = client.put(
        f"{API}/programs/{pid}/curriculum",
        json={"expected_revision": rev_before, "nodes": updated_tree},
    )
    assert cur2.status_code == 200
    # Now required for 18/20 is (18*5 - 13.4*2)/3 = (90 - 26.8)/3 = 21.0666667 (still impossible)
    # For target 16/20 -> (16*5 - 26.8)/3 = 53.2/3 = 17.7333333 (feasible!)
    obj_id = client.get(f"{API}/objectives", params={"program_id": pid}).json()[0]["id"]
    client.patch(f"{API}/objectives/{obj_id}", json={"target": "16"})
    req2 = client.post(f"{API}/required", json={"program_id": pid}).json()["requirements"][0]
    assert req2["feasibility"] == "feasible"
    assert req2["required_mean"] == "17.7333333"


def test_validation_rules_official_status_and_eliminatory_blocks_compensation(client):
    prog = client.post(f"{API}/programs", json={"title": "2A Rules"}).json()
    pid = prog["id"]
    cur = client.put(
        f"{API}/programs/{pid}/curriculum",
        json={
            "nodes": [
                {"key": "ue", "kind": "unit", "title": "UE"},
                {"key": "a", "parent_key": "ue", "kind": "subject", "title": "A", "weight": "2"},
                {"key": "b", "parent_key": "ue", "kind": "subject", "title": "B", "weight": "2"},
            ]
        },
    ).json()
    nodes = {n["key"]: n for n in cur["nodes"]}
    # A = 5 (below eliminatory min 6), B = 18 -> UE mean = 11.5 >= 10, but A is eliminated!
    client.post(f"{API}/records", json={"node_id": nodes["a"]["id"], "value": "5"})
    client.post(f"{API}/records", json={"node_id": nodes["b"]["id"], "value": "18"})

    # Verified rules without source_ref must be rejected
    bad_rules = client.put(
        f"{API}/programs/{pid}/rules",
        json={
            "verification_status": "verified",
            "rules": [{"type": "pass_threshold", "node_id": nodes["ue"]["id"], "value": "10"}],
        },
    )
    assert bad_rules.status_code == 422

    # Unverified rules keep official_status == "unknown"
    client.put(
        f"{API}/programs/{pid}/rules",
        json={
            "verification_status": "unverified",
            "rules": [
                {"type": "compensation", "node_id": nodes["ue"]["id"], "value": "10"},
                {"type": "pass_threshold", "node_id": nodes["a"]["id"], "value": "10"},
                {"type": "eliminatory_minimum", "node_id": nodes["a"]["id"], "value": "6"},
            ],
        },
    )
    v_unv = {n["node_key"]: n for n in client.get(f"{API}/validation", params={"program_id": pid}).json()["nodes"]}
    assert v_unv["a"]["official_status"] == "unknown"
    assert v_unv["a"]["computed_status"] == "not_validated"
    assert "ELIMINATED" in v_unv["a"]["reason_codes"]

    # Verified rules with source_ref report official_status and block compensation
    client.put(
        f"{API}/programs/{pid}/rules",
        json={
            "verification_status": "verified",
            "source_ref": "Reglement des etudes ENSAI 2026-2027 art. 4",
            "rules": [
                {"type": "compensation", "node_id": nodes["ue"]["id"], "value": "10"},
                {"type": "pass_threshold", "node_id": nodes["a"]["id"], "value": "10"},
                {"type": "eliminatory_minimum", "node_id": nodes["a"]["id"], "value": "6"},
            ],
        },
    )
    v_ver = {n["node_key"]: n for n in client.get(f"{API}/validation", params={"program_id": pid}).json()["nodes"]}
    assert v_ver["a"]["official_status"] == "not_validated"
    assert v_ver["ue"]["official_status"] == "not_validated"


def test_qa_repro_compensation_impossible_when_group_max_below_threshold():
    tree = build_tree([
        NodeSpec("ue", None, "unit", "UE"),
        NodeSpec("a", "ue", "subject", "A", D("3")),
        NodeSpec("b", "ue", "subject", "B", D("1")),
    ])
    # A = 4/20 (coef 3), B missing (coef 1). Max possible UE mean = (3*4 + 1*20)/4 = 8.0 < 10.0!
    rules = [
        Rule("compensation", "ue", D("10")),
        Rule("pass_threshold", "a", D("10")),
    ]
    ev = evaluate(tree, {"a": D("4")}, rules)
    # Even if B gets 20/20, UE reaches at most 8/20 < 10 -> compensation is IMPOSSIBLE, not possible!
    assert "COMPENSATION_STILL_POSSIBLE" not in ev["a"].reason_codes
    assert ev["a"].outlook != "possible"


def test_qa_repro_grades_import_endpoint_exists(client):
    r = client.post(
        f"{API}/import",
        json={"source": "manual_json", "dry_run": True, "program": {"title": "2A"}},
    )
    assert r.status_code == 200


def test_qa_repro_zero_coefficient_subject_handling():
    tree = build_tree([
        NodeSpec("ue", None, "unit", "UE"),
        NodeSpec("core", "ue", "subject", "Core", D("2")),
        NodeSpec("opt", "ue", "subject", "Option Sport", D("0")),
    ])
    s = summarize(tree, {"core": D("14"), "opt": D("20")}, "ue")
    assert s.final == D("14")


def test_qa_repro_three_equal_tens_achieves_target_ten():
    tree = build_tree([
        NodeSpec("ue", None, "unit", "UE"),
        NodeSpec("a", "ue", "subject", "A", D("1")),
        NodeSpec("b", "ue", "subject", "B", D("1")),
        NodeSpec("c", "ue", "subject", "C", D("1")),
    ])
    marks = {"a": D("10"), "b": D("10"), "c": D("10")}
    req = required(tree, marks, Objective("o10", "ue", D("10")))
    assert req.feasibility == "achieved"
