"""Comprehensive regression tests for Pamplemousse coefficients, hierarchy, reconciliation, API, and events.

Validates the full chain requested for Agent 01:
Extraction -> Parsing -> Reconciliation -> Normalization -> SQLite Persistence -> API -> Results/Planner bridge.
All fixtures are synthetic and strictly anonymized (zero private student data).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.modules.grades.source_results import build_results, read_records
from app.modules.planner.source_bridge import to_import_item
from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
from backend.app.core.database import records as db_records
from backend.app.core.runtime import Components, Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.api import create_app as create_sources_app
from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
from backend.app.modules.ingestion.config import SourcesRuntimeConfig
from backend.app.modules.ingestion.connectors.base import RateLimiter
from backend.app.modules.ingestion.connectors.pamplemousse import (
    PamplemousseConnector,
    _parse_coefficient_cell,
    discover_pamplemousse_nav_links,
    parse_fullcalendar_list_html,
    parse_ics_events,
    parse_pamplemousse_inscriptions_html,
    parse_pamplemousse_notes_html,
    reconcile_pamplemousse_academics,
)
from backend.app.modules.ingestion.contracts import ValueStatus


def test_parse_coefficient_cell_handles_french_decimals_nbsp_zero_and_percent() -> None:
    assert _parse_coefficient_cell("2,5") == (Decimal("2.5"), "raw")
    assert _parse_coefficient_cell("1\u202f5") == (Decimal("15"), "raw")
    assert _parse_coefficient_cell("Coef. 1,25") == (Decimal("1.25"), "raw")
    assert _parse_coefficient_cell("0") == (Decimal("0"), "raw")
    assert _parse_coefficient_cell("0,0") == (Decimal("0.0"), "raw")
    assert _parse_coefficient_cell("25%") == (Decimal("0.25"), "fraction")
    assert _parse_coefficient_cell("33,5 %") == (Decimal("0.335"), "fraction")
    assert _parse_coefficient_cell("") == (None, None)
    assert _parse_coefficient_cell("-") == (None, None)
    assert _parse_coefficient_cell("N.C.") == (None, None)
    assert _parse_coefficient_cell("-1.5") == (None, None)


def test_inscriptions_merged_rowspan_colspan_banner_and_ects_separation() -> None:
    """Merged UE/Sem rowspan cells and a banner colspan row must never shift Coef. or confuse ECTS."""
    html = """
    <html><body>
      <select name="annee"><option selected>2026-2027</option></select>
      <table>
        <tr><th colspan="7">Inscriptions pédagogiques 2026-2027</th></tr>
        <tr>
          <th>Sem.</th><th>Code UE</th><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>Crédits ECTS</th>
        </tr>
        <tr>
          <td rowspan="3">Semestre 1</td>
          <td rowspan="2">UE1</td>
          <td rowspan="2">3A-UE1 Machine Learning</td>
          <td>Économétrie avancée</td>
          <td>3A-ML-01-1</td>
          <td>2,5</td>
          <td>4</td>
        </tr>
        <tr>
          <td>Statistique bayésienne</td>
          <td>3A-ML-01-2</td>
          <td>1,5</td>
          <td>3</td>
        </tr>
        <tr>
          <td>UE2</td>
          <td>3A-UE2 Informatique</td>
          <td>Architecture distribuée</td>
          <td>3A-INF-02-1</td>
          <td></td>
          <td>2</td>
        </tr>
        <tr>
          <td>S2</td>
          <td></td>
          <td>sans UE</td>
          <td>Activité optionnelle</td>
          <td>3A-OPT-01</td>
          <td>0</td>
          <td>0</td>
        </tr>
      </table>
    </body></html>
    """
    recs = parse_pamplemousse_inscriptions_html(html)
    subjs = {r.subject_code: r for r in recs if r.record_type == "subject"}
    assert set(subjs.keys()) == {"3A-ML-01-1", "3A-ML-01-2", "3A-INF-02-1", "3A-OPT-01"}

    # Coef != ECTS strictly preserved across rowspan rows
    eco = subjs["3A-ML-01-1"]
    assert eco.weight == Decimal("2.5")
    assert eco.weight_status == ValueStatus.CONFIRMED
    assert eco.provenance["ects"] == "4"
    assert eco.semester == "S1"

    bayes = subjs["3A-ML-01-2"]
    assert bayes.weight == Decimal("1.5")
    assert bayes.provenance["ects"] == "3"
    assert bayes.parent_external_id == eco.parent_external_id  # Inherited same UE via rowspan!
    assert bayes.semester == "S1"

    arch = subjs["3A-INF-02-1"]
    assert arch.weight is None
    assert arch.weight_status == ValueStatus.MISSING
    assert arch.provenance["ects"] == "2"

    opt = subjs["3A-OPT-01"]
    assert opt.weight == Decimal("0")
    assert opt.weight_status == ValueStatus.CONFIRMED
    assert opt.provenance["sans_ue"] is True

    # Inscriptions has no UE coefficient column -> UEs stay UNKNOWN (never fabricated MISSING)
    ues = [r for r in recs if r.record_type == "teaching_unit"]
    assert len(ues) == 2
    assert all(u.weight is None and u.weight_status == ValueStatus.UNKNOWN for u in ues)


def test_reconcile_merges_ue_and_missing_subject_coefficients_accent_insensitively() -> None:
    """Reconciliation must merge UE/subject coefficients from Notes, match accented titles, and avoid bogus coef rows."""
    inscriptions_html = """
    <html><body>
      <table>
        <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
        <tr><td>3A-UE1 Compléments ML</td><td>Économétrie appliquée</td><td>3AECO01</td><td></td><td>3</td><td>S1</td></tr>
        <tr><td>3A-UE1 Compléments ML</td><td>Apprentissage supervisé</td><td>3AML002</td><td>2,5</td><td>4</td><td>S1</td></tr>
      </table>
    </body></html>
    """
    notes_html = """
    <html><body>
      <h2>BULLETIN DÉTAILLÉ</h2>
      <table>
        <tr><th colspan="3">Résultats 2026-2027</th></tr>
        <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th></tr>
        <tr class="semestre"><td><strong>Premier semestre</strong></td><td>15,20</td><td>1,00</td></tr>
        <tr class="ue"><td><strong>3A-UE1 COMPLEMENTS ML</strong></td><td>15,80</td><td>5,0</td></tr>
        <tr class="matiere"><td><strong>ECONOMETRIE APPLIQUEE</strong></td><td>16,00</td><td>2,0</td></tr>
        <tr><td>- EXAMEN</td><td>16,00</td><td>60%</td></tr>
        <tr><td>- PROJET</td><td>16,00</td><td>40%</td></tr>
        <tr class="matiere"><td><strong>Apprentissage supervisé</strong></td><td>15,50</td><td>2,5</td></tr>
        <tr><td>- PARTIEL</td><td>15,50</td><td>1,0</td></tr>
      </table>
    </body></html>
    """
    insc = parse_pamplemousse_inscriptions_html(inscriptions_html, academic_year="2026-2027")
    notes = parse_pamplemousse_notes_html(notes_html, academic_year="2026-2027")
    reconciled = reconcile_pamplemousse_academics(insc, notes)

    # 1. UE matched accent-insensitively and enriched with official coefficient 5.0 from Notes!
    ues = [r for r in reconciled if r.record_type == "teaching_unit"]
    assert len(ues) == 1
    assert ues[0].weight == Decimal("5.0")
    assert ues[0].weight_status == ValueStatus.CONFIRMED
    assert ues[0].provenance.get("weight_source") == "notes_html"

    # 2. Subject with blank coef in Inscriptions matched accent-insensitively and enriched with 2.0 from Notes!
    subjs = {r.subject_code: r for r in reconciled if r.record_type == "subject"}
    assert set(subjs.keys()) == {"3AECO01", "3AML002"}
    eco = subjs["3AECO01"]
    assert eco.weight == Decimal("2.0")
    assert eco.weight_status == ValueStatus.CONFIRMED
    assert eco.provenance["ects"] == "3"
    assert eco.provenance.get("weight_source") == "notes_html"

    # 3. Leading-dash assessment rows ('- EXAMEN', '- PROJET', '- PARTIEL') attached to reconciled subjects!
    assessments = [r for r in reconciled if r.record_type == "assessment"]
    assert len(assessments) == 3
    eco_assessments = [a for a in assessments if a.parent_external_id == eco.external_id]
    assert len(eco_assessments) == 2
    assert {a.weight for a in eco_assessments} == {Decimal("0.6"), Decimal("0.4")}
    assert all(a.weight_mode == "fraction" for a in eco_assessments)

    # 4. Official aggregates for UE/subject do NOT carry spurious MISSING weight_status
    aggs = [r for r in reconciled if r.record_type == "aggregate_grade"]
    ue_and_subj_aggs = [
        a for a in aggs if (a.provenance or {}).get("aggregate_scope") in ("teaching_unit", "subject")
    ]
    assert len(ue_and_subj_aggs) == 3
    assert all(a.weight is None and a.weight_status == ValueStatus.UNKNOWN for a in ue_and_subj_aggs)


def test_pamplemousse_leos_fallback_discovery_and_program_page_coefficients(tmp_path: Path) -> None:
    """Leos portals must still fetch p=107/p=1052 when menu links are collapsed, and use Programme page if needed."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    base = cfg.pamplemousse_base_url
    home_html = """
    <html><body>
      <select><option selected>2026-2027</option></select>
      <a href="index.php?p=121">EDT</a>
      <a href="index.php?p=305">Programme pédagogique</a>
    </body></html>
    """
    nav = discover_pamplemousse_nav_links(home_html, base)
    assert "program" in nav and nav["program"].endswith("index.php?p=305")
    assert "inscriptions" not in nav and "notes" not in nav

    pages = {
        base: home_html,
        "https://23ed753f.leos.fr/index.php?p=121": "<html><body></body></html>",
        "https://23ed753f.leos.fr/index.php?p=107": (
            "<html><body><table><tr><th>UE</th><th>Matière</th><th>Code</th><th>ECTS</th><th>Sem.</th></tr>"
            "<tr><td>3A-UE1 ML</td><td>Deep Learning</td><td>3AML01</td><td>4</td><td>S1</td></tr>"
            "</table></body></html>"
        ),
        "https://23ed753f.leos.fr/index.php?p=305": (
            "<html><body><table><tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>"
            "<tr><td>3A-UE1 ML</td><td>Deep Learning</td><td>3AML01</td><td>3,5</td><td>4</td><td>S1</td></tr>"
            "</table></body></html>"
        ),
        "https://23ed753f.leos.fr/index.php?p=1052": (
            "<html><body><h2>BULLETIN DÉTAILLÉ</h2><table>"
            "<tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th></tr>"
            "<tr class='semestre'><td><strong>S1</strong></td><td>15,0</td><td>1</td></tr>"
            "<tr class='ue'><td><strong>3A-UE1 ML</strong></td><td>15,0</td><td>4</td></tr>"
            "<tr class='matiere'><td><strong>Deep Learning</strong></td><td>15,0</td><td>3,5</td></tr>"
            "</table></body></html>"
        ),
    }
    conn = PamplemousseConnector(cfg, page_fetcher=lambda u: pages[u], rate_limiter=RateLimiter(0.0))
    page = conn.list_changes(scope={})
    assert "inscriptions" in conn.last_stats["pages_visited"]
    assert "program" in conn.last_stats["pages_visited"]
    assert "notes" in conn.last_stats["pages_visited"]
    dl = next(it for it in page.items if (it.payload or {}).get("subject_code") == "3AML01")
    assert dl.payload["weight"] == "3.5"


def test_pamplemousse_end_to_end_persistence_api_override_idempotence_and_results(tmp_path: Path) -> None:
    """Verify Pamplemousse -> CodexBridge -> SQLite -> /api/v1/sources/coefficients + override + /results."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    inscriptions_html = """
    <html><body>
      <select><option selected>2026-2027</option></select>
      <table>
        <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
        <tr><td>3A-UE1 ML</td><td>Régression avancée</td><td>3ASTA01</td><td>2,5</td><td>4</td><td>S1</td></tr>
        <tr><td>3A-UE1 ML</td><td>Séminaire IA</td><td>3AML09</td><td></td><td>1</td><td>S1</td></tr>
      </table>
    </body></html>
    """
    notes_html = """
    <html><body>
      <h2>BULLETIN DÉTAILLÉ</h2>
      <table>
        <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th></tr>
        <tr class="semestre"><td><strong>Premier semestre</strong></td><td>14,50</td><td>1</td></tr>
        <tr class="ue"><td><strong>3A-UE1 ML</strong></td><td>14,50</td><td>3,5</td></tr>
        <tr class="matiere"><td><strong>Regression avancee</strong></td><td>14,50</td><td>2,5</td></tr>
        <tr><td>- EXAMEN</td><td>14,50</td><td>1,0</td></tr>
      </table>
    </body></html>
    """
    home_html = """
    <html><body>
      <select><option selected>2026-2027</option></select>
      <a href="index.php?p=107">Inscriptions</a>
      <a href="index.php?p=1052">Notes</a>
    </body></html>
    """
    pages = {
        cfg.pamplemousse_base_url: home_html,
        "https://23ed753f.leos.fr/index.php?p=107": inscriptions_html,
        "https://23ed753f.leos.fr/index.php?p=1052": notes_html,
    }
    conn = PamplemousseConnector(cfg, page_fetcher=lambda u: pages[u], rate_limiter=RateLimiter(0.0))
    bridge = CodexConnectorBridge(conn)
    cpage = bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE, academic_year="2026-2027"), None)
    assert cpage.complete is True
    assert not cpage.error_codes
    codex_records = list(cpage.items)

    settings = Settings(config_dir=tmp_path / "rt_cfg", local_data_dir=tmp_path / "rt_data")
    app = create_sources_app(settings=settings, components=Components())
    with TestClient(app) as client:
        rt: Runtime = app.state.runtime
        headers = {"Authorization": f"Bearer {rt.api_token}"}
        pamp_conn_id = rt.connection_ids[CodexSourceKind.PAMPLEMOUSSE]
        rt.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)

        # 1. First ingestion
        for rec in codex_records:
            rt.pipeline.ingest(pamp_conn_id, rec)

        # 2. Idempotent second ingestion must produce zero changes
        second_statuses = [rt.pipeline.ingest(pamp_conn_id, rec) for rec in codex_records]
        assert all(st == "unchanged" for st in second_statuses)

        # 3. Verify GET /api/v1/coefficients exposes official UE, subject, missing, and assessment coefs
        resp = client.get("/api/v1/coefficients", headers=headers)
        assert resp.status_code == 200
        coef_items = resp.json()["items"]
        by_level = {}
        for item in coef_items:
            by_level.setdefault(item["payload"]["level"], []).append(item)
        assert len(by_level["teaching_unit"]) == 1
        assert Decimal(str(by_level["teaching_unit"][0]["payload"]["weight"])) == Decimal("3.5")
        assert len(by_level["subject"]) == 2
        known_subj = next(i for i in by_level["subject"] if i["payload"]["weight"] is not None)
        missing_subj = next(i for i in by_level["subject"] if i["payload"]["weight"] is None)
        assert Decimal(str(known_subj["payload"]["weight"])) == Decimal("2.5")
        assert missing_subj["payload"]["record_status"] == "missing"

        # 4. Manual override for missing coefficient persists distinctly from official payload & survives resync
        ov = client.put(
            f"/api/v1/academic/records/{missing_subj['id']}/override",
            json={"fields": {"weight": "1.5", "record_status": "known"}},
            headers=headers,
        )
        assert ov.status_code == 200
        for rec in codex_records:
            rt.pipeline.ingest(pamp_conn_id, rec)
        after = client.get("/api/v1/coefficients", headers=headers).json()["items"]
        overridden = next(i for i in after if i["id"] == missing_subj["id"])
        assert overridden["payload"]["weight"] is None  # Official source value untouched!
        assert overridden["user_override"]["weight"] == "1.5"
        assert overridden["effective_payload"]["weight"] == "1.5"

        # 5. Downstream /api/v1/results builder sees full Semester -> UE -> Subject -> Assessment with coefficients
        tree = build_results(read_records(rt.engine))
        assert tree.academic_year == "2026-2027"
        assert len(tree.semesters) == 1
        unit = tree.semesters[0].units[0]
        by_code = {s.code: s for s in unit.subjects}
        assert by_code["3ASTA01"].coefficient == 2.5
        assert by_code["3ASTA01"].ects == 4.0
        assert by_code["3ASTA01"].official_grade == 14.5
        assert len(by_code["3ASTA01"].assessments) == 1
        assert by_code["3ASTA01"].assessments[0].coefficient == 1.0
        assert by_code["3AML09"].coefficient is None  # Missing stays null, never 0!

        # 6. Zero spurious duplicate official_aggregate:coef rows in DB
        with rt.engine.connect() as db:
            all_coef_ids = [
                row[0]
                for row in db.execute(select(db_records.c.external_id).where(db_records.c.kind == "coefficient"))
            ]
        assert not any(
            eid.endswith(":official_aggregate:coef") and ("ue:" in eid or "notes:subject:" in eid)
            for eid in all_coef_ids
        )


def test_pamplemousse_ics_duration_course_code_cancelled_and_planner_bridge() -> None:
    """ICS and FullCalendar List extract course_id, honor DURATION, mark [ANNULÉ], and feed source_bridge cleanly."""
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:ev-dur-1\r\n"
        "SUMMARY:CM Économétrie 2AECO02\r\n"
        "DESCRIPTION:Prof: Mme Martin\\nGroupe: 2A-G1\r\n"
        "LOCATION:Amphi 2\r\n"
        "DTSTART;TZID=Europe/Paris:20261130T090000\r\n"
        "DURATION:PT1H30M\r\n"
        "END:VEVENT\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:ev-cancel-2\r\n"
        "SUMMARY:[ANNULÉ] TD Machine Learning 3A-ML-01-1\r\n"
        "DTSTART;TZID=Europe/Paris:20261130T140000\r\n"
        "DTEND;TZID=Europe/Paris:20261130T160000\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    events = parse_ics_events(ics)
    assert len(events) == 2
    ev1, ev2 = events[0], events[1]
    assert ev1.start_at == "2026-11-30T08:00:00Z"
    assert ev1.end_at == "2026-11-30T09:30:00Z"
    assert ev1.provenance["duration_minutes"] == 90
    assert ev1.course_id == "2AECO02"
    assert ev1.teacher == "Mme Martin"
    assert ev1.group == "2A-G1"
    assert ev1.room == "Amphi 2"

    assert ev2.status == "cancelled"
    assert ev2.busy is False
    assert ev2.course_id == "3A-ML-01-1"

    # FullCalendar List cancellation + course code
    fc_html = """
    <table>
      <tr class="fc-list-day" data-date="2026-12-01"><th colspan="2">Mardi 1 décembre 2026</th></tr>
      <tr class="fc-list-event"><td>10:00 - 12:00</td><td>[Annulé] TP Big Data 3A-ID-03-1</td></tr>
    </table>
    """
    fc_evs = parse_fullcalendar_list_html(fc_html)
    assert len(fc_evs) == 1
    assert fc_evs[0].status == "cancelled" and fc_evs[0].busy is False
    assert fc_evs[0].course_id == "3A-ID-03-1"

    # Verify compatibility with planner/source_bridge.to_import_item
    from backend.app.modules.ingestion.codex_bridge import _to_codex_records
    from backend.app.modules.ingestion.contracts import SourceKind, SourceRecord

    for ev, expect_imported in ((ev1, True), (ev2, False)):
        d = ev.to_dict()
        d["record_kind"] = "event"
        srec = SourceRecord(
            source_kind=SourceKind.PAMPLEMOUSSE,
            external_id=ev.external_id,
            title=ev.title,
            record_kind="event",
            payload=d,
        )
        codex_ev = _to_codex_records(srec)[0]
        imported = to_import_item(ev.external_id, codex_ev.model_dump(mode="json"), "pamplemousse")
        if expect_imported:
            assert imported is not None
            assert imported.subject_id == "2AECO02"
            assert imported.room == "Amphi 2"
            assert imported.teacher == "Mme Martin"
        else:
            assert imported is None
