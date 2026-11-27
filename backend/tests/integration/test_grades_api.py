from sqlalchemy import select

from app.core.events import OutboxEvent

API = "/api/v1/grades"
UE_TREE = [
    {"key": "s5", "kind": "semester", "title": "Semestre 5"},
    {"key": "ue_ds", "parent_key": "s5", "kind": "unit", "title": "UE Data Science", "weight": 5},
    {"key": "A", "parent_key": "ue_ds", "kind": "subject", "title": "A", "weight": 4, "subject_id": "subj-a"},
    {"key": "B", "parent_key": "ue_ds", "kind": "subject", "title": "B", "weight": "3", "subject_id": "subj-b"},
    {"key": "ue_opt", "parent_key": "s5", "kind": "unit", "title": "UE Optimisation", "weight": 4},
    {"key": "C", "parent_key": "ue_opt", "kind": "subject", "title": "C", "subject_id": "subj-c"},
]


def setup_program(client, tree=UE_TREE):
    p = client.post(f"{API}/programs", json={"title": "2A 2026-2027", "academic_year": "2026-2027", "cohort": "2A"}).json()
    cur = client.put(f"{API}/programs/{p['id']}/curriculum", json={"expected_revision": 1, "nodes": tree})
    assert cur.status_code == 200, cur.text
    nodes = {n["key"]: n for n in cur.json()["nodes"]}
    return p["id"], nodes


def mark(client, node_id, value, kind="actual"):
    r = client.post(f"{API}/records", json={"node_id": node_id, "value": value, "kind": kind})
    assert r.status_code == 201, r.text
    return r.json()


def test_curriculum_weights_and_validation(client):
    pid, nodes = setup_program(client)
    assert nodes["A"]["weight_in_parent"] == "0.5714286" and nodes["A"]["is_leaf"]
    assert nodes["ue_ds"]["weight_in_root"] == "0.5555556" and nodes["s5"]["weight_in_parent"] is None
    bad = [{"key": "r", "kind": "unit", "title": "r", "weight_mode": "fraction"},
           {"key": "x", "parent_key": "r", "kind": "subject", "title": "x", "weight": "0.6"},
           {"key": "y", "parent_key": "r", "kind": "subject", "title": "y", "weight": "0.6"}]
    r = client.put(f"{API}/programs/{pid}/curriculum", json={"nodes": bad})
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "fraction_sum"
    stale = client.put(f"{API}/programs/{pid}/curriculum", json={"expected_revision": 1, "nodes": UE_TREE})
    assert stale.status_code == 409


def test_marks_summary_missing_vs_zero_and_scale(client):
    pid, nodes = setup_program(client)
    rec = mark(client, nodes["A"]["id"], "13")
    assert rec["value"] == "13" and rec["value_on_20"] == "13"
    summ = {n["node_key"]: n for n in client.get(f"{API}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["ue_ds"]["provisional"] == "13" and summ["ue_ds"]["coverage"] == "0.5714286"
    assert summ["ue_ds"]["final"] is None and summ["C"]["provisional"] is None
    assert summ["s5"]["official_status"] == "unknown"

    zero = mark(client, nodes["B"]["id"], 0)
    summ = {n["node_key"]: n for n in client.get(f"{API}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["ue_ds"]["final"] == "7.4285714"  # 0 counts as a real mark
    client.patch(f"{API}/records/{zero['id']}", json={"value": "10", "expected_revision": 1})
    summ = {n["node_key"]: n for n in client.get(f"{API}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["ue_ds"]["final"] == "11.7142857"

    assert client.post(f"{API}/records", json={"node_id": nodes["A"]["id"], "value": 12}).status_code == 409
    assert client.post(f"{API}/records", json={"node_id": nodes["ue_ds"]["id"], "value": 12}).status_code == 422
    assert client.post(f"{API}/records", json={"node_id": nodes["C"]["id"], "value": 21}).status_code == 422
    est = mark(client, nodes["C"]["id"], 14, kind="estimated")
    summ = {n["node_key"]: n for n in client.get(f"{API}/summary", params={"program_id": pid}).json()["nodes"]}
    assert summ["C"]["provisional"] is None and summ["C"]["forecast"] == "14"  # estimate ≠ actual
    assert est["kind"] == "estimated"


def test_objectives_required_marks_and_bulk_every_ue(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    objs = client.post(f"{API}/objectives", json={"program_id": pid, "node_kind": "unit", "target": 12}).json()
    assert {o["node_key"] for o in objs} == {"ue_ds", "ue_opt"}
    req = client.post(f"{API}/required", json={"program_id": pid}).json()
    by_node = {r["node_key"]: r for r in req["requirements"]}
    assert by_node["ue_ds"]["required_mean"] == "10.6666667" and by_node["ue_ds"]["feasibility"] == "feasible"
    assert by_node["ue_opt"]["required_mean"] == "12"
    leaves = {x["node_key"]: x["needed"] for x in req["leaves"]}
    assert leaves == {"B": "10.6666667", "C": "12"}

    # Assumption "C = 15" doesn't touch real marks.
    req2 = client.post(f"{API}/required", json={"program_id": pid, "assumptions": [{"node_id": nodes["C"]["id"], "value": 15}]}).json()
    assert {r["node_key"]: r["feasibility"] for r in req2["requirements"]}["ue_opt"] == "achieved"
    assert len(client.get(f"{API}/records", params={"program_id": pid}).json()) == 1

    hard = client.patch(f"{API}/objectives/{objs[0]['id']}", json={"target": 20})
    assert hard.status_code == 200
    req3 = {r["node_key"]: r for r in client.post(f"{API}/required", json={"program_id": pid}).json()["requirements"]}
    assert req3["ue_ds"]["feasibility"] == "impossible"


def test_simulation_never_writes(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    client.post(f"{API}/objectives", json={"program_id": pid, "node_id": nodes["ue_ds"]["id"], "target": 12})
    before_rev = client.get(f"{API}/programs").json()[0]["revision"]
    with client.app.state.db.session_factory() as s:
        events_before = len(list(s.scalars(select(OutboxEvent))))
    sim = client.post(f"{API}/simulate", json={"program_id": pid, "expected_revision": before_rev,
                                               "values": [{"node_id": nodes["B"]["id"], "value": 8}]}).json()
    proj = {p["node_key"]: p for p in sim["projections"]}
    assert proj["ue_ds"]["baseline"] == "13" and proj["ue_ds"]["projected"] == "10.8571429"
    assert proj["ue_ds"]["delta"] == "-2.1428571"
    assert "SIMULATION_ONLY_NOT_SAVED" in sim["warnings"]
    assert {r["node_key"]: r["feasibility"] for r in sim["requirements"]}["ue_ds"] == "impossible"
    assert client.get(f"{API}/programs").json()[0]["revision"] == before_rev
    assert len(client.get(f"{API}/records", params={"program_id": pid}).json()) == 1
    with client.app.state.db.session_factory() as s:
        assert len(list(s.scalars(select(OutboxEvent)))) == events_before
    over_actual = client.post(f"{API}/simulate", json={"program_id": pid, "values": [{"node_id": nodes["A"]["id"], "value": 5}]})
    assert over_actual.status_code == 422


def test_signals_and_curriculum_change_removes_marks(client):
    pid, nodes = setup_program(client)
    mark(client, nodes["A"]["id"], 13)
    client.post(f"{API}/objectives", json={"program_id": pid, "node_kind": "unit", "target": 12})
    sig = {s["node_key"]: s for s in client.get(f"{API}/signals", params={"program_id": pid}).json()["signals"]}
    assert sig["A"]["score"] == "0" and "FULLY_GRADED" in sig["A"]["reason_codes"]
    assert sig["C"]["score"] == "1" and sig["B"]["subject_id"] == "subj-b"

    smaller = [n for n in UE_TREE if n["key"] != "A"]
    rev = client.get(f"{API}/programs").json()[0]["revision"]
    r = client.put(f"{API}/programs/{pid}/curriculum", json={"expected_revision": rev, "nodes": smaller}).json()
    assert r["removed_records"] == 1
    assert client.get(f"{API}/records", params={"program_id": pid}).json() == []
    # Giving children to a graded leaf is rejected.
    mark(client, {n["key"]: n for n in r["nodes"]}["B"]["id"], 11)
    grown = smaller + [{"key": "B1", "parent_key": "B", "kind": "assessment", "title": "B1"}]
    assert client.put(f"{API}/programs/{pid}/curriculum", json={"nodes": grown}).status_code == 422


def test_grades_isolation(client, as_bob):
    pid, nodes = setup_program(client)
    rec = mark(client, nodes["A"]["id"], 13)
    assert client.get(f"{API}/programs", headers=as_bob).json() == []
    assert client.get(f"{API}/summary", params={"program_id": pid}, headers=as_bob).status_code == 404
    assert client.post(f"{API}/records", json={"node_id": nodes["B"]["id"], "value": 1}, headers=as_bob).status_code == 404
    assert client.patch(f"{API}/records/{rec['id']}", json={"value": 1}, headers=as_bob).status_code == 404
    with client.app.state.db.session_factory() as s:  # outbox carries no mark values
        payloads = [e.payload or {} for e in s.scalars(select(OutboxEvent))]
        assert payloads and all(not ({"value", "mark", "score", "target"} & set(p)) for p in payloads)
        assert all("13" not in [str(v) for v in p.values()] for p in payloads)
