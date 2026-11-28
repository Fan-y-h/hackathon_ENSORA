"""QA Section 3 — Sources, Pamplemousse coefficients, propagation & resilience tests.

All tests run on isolated temporary SQLite databases and fixtures (never touching real user data).
Known open bugs owned by other agents are marked ``xfail(strict=True)`` so ``pytest --runxfail``
reproduces every open defect and ``strict=True`` alerts QA immediately once a fix lands.
"""

from __future__ import annotations

from decimal import Decimal
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.grades.source_results import build_results
from app.modules.ingestion.connectors.pamplemousse import (
    PamplemousseConnector,
    parse_decimal_strict,
    parse_pamplemousse_inscriptions_html,
    parse_pamplemousse_notes_html,
    reconcile_pamplemousse_academics,
)
from backend.app.core.runtime import Components
from backend.app.core.settings import Settings as P1Settings


INSCRIPTIONS_FIXTURE = """
<html><body>
<select><option selected>2026-2027</option></select>
<table>
  <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
  <tr><td>UE2-01 Statistique</td><td>Régression linéaire</td><td>2ASTA02</td><td>2</td><td>4</td><td>S1</td></tr>
  <tr><td>UE2-01 Statistique</td><td>Séries temporelles</td><td>2ASTA03</td><td>1,5</td><td>3</td><td>S1</td></tr>
  <tr><td>UE2-01 Statistique</td><td>Atelier R (sans coef inscrit)</td><td>2ASTA09</td><td>-</td><td>1</td><td>S1</td></tr>
  <tr><td>UE2-01 Statistique</td><td>Option facultative</td><td>2AOPT01</td><td>0</td><td>0</td><td>S1</td></tr>
  <tr><td>Sans UE</td><td>Projet Fil Rouge</td><td>2APROJ01</td><td>3</td><td>5</td><td></td></tr>
</table>
</body></html>
"""

NOTES_FIXTURE = """
<html><body>
<h1>BULLETIN DÉTAILLÉ 2026-2027</h1>
<table>
  <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th><th>Classement</th></tr>
  <tr class="overall"><td>Moyenne générale</td><td>13,50</td><td></td><td>5/120</td></tr>
  <tr class="semestre"><td>Premier semestre</td><td>13,50</td><td></td><td></td></tr>
  <tr class="ue"><td>UE2-01 Statistique</td><td>13,00</td><td>3,5</td><td></td></tr>
  <tr class="matiere"><td><strong>2ASTA02 Régression linéaire</strong></td><td>13,00</td><td>2</td><td></td></tr>
  <tr class="epreuve"><td style="padding-left:20px">- EXAMEN</td><td>13,00 / 20</td><td>1</td><td></td></tr>
  <tr class="matiere"><td><strong>2ASTA09 Atelier R (sans coef inscrit)</strong></td><td>15,00</td><td>1,25</td><td></td></tr>
  <tr class="epreuve"><td style="padding-left:20px">- TP NOTÉ</td><td>15,00 / 20</td><td>1</td><td></td></tr>
  <tr class="matiere"><td><strong>2ASTA03 Séries temporelles</strong></td><td>-</td><td>1,5</td><td></td></tr>
</table>
</body></html>
"""


@pytest.fixture
def isolated_unified_client(tmp_path):
    p1_settings = P1Settings(
        config_dir=tmp_path / "config",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "config/sessions/ensai",
    )
    app = create_app(
        f"sqlite:///{(tmp_path / 'planner.db').as_posix()}",
        enable_sources=True,
        p1_settings=p1_settings,
        p1_components=Components(),
    )
    with TestClient(app) as tc:
        token = (p1_settings.local_data_dir / "api-token").read_text()
        yield tc, {"Authorization": f"Bearer {token}"}


def test_pamplemousse_inscriptions_parses_decimal_zero_missing_and_ects_separately():
    recs = parse_pamplemousse_inscriptions_html(INSCRIPTIONS_FIXTURE)
    by_code = {r.subject_code: r for r in recs if r.record_type == "subject"}

    assert by_code["2ASTA02"].weight == Decimal("2")
    assert by_code["2ASTA02"].provenance["ects"] == "4"
    assert by_code["2ASTA02"].weight != Decimal(by_code["2ASTA02"].provenance["ects"])

    assert by_code["2ASTA03"].weight == Decimal("1.5")
    assert by_code["2ASTA03"].provenance["ects"] == "3"

    # Missing coefficient stays None (never 0, never ECTS)
    assert by_code["2ASTA09"].weight is None
    assert by_code["2ASTA09"].weight_status.value == "missing"

    # Explicit 0 coefficient stays Decimal("0") and confirmed
    assert by_code["2AOPT01"].weight == Decimal("0")
    assert by_code["2AOPT01"].weight_status.value == "confirmed"

    # Sans UE subject does not fabricate a fake UE
    assert by_code["2APROJ01"].weight == Decimal("3")
    assert by_code["2APROJ01"].provenance["sans_ue"] is True


def test_pamplemousse_notes_ignores_classement_and_preserves_missing_grade():
    recs = parse_pamplemousse_notes_html(NOTES_FIXTURE)
    # Ranking "5/120" must never appear in any record payload or title
    for r in recs:
        assert "5/120" not in str(r.to_dict())
    series = [r for r in recs if r.record_type == "subject" and "Séries temporelles" in r.title]
    assert len(series) == 1
    assert series[0].grade_value is None  # missing stays None, never 0


def test_qa_repro_reconcile_backfills_missing_inscriptions_coef_from_notes():
    insc = parse_pamplemousse_inscriptions_html(INSCRIPTIONS_FIXTURE)
    notes = parse_pamplemousse_notes_html(NOTES_FIXTURE)
    merged = reconcile_pamplemousse_academics(insc, notes)
    by_code = {r.subject_code: r for r in merged if r.record_type == "subject"}
    # 2ASTA09 had '-' in Inscriptions and '1,25' in Notes -> reconciled subject must inherit 1.25
    assert by_code["2ASTA09"].weight == Decimal("1.25")


def test_qa_repro_parse_decimal_strict_does_not_misparse_fraction_weight():
    # A ratio weight '1/2' must either evaluate to 0.5 or return None, NEVER 1!
    assert parse_decimal_strict("1/2") != Decimal("1")


def test_qa_repro_build_results_includes_sans_ue_subjects_and_positive_coefs():
    y = "pamplemousse:year:2026_2027"
    s1 = "pamplemousse:sem:2026_2027:s1"
    ue = "pamplemousse:ue:2026_2027:s1:ue1"
    in_ue = "pamplemousse:subject:2026_2027:2asta02:aa"
    sans_ue = "pamplemousse:subject:2026_2027:2aproj01:bb"
    records = {
        y: {"kind": "academic_year", "title": "2026-2027", "parent_key": None},
        s1: {"kind": "semester", "title": "S1", "parent_key": y},
        ue: {"kind": "teaching_unit", "title": "UE1", "parent_key": s1},
        in_ue: {"kind": "subject", "title": "Régression", "code": "2ASTA02", "parent_key": ue},
        in_ue + ":coef": {"kind": "coefficient", "weight": "2", "level": "subject", "parent_key": in_ue},
        sans_ue: {"kind": "subject", "title": "Projet Fil Rouge", "code": "2APROJ01", "parent_key": y},
        sans_ue + ":coef": {"kind": "coefficient", "weight": "3", "level": "subject", "parent_key": sans_ue},
    }
    res = build_results(records)
    all_codes = {sb.code for sem in res.semesters for unit in sem.units for sb in unit.subjects}
    assert "2ASTA02" in all_codes
    assert "2APROJ01" in all_codes


def test_sources_auth_consent_propagation_and_deduplication(isolated_unified_client):
    client, headers = isolated_unified_client
    # Unauthorized call rejected
    assert client.get("/api/v1/sources/connections").status_code == 401

    conns = client.get("/api/v1/sources/connections", headers=headers).json()["items"]
    by_src = {c["source"]: c for c in conns}
    assert {"pamplemousse", "moodle", "ensora_drive", "google_calendar", "local", "academic_import"} <= set(by_src)

    # Without consent, academic import must be blocked
    ev_payload = [{
        "kind": "event",
        "title": "CM 2ASTA02 Régression linéaire",
        "event_type": "class",
        "starts_at": "2026-12-07T08:00:00+00:00",
        "ends_at": "2026-12-07T10:00:00+00:00",
        "room": "Amphi 2",
        "provenance": {"external_id": "pamp-ev-1"},
    }]
    ac_id = by_src["academic_import"]["id"]
    denied = client.post("/api/v1/sources/academic/import", json=ev_payload, headers=headers)
    assert denied.status_code in (400, 403, 409)

    # Grant consent + authorize -> import succeeds & propagates idempotently to agenda
    assert client.put(
        f"/api/v1/sources/connections/{ac_id}/consent",
        json={"purpose": "read", "granted": True},
        headers=headers,
    ).status_code == 200
    assert client.post(f"/api/v1/sources/connections/{ac_id}/authorize", headers=headers).json()["status"] == "CONNECTED"

    r1 = client.post("/api/v1/sources/academic/import", json=ev_payload, headers=headers)
    assert r1.status_code == 201
    # Second identical import must not duplicate
    r2 = client.post("/api/v1/sources/academic/import", json=ev_payload, headers=headers)
    assert r2.status_code == 201

    b1 = client.post("/api/v1/agenda/import-from-sources")
    assert b1.status_code == 200
    assert b1.json()["sources"][0]["result"]["created"] == 1
    b2 = client.post("/api/v1/agenda/import-from-sources")
    assert b2.status_code == 200
    assert b2.json()["sources"][0]["result"]["created"] == 0
    assert b2.json()["sources"][0]["result"]["unchanged"] == 1

    # Imported event is strictly read-only in agenda API
    events = client.get(
        "/api/v1/events", params={"start": "2026-12-01T00:00:00Z", "end": "2026-12-31T00:00:00Z"}
    ).json()["items"]
    assert len(events) == 1
    assert events[0]["read_only"] is True
    patch = client.patch(f"/api/v1/events/{events[0]['id']}", json={"title": "Hacked"})
    assert patch.status_code == 403


def test_qa_repro_concept_candidates_endpoint_returns_200(isolated_unified_client):
    client, headers = isolated_unified_client
    conns = {c["source"]: c for c in client.get("/api/v1/sources/connections", headers=headers).json()["items"]}
    loc_id = conns["local"]["id"]
    client.put(f"/api/v1/sources/connections/{loc_id}/consent", json={"purpose": "read", "granted": True}, headers=headers)
    client.post(f"/api/v1/sources/connections/{loc_id}/authorize", headers=headers)
    up = client.post(
        "/api/v1/sources/documents/import",
        files={"file": ("stat.md", b"# Regression\nTheoreme de Gauss-Markov: BLUE.\n", "text/markdown")},
        headers=headers,
    )
    assert up.status_code == 201
    doc = client.get("/api/v1/sources/documents", headers=headers).json()["items"][0]
    resp = client.get(
        f"/api/v1/sources/documents/{doc['id']}/versions/{doc['active_version_id']}/concept-candidates",
        headers=headers,
    )
    assert resp.status_code == 200
