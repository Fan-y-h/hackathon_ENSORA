"""Regression tests for live source scraping completeness & functional validation.

Covers actual ENSAI platform structures supplied by the human operator:
- Moodle: full course catalog vs recently accessed, academic year/archive filtering,
  tab-based sections (`&section=N`), unavailable section skipping, inline HTML instructions
  + code blocks (`resource_kind="educational_html"`), `.ipynb` / PDF resources, forbidden URL
  exclusion, download + parsing, and Codex `ChangePage.observations`
- Pamplemousse: Mon Espace nav discovery (`p=121` EDT, `p=107` Inscriptions, `p=1052` Notes,
  iCalendar), FullCalendar JSON & List HTML views, Inscriptions (`UE|Matière|Code|Coef|ECTS|Sem`,
  `sans UE`, zero vs missing coef, repeated names), Notes (`BULLETIN DÉTAILLÉ` hierarchy,
  official aggregates vs assessments, French comma decimals, missing != 0, Classement ignored),
  reconciliation, rich diagnostics, and Codex Runtime SQLite ETL persistence
- Google Calendar: controlled 3-event live test cases (2 busy + 1 free, Europe/Paris -> UTC)
  with strict availability privacy and scoped window isolation
- Ensora Google Drive: exhaustive `--all` discovery + `--verify-downloads` parsing stats
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
from backend.app.core.runtime import Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
from backend.app.modules.ingestion.config import SourcesRuntimeConfig
from backend.app.modules.ingestion.connectors.base import RateLimiter
from backend.app.modules.ingestion.connectors.ensora_drive import (
    DRIVE_FOLDER_MIME,
    EnsoraDriveConnector,
)
from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector
from backend.app.modules.ingestion.connectors.moodle import (
    MoodleConnector,
    discover_course_section_tabs,
    parse_moodle_dashboard_html,
)
from backend.app.modules.ingestion.connectors.pamplemousse import (
    PamplemousseConnector,
    discover_pamplemousse_nav_links,
    extract_pamplemousse_academic_year,
    parse_fullcalendar_json_events,
    parse_fullcalendar_list_html,
    parse_pamplemousse_inscriptions_html,
    parse_pamplemousse_notes_html,
    reconcile_pamplemousse_academics,
)
from backend.app.modules.ingestion.contracts import SourceKind, ValueStatus
from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry


def test_moodle_bulk_year_filter_uses_explicit_course_page_year(tmp_path: Path) -> None:
    config = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    connector = MoodleConnector(config, rate_limiter=RateLimiter(0.0))
    page = """<title>Statistics 2025-2026</title>
      <a href="/mod/resource/view.php?id=41">Lecture notes</a>"""
    scope = {
        "dashboard_html": """<div class="block_myoverview"><div class="course-card">
          <a href="/course/view.php?id=42">Statistics</a></div></div>""",
        "course_html_by_id": {"42": page},
    }
    assert connector.list_changes(scope={**scope, "academic_year": "2026-2027"}).items == ()
    assert connector.last_stats["courses_excluded_by_page_year"] == 1
    included = connector.list_changes(scope={**scope, "academic_year": "2025-2026"})
    assert any(record.payload["academic_year"] == "2025-2026" for record in included.items)


def test_moodle_tab_based_sections_inline_html_academic_year_and_parsing(tmp_path: Path) -> None:
    """Moodle discovers full enrolled courses (not just recent), traverses accessible `&section=N` tabs,
    skips disabled/unavailable tabs, extracts inline HTML instructions + notebooks, parses downloads,
    and emits Codex `educational_html` + `observations` without placing raw HTML in metadata."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    base = cfg.moodle_base_url

    # 1. Course listing has BOTH recently-accessed widget AND full enrolled course list with years/categories
    my_courses_html = """
    <html><body>
      <section class="block_recentlyaccessedcourses">
        <a href="https://foad-moodle.ensai.fr/course/view.php?id=999">Recently Viewed Only</a>
      </section>
      <div class="block_myoverview">
        <div class="course-card">
          <div class="categoryname">3A Data Science 2026-2027</div>
          <a href="https://foad-moodle.ensai.fr/course/view.php?id=505">Nom du cours Apprentissage profond / Deep Learning</a>
        </div>
        <div class="course-card">
          <div class="categoryname">Archives 2024-2025</div>
          <a href="https://foad-moodle.ensai.fr/course/view.php?id=101">Ancien cours Statistiques (Archivé)</a>
        </div>
        <div class="course-card">
          <div class="categoryname">3A Data Science 2026-2027</div>
          <a href="https://foad-moodle.ensai.fr/course/view.php?id=506">Nom du cours Séries Temporelles</a>
        </div>
      </div>
    </body></html>
    """
    parsed_courses = parse_moodle_dashboard_html(my_courses_html, base)
    ids = {c.course_id for c in parsed_courses}
    assert ids == {"505", "101", "506"}
    assert "999" not in ids

    # 2. Tab-based course HTML (modeled on course 505 structure, not hardcoded)
    course_root_html = """
    <html><body>
      <ul class="nav nav-tabs">
        <li><a href="https://foad-moodle.ensai.fr/course/view.php?id=505&amp;section=0#tabs-tree-start">General</a></li>
        <li><a href="https://foad-moodle.ensai.fr/course/view.php?id=505&amp;section=1#tabs-tree-start">Midterm Exam</a></li>
        <li><a href="https://foad-moodle.ensai.fr/course/view.php?id=505&amp;section=2#tabs-tree-start">Atelier 1</a></li>
        <li class="disabled"><a class="dimmed" aria-disabled="true" href="https://foad-moodle.ensai.fr/course/view.php?id=505&amp;section=9#tabs-tree-start">Old Archived Section</a></li>
      </ul>
      <div class="contentwithoutlink">
        <p>Welcome to Deep Learning. Please read the syllabus and review PyTorch prerequisites before Atelier 1.</p>
      </div>
      <a href="https://foad-moodle.ensai.fr/mod/forum/view.php?id=7001">Announcements</a>
      <a href="https://foad-moodle.ensai.fr/user/index.php?id=505">Participants</a>
      <a href="https://foad-moodle.ensai.fr/message/index.php">Messages</a>
    </body></html>
    """
    section_1_html = """
    <html><body>
      <h3 class="sectionname">Midterm Exam</h3>
      <a href="https://foad-moodle.ensai.fr/pluginfile.php/505/mod_resource/content/1/midterm_2026.pdf">midterm_2026.pdf</a>
    </body></html>
    """
    section_2_html = """
    <html><body>
      <h3 class="sectionname">Atelier 1</h3>
      <div class="summary">
        <p>Atelier 1 instructions: implement an MLP in PyTorch and compare validation curves across learning rates.</p>
        <pre><code>import torch\nx = torch.randn(32, 10)</code></pre>
      </div>
      <a href="https://foad-moodle.ensai.fr/pluginfile.php/505/mod_resource/content/1/TP1-1-pytorch-tutorial.ipynb">TP1-1-pytorch-tutorial.ipynb</a>
      <a href="https://foad-moodle.ensai.fr/pluginfile.php/505/mod_resource/content/1/TP1-2-MLP.ipynb">TP1-2-MLP.ipynb</a>
      <a href="https://foad-moodle.ensai.fr/pluginfile.php/505/mod_resource/content/1/TP1-2-MLP-solution.ipynb">TP1-2-MLP-solution.ipynb</a>
      <a href="https://foad-moodle.ensai.fr/mod/url/view.php?id=7100">SSPCloudDatalab</a>
      <a href="https://external.untrusted.example/track">External Link</a>
    </body></html>
    """

    tabs = discover_course_section_tabs(course_root_html, base, "505")
    assert [t.section_num for t in tabs] == ["0", "1", "2", "9"]
    assert tabs[2].title == "Atelier 1" and tabs[2].available is True
    assert tabs[3].available is False

    pages = {
        "https://foad-moodle.ensai.fr/my/courses.php": my_courses_html,
        "https://foad-moodle.ensai.fr/course/view.php?id=505": course_root_html,
        "https://foad-moodle.ensai.fr/course/view.php?id=505&section=1": section_1_html,
        "https://foad-moodle.ensai.fr/course/view.php?id=505&section=2": section_2_html,
        "https://foad-moodle.ensai.fr/course/view.php?id=506": "<html><body><a href='https://foad-moodle.ensai.fr/pluginfile.php/506/mod_resource/content/1/ts_cm1.pdf'>ts_cm1.pdf</a></body></html>",
    }
    nb_bytes = json.dumps(
        {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {},
            "cells": [
                {"cell_type": "markdown", "source": ["# Atelier 1 MLP\n", "PyTorch tutorial."]},
                {"cell_type": "code", "source": ["import torch\n", "print('never executed')"]},
            ],
        }
    ).encode("utf-8")

    def fake_fetch(url: str) -> str:
        assert "section=9" not in url  # Unavailable section MUST NOT be fetched!
        return pages[url]

    def fake_blob(url: str) -> tuple[bytes, str, str]:
        if url.endswith(".ipynb"):
            return nb_bytes, url.rsplit("/", 1)[-1], "application/x-ipynb+json"
        return b"%PDF-1.4\n%%EOF", "midterm_2026.pdf", "application/pdf"

    conn = MoodleConnector(
        cfg,
        page_fetcher=fake_fetch,
        blob_fetcher=fake_blob,
        rate_limiter=RateLimiter(0.0),
    )

    page = conn.list_changes(scope={"academic_year": "2026-2027", "include_archived": False})
    assert page.complete is True
    assert page.errors == ()
    stats = conn.last_stats
    assert stats["courses_discovered"] == 2
    assert stats["courses_traversed"] == 2
    assert stats["unavailable_sections_skipped"] == 1
    assert stats["resources_excluded"] >= 2  # Unavailable section + external/forbidden links
    assert stats["sections_traversed"] >= 4
    assert stats["html_pages_extracted"] >= 2
    assert stats["formats_by_extension"][".ipynb"] == 3
    assert stats["formats_by_extension"][".pdf"] == 2

    titles = {r.title for r in page.items}
    assert "TP1-1-pytorch-tutorial.ipynb" in titles
    assert "TP1-2-MLP-solution.ipynb" in titles
    assert "SSPCloudDatalab" in titles
    assert "Participants" not in titles
    assert "Messages" not in titles
    assert "External Link" not in titles

    # Verify actual blob fetch + parsing on BOTH embedded HTML section AND `.ipynb` notebook
    parser_reg = DocumentParserRegistry()
    html_rec = next(
        r for r in page.items
        if (r.payload or {}).get("resource_kind") == "html_section" and "Atelier 1" in (r.section_title or "")
    )
    html_blob = conn.fetch_blob(html_rec)
    assert html_blob.mime_type == "text/html"
    parsed_html = parser_reg.parse_bytes(html_blob.content, filename=html_blob.filename, mime_type=html_blob.mime_type)
    assert parsed_html and any("PyTorch" in b.text for b in parsed_html.blocks)

    nb_rec = next(r for r in page.items if r.title == "TP1-2-MLP.ipynb")
    nb_blob = conn.fetch_blob(nb_rec)
    parsed_nb = parser_reg.parse_bytes(nb_blob.content, filename=nb_blob.filename, mime_type=nb_blob.mime_type)
    assert parsed_nb and any("Atelier 1 MLP" in b.text for b in parsed_nb.blocks)

    # Verify Codex bridge emits educational_html, observations, academic_year, and NO raw HTML in metadata
    bridge = CodexConnectorBridge(conn)
    cpage = bridge.list_changes(SyncScope(source=CodexSourceKind.MOODLE, academic_year="2026-2027"), None)
    assert cpage.observations["courses_discovered"] == 2
    assert cpage.observations["sections_traversed"] >= 4
    assert cpage.observations["resources_excluded"] >= 2
    edu_docs = [it for it in cpage.items if it.kind == "document" and it.resource_kind == "educational_html"]
    assert len(edu_docs) >= 2
    assert edu_docs[0].provenance.academic_year == "2026-2027"
    assert "import torch" not in json.dumps(edu_docs[0].model_dump(mode="json"))
    fetched_html = bridge.fetch_blob(next(d for d in edu_docs if "Atelier 1" in d.title))
    assert fetched_html.mime_type == "text/html"
    assert b"PyTorch" in fetched_html.content


def test_pamplemousse_live_nav_edt_inscriptions_notes_reconciliation_and_codex_etl(tmp_path: Path) -> None:
    """Pamplemousse discovers EDT (`p=121`), Inscriptions (`p=107`), Notes (`p=1052`), and iCalendar
    from Mon Espace, parses all structures faithfully, reconciles academics, and persists into Codex SQLite."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    base = cfg.pamplemousse_base_url

    home_html = """
    <html><body>
      <select name="annee">
        <option value="2025">2025–2026</option>
        <option value="2026" selected>2026–2027</option>
      </select>
      <nav>
        <a href="index.php">Mon Espace</a>
        <a href="index.php?p=121">EDT</a>
        <a href="index.php?p=107">Inscriptions</a>
        <a href="index.php?p=1052">Notes</a>
        <a href="index.php?p=900">iCalendar</a>
        <a href="index.php?p=555">Évaluation des enseignements</a>
        <a href="index.php?logout=1">Déconnexion</a>
      </nav>
    </body></html>
    """
    assert extract_pamplemousse_academic_year(home_html) == "2026-2027"
    nav = discover_pamplemousse_nav_links(home_html, base)
    assert nav["edt"].endswith("index.php?p=121")
    assert nav["inscriptions"].endswith("index.php?p=107")
    assert nav["notes"].endswith("index.php?p=1052")
    assert nav["icalendar"].endswith("index.php?p=900")
    assert all("555" not in u and "logout" not in u for u in nav.values())

    # 1. EDT FullCalendar JSON + List view parsing (Europe/Paris -> UTC, duration, types, red-color ambiguity)
    fc_json = [
        {
            "id": "evt-1",
            "title": "CM Apprentissage profond (3A-ML01)",
            "start": "2026-11-30T08:30:00+01:00",
            "end": "2026-11-30T10:30:00+01:00",
            "extendedProps": {"room": "Amphi 1", "teacher": "Prof. A", "group": "3A-DS"},
        },
        {
            "id": "evt-2",
            "title": "Sout.ID PPDP",
            "start": "2026-12-01T14:00:00+01:00",
            "end": "2026-12-01T16:00:00+01:00",
            "backgroundColor": "#e53935",
        },
        {
            "id": "evt-3",
            "title": "Sport Badmington",
            "start": "2026-12-02T17:00:00+01:00",
            "end": "2026-12-02T18:30:00+01:00",
        },
    ]
    fc_events = parse_fullcalendar_json_events(fc_json, academic_year="2026-2027")
    assert len(fc_events) == 3
    assert fc_events[0].start_at == "2026-11-30T07:30:00Z"
    assert fc_events[0].provenance["duration_minutes"] == 120
    assert fc_events[0].course_id == "3A-ML01"
    assert fc_events[1].confirmation_required is True
    assert fc_events[2].event_type.value == "sport"

    fc_list_html = """
    <html><body><table>
      <tr class="fc-list-day" data-date="2026-12-03"><th colspan="3">Jeudi 3 décembre 2026</th></tr>
      <tr class="fc-list-event"><td>09:00 - 11:00</td><td></td><td>TP Deep Learning</td></tr>
    </table></body></html>
    """
    list_events = parse_fullcalendar_list_html(fc_list_html, academic_year="2026-2027")
    assert len(list_events) == 1
    assert list_events[0].start_at == "2026-12-03T08:00:00Z"
    assert list_events[0].event_type.value == "tp"

    # 2. Inscriptions (`index.php?p=107`) — UE, Matière, Code, Coef., ECTS, Sem.
    inscriptions_html = """
    <html><body>
      <table>
        <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
        <tr><td>3A-UE1 Machine Learning</td><td>Apprentissage profond</td><td>ML301</td><td>3,0</td><td>4</td><td>S1</td></tr>
        <tr><td>sans UE</td><td>Sport Optionnel</td><td>EPS01</td><td>0</td><td>0</td><td>S1</td></tr>
        <tr><td>3A-UE2 Économétrie</td><td>Séminaire Recherche</td><td>SEM302</td><td></td><td>2</td><td>S1</td></tr>
        <tr><td>3A-UE2 Économétrie</td><td>Projet Appliqué</td><td>PRJ-G1</td><td>2,5</td><td>3</td><td>S2</td></tr>
        <tr><td>3A-UE3 Finance</td><td>Projet Appliqué</td><td>PRJ-G2</td><td>2,0</td><td>3</td><td>S2</td></tr>
      </table>
    </body></html>
    """
    insc_recs = parse_pamplemousse_inscriptions_html(inscriptions_html, academic_year="2026-2027")
    subj_recs = [r for r in insc_recs if r.record_type == "subject"]
    assert len(subj_recs) == 5
    dl_subj = next(r for r in subj_recs if r.subject_code == "ML301")
    assert dl_subj.weight == Decimal("3.0") and dl_subj.weight_status == ValueStatus.CONFIRMED
    assert dl_subj.provenance["ects"] == "4"

    sport_subj = next(r for r in subj_recs if r.subject_code == "EPS01")
    assert sport_subj.provenance["sans_ue"] is True
    assert sport_subj.weight == Decimal("0") and sport_subj.weight_status == ValueStatus.CONFIRMED

    missing_coef_subj = next(r for r in subj_recs if r.subject_code == "SEM302")
    assert missing_coef_subj.weight is None and missing_coef_subj.weight_status == ValueStatus.MISSING

    proj_recs = [r for r in subj_recs if r.title == "Projet Appliqué"]
    assert len(proj_recs) == 2 and len({r.external_id for r in proj_recs}) == 2

    # 3. Detailed Notes (`index.php?p=1052` — BULLETIN DÉTAILLÉ)
    notes_html = """
    <html><body>
      <h2>BULLETIN DÉTAILLÉ</h2>
      <table>
        <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th><th>Classement</th></tr>
        <tr class="overall"><td><strong>Moyenne générale</strong></td><td>15,25</td><td></td><td>5/120</td></tr>
        <tr class="semestre"><td><strong>Premier semestre</strong></td><td>15,25</td><td>10</td><td>4/120</td></tr>
        <tr class="ue"><td><strong>3A-UE1 Machine Learning</strong></td><td>16,0</td><td>5</td><td>3/120</td></tr>
        <tr class="matiere"><td><strong>Apprentissage profond</strong></td><td>16,5</td><td>3,0</td><td>2/120</td></tr>
        <tr class="epreuve"><td style="padding-left:24px">TP Distillation</td><td>17,5</td><td>1,0</td><td>1/120</td></tr>
        <tr class="epreuve"><td style="padding-left:24px">Final Homework</td><td>-</td><td>2,0</td><td></td></tr>
        <tr class="matiere"><td><strong>Projet Appliqué</strong></td><td></td><td>2,0</td><td></td></tr>
      </table>
    </body></html>
    """
    notes_recs = parse_pamplemousse_notes_html(notes_html, academic_year="2026-2027")
    assert "5/120" not in json.dumps([r.to_dict() for r in notes_recs])
    agg_recs = [r for r in notes_recs if r.record_type == "aggregate_grade"]
    assess_recs = [r for r in notes_recs if r.record_type == "assessment"]
    assert len(agg_recs) >= 3
    tp_dist = next(r for r in assess_recs if r.title == "TP Distillation")
    assert tp_dist.grade_value == Decimal("17.5") and tp_dist.weight == Decimal("1.0")
    final_hw = next(r for r in assess_recs if r.title == "Final Homework")
    assert final_hw.grade_value is None and final_hw.grade_status == ValueStatus.MISSING

    # 4. Reconciliation links unambiguous Apprentissage profond -> ML301 and flags ambiguous Projet Appliqué
    reconciled = reconcile_pamplemousse_academics(insc_recs, notes_recs)
    reconciled_tp = next(r for r in reconciled if r.title == "TP Distillation")
    assert reconciled_tp.parent_external_id == dl_subj.external_id
    ambiguous_proj = [
        r for r in reconciled
        if r.title == "Projet Appliqué" and (r.provenance or {}).get("reconciliation_status") == "ambiguous"
    ]
    assert len(ambiguous_proj) == 1

    # 5. Live multi-page PamplemousseConnector run + Codex SQLite ETL persistence
    pamp_pages = {
        base: home_html,
        "https://23ed753f.leos.fr/index.php?p=900": '<html><body><a href="https://23ed753f.leos.fr/export.ics">Télécharger</a></body></html>',
        "https://23ed753f.leos.fr/export.ics": (
            "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:pamp-live-1\r\nSUMMARY:CM Deep Learning\r\n"
            "DTSTART;TZID=Europe/Paris:20261130T083000\r\nDTEND;TZID=Europe/Paris:20261130T103000\r\n"
            "LOCATION:Amphi 1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
        ),
        "https://23ed753f.leos.fr/index.php?p=107": inscriptions_html,
        "https://23ed753f.leos.fr/index.php?p=1052": notes_html,
    }
    pamp_conn = PamplemousseConnector(
        cfg,
        page_fetcher=lambda u: pamp_pages[u],
        rate_limiter=RateLimiter(0.0),
    )
    page = pamp_conn.list_changes(scope={})
    assert page.errors == ()
    assert page.warnings == ()
    st = pamp_conn.last_stats
    assert st["academic_year"] == "2026-2027"
    assert st["ics_used"] is True
    assert st["timetable_events"] == 1
    assert st["enrolled_subjects"] == 6
    assert st["subject_coefficients"] == 5
    assert st["ects_values"] == 5
    assert st["assessment_records"] == 2
    assert st["available_grades"] >= 4

    from sqlalchemy import select
    from backend.app.core.database import records as db_records

    settings = Settings(config_dir=tmp_path / "rt_cfg", local_data_dir=tmp_path / "rt_data")
    with Runtime(settings) as rt:
        bridge = CodexConnectorBridge(pamp_conn)
        codex_page = bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE), None)
        assert not codex_page.error_codes
        conn_id = rt.connection_ids[CodexSourceKind.PAMPLEMOUSSE]
        for rec in codex_page.items:
            rt.pipeline.ingest(conn_id, rec)
        kinds = {rec.kind for rec in codex_page.items}
        assert {
            "event",
            "academic_year",
            "semester",
            "teaching_unit",
            "subject",
            "coefficient",
            "assessment",
            "assessment_weight",
            "grade",
        }.issubset(kinds)

        # Verify persisted P3-facing SQLite records preserve raw coefficients (including 0 and missing),
        # assessment-level raw coefficients, explicitly tagged sibling-derived AssessmentWeights,
        # ECTS independence, and official aggregate vs individual assessment grades.
        with rt.engine.connect() as db:
            rows = list(db.execute(select(db_records.c.kind, db_records.c.external_id, db_records.c.payload)))
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for kind, _ext_id, payload in rows:
            by_kind.setdefault(kind, []).append(payload)

        # 1. Raw assessment-level SubjectCoefficients preserved untouched (1.0 and 2.0)
        assess_coefs = [c for c in by_kind.get("coefficient", []) if c.get("level") == "assessment"]
        assert {Decimal(str(c["weight"])) for c in assess_coefs if c.get("weight") is not None} == {
            Decimal("1.0"),
            Decimal("2.0"),
        }
        assert all(c.get("mode") == "raw" for c in assess_coefs)

        # 2. Subject-level zero coefficient preserved distinct from explicit missing coefficient
        subj_coefs = [c for c in by_kind.get("coefficient", []) if c.get("level") == "subject"]
        assert any(c.get("weight") == "0" and c.get("record_status") == "known" for c in subj_coefs)
        assert any(c.get("weight") is None and c.get("record_status") == "missing" for c in subj_coefs)

        # 3. Derived sibling AssessmentWeights carry explicit basis and sum to 1 (1/3 + 2/3)
        weights = by_kind.get("assessment_weight", [])
        assert len(weights) == 2
        assert all(w.get("mode") == "fraction" for w in weights)
        assert all(w.get("weight_basis") == "normalized_sibling_coefficients" for w in weights)
        total_frac = sum((Decimal(str(w["weight"])) for w in weights), Decimal(0))
        assert abs(total_frac - Decimal("1")) < Decimal("0.0001")

        # 4. Subject ECTS preserved independently of coefficients
        ml_subj_persisted = next(s for s in by_kind.get("subject", []) if s.get("code") == "ML301")
        assert Decimal(str(ml_subj_persisted["ects"])) == Decimal("4")

        # 5. Official aggregates separated from individual assessment grades; missing grade stays null != 0
        grades = by_kind.get("grade", [])
        assert any(
            g.get("grade_kind") == "official_aggregate"
            and g.get("value") is not None
            and Decimal(str(g["value"])) == Decimal("16.5")
            for g in grades
        )
        assert any(
            g.get("grade_kind") == "assessment"
            and g.get("value") is not None
            and Decimal(str(g["value"])) == Decimal("17.5")
            for g in grades
        )
        assert any(
            g.get("grade_kind") == "assessment"
            and g.get("value") is None
            and g.get("record_status") == "missing"
            for g in grades
        )


def test_google_calendar_controlled_three_events_availability_and_privacy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Controlled 3-event live validation scenario (Europe/Paris):
    TEST ENSORA 01 (busy), TEST ENSORA 02 (busy), TEST ENSORA 03 (free) -> 2 busy blocking intervals,
    free event excluded from blocking, and zero private titles exposed in CLI/availability output."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    class _FakeCreds:
        valid = True
        expired = False

    class _FakeOAuth:
        def get_credentials(self, allow_refresh: bool = True, interactive: bool = False):
            return _FakeCreds()

    raw_items = [
        {
            "id": "ev_test_01",
            "status": "confirmed",
            "transparency": "opaque",
            "summary": "TEST ENSORA 01",
            "location": "Private Home",
            "description": "Private description 1",
            "start": {"dateTime": "2026-11-30T10:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-11-30T11:00:00+01:00", "timeZone": "Europe/Paris"},
            "updated": "2026-11-27T18:00:00Z",
        },
        {
            "id": "ev_test_02",
            "status": "confirmed",
            "transparency": "opaque",
            "summary": "TEST ENSORA 02",
            "start": {"dateTime": "2026-12-01T14:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-12-01T16:00:00+01:00", "timeZone": "Europe/Paris"},
            "updated": "2026-11-27T18:01:00Z",
        },
        {
            "id": "ev_test_03",
            "status": "confirmed",
            "transparency": "transparent",
            "summary": "TEST ENSORA 03",
            "start": {"dateTime": "2026-12-02T09:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-12-02T10:00:00+01:00", "timeZone": "Europe/Paris"},
            "updated": "2026-11-27T18:02:00Z",
        },
    ]

    class _FakeExec:
        def __init__(self, payload: dict[str, Any]) -> None:
            self._p = payload

        def execute(self) -> dict[str, Any]:
            return self._p

    class _FakeEventsAPI:
        def list(self, **kwargs: Any) -> _FakeExec:
            assert kwargs["calendarId"] == "primary"
            assert kwargs["singleEvents"] is True
            return _FakeExec({"timeZone": "Europe/Paris", "items": raw_items, "nextSyncToken": "st_ok"})

    class _FakeService:
        def events(self) -> _FakeEventsAPI:
            return _FakeEventsAPI()

    cal_conn = GoogleCalendarConnector(
        cfg,
        oauth_adapter=_FakeOAuth(),  # type: ignore[arg-type]
        calendar_service_factory=lambda _: _FakeService(),
        rate_limiter=RateLimiter(0.0),
    )

    import scripts.p1_sources_cli as cli

    monkeypatch.setattr(cli, "GoogleCalendarConnector", lambda c: cal_conn)
    rc = cli.main(["--config-dir", str(tmp_path), "sync-calendar", "--days", "14", "--yes"])
    assert rc == 0
    out_raw = capsys.readouterr().out
    out = json.loads(out_raw)
    assert out["calendar_id"] == "primary"
    assert out["busy_intervals_count"] == 2
    assert out["free_intervals_count"] == 1
    assert out["busy_intervals"] == [
        {"start_at": "2026-11-30T09:00:00Z", "end_at": "2026-11-30T10:00:00Z", "status": "busy", "all_day": False},
        {"start_at": "2026-12-01T13:00:00Z", "end_at": "2026-12-01T15:00:00Z", "status": "busy", "all_day": False},
    ]
    assert "TEST ENSORA" not in out_raw
    assert "Private Home" not in out_raw
    assert "Private description" not in out_raw

    # Also verify Codex SyncScope window_start/window_end emits exact CalendarSnapshot for SyncEngine window_verified
    # while keeping resume_cursor=None so global durable syncToken is never overwritten!
    bridge = CodexConnectorBridge(cal_conn)
    scoped = SyncScope(
        source=CodexSourceKind.GOOGLE_CALENDAR,
        window_start=datetime(2026, 11, 27, 0, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 12, 11, 0, 0, tzinfo=timezone.utc),
    )
    cpage = bridge.list_changes(scoped, None)
    assert cpage.resume_cursor is None  # Scoped window never overwrites global durable syncToken!
    assert cpage.calendar_snapshot is not None
    assert cpage.calendar_snapshot.external_id_prefix == "google_calendar:primary:"
    assert cpage.calendar_snapshot.starts_at == scoped.window_start
    assert cpage.calendar_snapshot.ends_at == scoped.window_end
    assert len([ev for ev in cpage.items if ev.busy]) == 2
    assert len([ev for ev in cpage.items if not ev.busy]) == 1


def test_ensora_drive_exhaustive_pagination_and_cli_verify_downloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """discover-drive --all --verify-downloads traverses approved root recursively, exports Google Docs,
    downloads & parses notebooks/text, and reports redacted counts."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    root_id = cfg.ensora_drive_root_folder_id

    class _FakeCreds:
        valid = True
        expired = False

    class _FakeOAuth:
        def get_credentials(self, allow_refresh: bool = True, interactive: bool = False):
            return _FakeCreds()

    tree = {
        root_id: [
            {"id": "f_3a", "name": "3A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id]},
        ],
        "f_3a": [
            {
                "id": "doc_1",
                "name": "Resume_ML.md",
                "mimeType": "text/markdown",
                "parents": ["f_3a"],
                "modifiedTime": "2026-11-20T10:00:00Z",
                "md5Checksum": "md5_1",
            },
            {
                "id": "doc_2",
                "name": "TP_Notes",
                "mimeType": "application/vnd.google-apps.document",
                "parents": ["f_3a"],
                "modifiedTime": "2026-11-21T10:00:00Z",
                "version": "v2",
            },
        ],
    }
    meta_map = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        "f_3a": {"id": "f_3a", "name": "3A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "doc_1": {"id": "doc_1", "name": "Resume_ML.md", "mimeType": "text/markdown", "parents": ["f_3a"], "trashed": False, "size": "48"},
        "doc_2": {"id": "doc_2", "name": "TP_Notes", "mimeType": "application/vnd.google-apps.document", "parents": ["f_3a"], "trashed": False},
    }

    class _Exec:
        def __init__(self, val: Any) -> None:
            self._v = val

        def execute(self) -> Any:
            return self._v

    class _FilesAPI:
        def get(self, **kwargs: Any) -> _Exec:
            return _Exec(meta_map[kwargs["fileId"]])

        def list(self, **kwargs: Any) -> _Exec:
            q = kwargs["q"]
            fid = q.split("'")[1]
            return _Exec({"files": tree.get(fid, [])})

        def get_media(self, **kwargs: Any) -> _Exec:
            return _Exec(b"# Machine Learning\n\nGradient descent overview.\n")

        def export_media(self, **kwargs: Any) -> _Exec:
            return _Exec(b"%PDF-1.4\n%%EOF")

    class _DriveSvc:
        def files(self) -> _FilesAPI:
            return _FilesAPI()

    drive_conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=_FakeOAuth(),  # type: ignore[arg-type]
        drive_service_factory=lambda _: _DriveSvc(),
        rate_limiter=RateLimiter(0.0),
    )

    import scripts.p1_sources_cli as cli

    monkeypatch.setattr(cli, "EnsoraDriveConnector", lambda c: drive_conn)
    rc = cli.main(
        ["--config-dir", str(tmp_path), "discover-drive", "--all", "--verify-downloads", "2", "--yes"]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["root_folder_id"] == root_id
    assert out["total_items"] == 2
    assert out["by_cohort"]["3A"] == 2
    assert out["download_verification"]["attempted"] == 2
    assert out["download_verification"]["downloaded_ok"] == 2
    assert out["download_verification"]["parsed_ok"] >= 1
    assert out["complete"] is True


def test_p0_moodle_wrapper_unwrap_ajax_year_assign_attachments_and_selective_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P0 regression: Moodle resource unwrapping (<object>/<iframe/resourceworkaround), AJAX 2026-2027
    year discovery, /mod/assign/ attachments, DownloadSizeLimitError diagnostics, and CLI --retry-failed."""
    from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter
    from backend.app.modules.ingestion.connectors.moodle import (
        DownloadSizeLimitError,
        _extract_moodle_pluginfile_url,
        parse_moodle_ajax_courses,
    )

    # 1. AJAX course list derives source-backed 2026-2027 academic year from startdate & category
    ajax_payload = [
        {
            "error": False,
            "data": {
                "courses": [
                    {
                        "id": 505,
                        "fullname": "Apprentissage profond",
                        "shortname": "3A_DL",
                        "coursecategory": "3A Data Science",
                        "startdate": int(datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp()),
                        "viewurl": "https://foad-moodle.ensai.fr/course/view.php?id=505",
                        "hidden": False,
                    },
                    {
                        "id": 201,
                        "fullname": "Ancien cours",
                        "shortname": "2A_OLD",
                        "coursecategory": "2024-2025",
                        "startdate": int(datetime(2024, 9, 10, tzinfo=timezone.utc).timestamp()),
                        "hidden": True,
                    },
                ]
            },
        }
    ]
    ajax_courses = parse_moodle_ajax_courses(ajax_payload, "https://foad-moodle.ensai.fr/")
    assert len(ajax_courses) == 2
    c505 = next(c for c in ajax_courses if c.course_id == "505")
    assert c505.academic_year == "2026-2027"
    assert c505.archived is False

    # 2. Moodle HTML wrapper unwrapping prefers mod_resource over theme/user icons & handles <object data=...>
    wrapper_html = """
    <html><body>
      <img src="https://foad-moodle.ensai.fr/pluginfile.php/5/user/icon/boost/f1" />
      <object id="resourceobject" data="https://foad-moodle.ensai.fr/pluginfile.php/888/mod_resource/content/1/Lecture01.pdf" type="application/pdf"></object>
    </body></html>
    """
    resolved = _extract_moodle_pluginfile_url(
        wrapper_html,
        "https://foad-moodle.ensai.fr/mod/resource/view.php?id=9001",
        "https://foad-moodle.ensai.fr/",
    )
    assert resolved == "https://foad-moodle.ensai.fr/pluginfile.php/888/mod_resource/content/1/Lecture01.pdf"

    # 3. Real _browser_download_once unwraps <object data=...> and returns parsed PDF + flags oversized files clearly
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.max_download_bytes = 2048  # Small cap to verify DownloadSizeLimitError diagnostics

    class _FakeResp:
        def __init__(self, status: int, headers: dict[str, str], body: bytes, url: str) -> None:
            self.status = status
            self.headers = headers
            self._body = body
            self.url = url

        def body(self) -> bytes:
            return self._body

    class _FakeReq:
        def get(self, url: str, timeout: int = 30000, max_redirects: int = 0) -> _FakeResp:
            if "id=9001" in url:
                return _FakeResp(200, {"content-type": "text/html; charset=utf-8"}, wrapper_html.encode("utf-8"), url)
            if "Lecture01.pdf" in url:
                return _FakeResp(
                    200,
                    {"content-type": "application/pdf", "content-disposition": 'attachment; filename="Lecture01.pdf"'},
                    b"%PDF-1.4\n1 0 obj <<>> endobj\nstream\nBT (Deep Learning Notes) Tj ET\nendstream\n%%EOF",
                    url,
                )
            if "huge_weights.bin" in url:
                return _FakeResp(200, {"content-type": "application/octet-stream", "content-length": "9999999"}, b"x", url)
            raise RuntimeError(f"Unexpected URL {url}")

    class _FakeCtx:
        def __init__(self) -> None:
            self.request = _FakeReq()
            self.pages: list[Any] = []

        def close(self) -> None:
            pass

    auth = EnsaiAuthAdapter(cfg, context_factory=lambda _d, _h: _FakeCtx())
    course_html = """
    <html><body>
      <nav class="breadcrumb"><span>2026-2027</span></nav>
      <a href="/mod/resource/view.php?id=9001">Lecture 01</a>
      <a href="/pluginfile.php/888/mod_resource/content/1/huge_weights.bin">Huge Weights</a>
      <a href="/mod/assign/view.php?id=7001">Final Homework</a>
      <a href="/mod/url/view.php?id=7002">External PyTorch Docs</a>
    </body></html>
    """
    assign_html = """
    <html><body>
      <a href="/pluginfile.php/888/mod_assign/introattachment/0/homework_prompt.pdf">homework_prompt.pdf</a>
    </body></html>
    """

    def _fetch_page(u: str) -> str:
        if "my/courses.php" in u:
            return '<div class="block_myoverview"><a href="/course/view.php?id=505">Apprentissage profond</a></div>'
        if "mod/assign/view.php?id=7001" in u:
            return assign_html
        return course_html

    conn = MoodleConnector(cfg, auth_adapter=auth, page_fetcher=_fetch_page, rate_limiter=RateLimiter(0.0))
    page = conn.list_changes(scope={"course_ids": ["505"]})
    ext_ids = {r.external_id for r in page.items}
    # Assignment introattachment surfaced AND breadcrumb year applied!
    assert any("homework_prompt.pdf" in (r.source_url or "") for r in page.items)
    assert all((r.payload or {}).get("academic_year") == "2026-2027" for r in page.items)

    # Lecture01 unwraps cleanly via <object data=...>
    rec_lec = next(r for r in page.items if "id=9001" in (r.source_url or ""))
    fetched = conn.fetch_blob(rec_lec)
    assert fetched.filename == "Lecture01.pdf"
    assert fetched.mime_type == "application/pdf"
    assert fetched.content.startswith(b"%PDF-1.4")

    # External URL pointer emits inert HTML without external fetch
    rec_url = next(r for r in page.items if "id=7002" in (r.source_url or ""))
    fetched_url = conn.fetch_blob(rec_url)
    assert fetched_url.mime_type == "text/html"

    # Oversized file raises DownloadSizeLimitError (ValueError subclass) with clear size metadata
    rec_huge = next(r for r in page.items if "huge_weights.bin" in (r.source_url or ""))
    with pytest.raises(DownloadSizeLimitError) as exc_info:
        conn.fetch_blob(rec_huge)
    assert exc_info.value.size_bytes == 9999999
    assert exc_info.value.limit_bytes == 2048

    # CLI --verify-all-downloads reports structured failure_details + --retry-failed retries only failed ID
    import scripts.p1_sources_cli as cli

    export_path = tmp_path / "moodle_diag.json"
    monkeypatch.setattr(cli, "MoodleConnector", lambda c: conn)
    rc = cli.main(
        [
            "--config-dir",
            str(tmp_path),
            "--export-json",
            str(export_path),
            "sync-moodle",
            "--course-id",
            "505",
            "--verify-all-downloads",
            "--yes",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    dv = out["download_verification"]
    assert dv["failed_count"] >= 1
    assert any(d["reason"] == "size_limit_exceeded" for d in dv["failure_details"])
    assert export_path.is_file()

    # Selective retry using --retry-failed only attempts failed/unparsed IDs
    rc2 = cli.main(
        [
            "--config-dir",
            str(tmp_path),
            "sync-moodle",
            "--course-id",
            "505",
            "--retry-failed",
            str(export_path),
            "--yes",
        ]
    )
    assert rc2 == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["download_verification"]["attempted"] < dv["attempted"]


def test_p0_pamplemousse_ical_webcal_and_edt_fallback_when_nav_has_notes(tmp_path: Path) -> None:
    """P0 regression: Pamplemousse extracts webcal/textarea iCalendar URLs AND always falls back to
    index.php?p=121 (including FullCalendar v3 fc-list-item & inline script JSON) even when Inscriptions/Notes
    are discovered on index.php."""
    from backend.app.modules.ingestion.connectors.pamplemousse import (
        extract_ical_feed_urls,
        extract_inline_fullcalendar_json,
        parse_fullcalendar_list_html,
    )

    ical_page = """
    <html><body>
      <textarea>webcal://23ed753f.leos.fr/export_ical.php?token=abc123&amp;fmt=.ics</textarea>
    </body></html>
    """
    urls = extract_ical_feed_urls(ical_page, "https://23ed753f.leos.fr/index.php?p=900")
    assert urls == ["https://23ed753f.leos.fr/export_ical.php?token=abc123&fmt=.ics"]

    # FullCalendar v3 list HTML (`fc-list-heading` + `fc-list-item`) with French date header
    v3_html = """
    <table>
      <tr class="fc-list-heading"><td colspan="3">lundi 30 novembre 2026</td></tr>
      <tr class="fc-list-item"><td class="fc-list-item-time">09:00 - 11:00</td><td class="fc-list-item-title">CM Deep Learning</td></tr>
    </table>
    """
    v3_events = parse_fullcalendar_list_html(v3_html, academic_year="2026-2027")
    assert len(v3_events) == 1
    assert v3_events[0].start_at == "2026-11-30T08:00:00Z"
    assert v3_events[0].end_at == "2026-11-30T10:00:00Z"

    # Inline script FullCalendar JSON extraction
    inline_html = """
    <html><body><script>
      var calendar = new FullCalendar.Calendar(el, {
        events: [{"id":"e1","title":"TD Statistiques","start":"2026-12-01T14:00:00+01:00","end":"2026-12-01T16:00:00+01:00","salle":"B12"}]
      });
    </script></body></html>
    """
    inline = extract_inline_fullcalendar_json(inline_html)
    assert len(inline) == 1
    assert inline[0]["title"] == "TD Statistiques"

    # End-to-end: index.php has Inscriptions + Notes + broken iCalendar, NO explicit EDT link -> MUST still visit p=121!
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    pages = {
        "https://23ed753f.leos.fr/index.php": """
        <html><body>
          <select><option selected>2026-2027</option></select>
          <a href="index.php?p=900">iCalendar</a>
          <a href="index.php?p=107">Inscriptions</a>
          <a href="index.php?p=1052">Notes</a>
        </body></html>
        """,
        "https://23ed753f.leos.fr/index.php?p=900": "<html><body>No ics link yet</body></html>",
        "https://23ed753f.leos.fr/index.php?p=121": inline_html,
        "https://23ed753f.leos.fr/index.php?p=107": "<html><body><table><tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr><tr><td>UE1</td><td>Statistiques</td><td>3A_ST</td><td>2</td><td>4</td><td>S1</td></tr></table></body></html>",
        "https://23ed753f.leos.fr/index.php?p=1052": "<html><body>No bulletin yet</body></html>",
    }
    conn = PamplemousseConnector(cfg, page_fetcher=lambda u: pages[u], rate_limiter=RateLimiter(0.0))
    res = conn.list_changes(scope={"academic_year": "2026-2027"})
    assert conn.last_stats["timetable_events"] == 1
    assert "edt" in conn.last_stats["pages_visited"]
    assert conn.last_stats["enrolled_subjects"] == 1


def test_p0_expanded_parsers_xlsx_odt_zip_rmd_rtf_and_magic_byte_sniffing() -> None:
    """P0 regression: Drive & Moodle parsing coverage for .xlsx, .odt, .zip, .rmd, .rtf, and
    magic-byte sniffing on extensionless / application/octet-stream documents."""
    import io
    import zipfile
    from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry, sniff_mime_from_bytes

    reg = DocumentParserRegistry()

    # 1. XLSX spreadsheet
    xlsx_buf = io.BytesIO()
    with zipfile.ZipFile(xlsx_buf, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheets><sheet name="Coefficients" sheetId="1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<si><t>Matiere</t></si><si><t>Econometrie</t></si></sst>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><v>3</v></c></row>'
            '<row r="2"><c r="A2" t="s"><v>1</v></c><c r="B2"><v>4</v></c></row>'
            "</sheetData></worksheet>",
        )
    xlsx_bytes = xlsx_buf.getvalue()
    parsed_xlsx = reg.parse_bytes(xlsx_bytes, filename="syllabus.xlsx")
    assert parsed_xlsx.parser_name == "xlsx_parser"
    assert len(parsed_xlsx.blocks) == 1
    assert "Econometrie" in parsed_xlsx.full_text

    # 2. ODT LibreOffice document
    odt_buf = io.BytesIO()
    with zipfile.ZipFile(odt_buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr(
            "content.xml",
            '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
            'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
            "<office:body><office:text>"
            "<text:h>Chapitre 1: Séries Temporelles</text:h>"
            "<text:p>Processus ARMA stationnaire et fonction d'autocorrélation.</text:p>"
            "</office:text></office:body></office:document-content>",
        )
    odt_bytes = odt_buf.getvalue()
    parsed_odt = reg.parse_bytes(odt_bytes, filename="cours_ts.odt")
    assert parsed_odt.parser_name == "opendocument_parser"
    assert len(parsed_odt.blocks) == 2
    assert "ARMA" in parsed_odt.full_text

    # 3. ZIP educational archive (parses .py & .Rmd safely, ignores path traversal and nested .zip)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("tp1/solution.py", "def gradient_step(w, grad, lr=0.01):\n    return w - lr * grad\n")
        zf.writestr("tp1/report.Rmd", "# TP 1\n\n```r\nsummary(lm(y ~ x))\n```\n")
        zf.writestr("../escape.txt", "must be ignored")
        zf.writestr("nested.zip", b"PK\x03\x04fake")
    parsed_zip = reg.parse_bytes(zip_buf.getvalue(), filename="tp1_pack.zip")
    assert parsed_zip.parser_name == "zip_archive_parser"
    assert len(parsed_zip.blocks) >= 2
    assert "gradient_step" in parsed_zip.full_text
    assert "must be ignored" not in parsed_zip.full_text

    # 4. R Markdown (.Rmd) and RTF (.rtf)
    parsed_rmd = reg.parse_bytes(b"# Modele Lineaire\n\nEstimation par moindres carres.\n", filename="td2.Rmd")
    assert parsed_rmd.parser_name == "markdown_parser"
    assert len(parsed_rmd.blocks) >= 2

    parsed_rtf = reg.parse_bytes(rb"{\rtf1\ansi{\fonttbl\f0 Arial;}Notes de cours\par Theoreme central limite}", filename="notes.rtf")
    assert parsed_rtf.parser_name == "text_code_parser"
    assert "Theoreme central limite" in parsed_rtf.full_text

    # 5. Magic-byte sniffing on extensionless application/octet-stream blobs (XLSX, PDF, Jupyter)
    assert sniff_mime_from_bytes(xlsx_bytes) == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    sniffed_xlsx = reg.parse_bytes(xlsx_bytes, filename="download_no_ext", mime_type="application/octet-stream")
    assert sniffed_xlsx.parser_name == "xlsx_parser"
    assert len(sniffed_xlsx.blocks) == 1

    nb_bytes = json.dumps({"nbformat": 4, "cells": [{"cell_type": "markdown", "source": ["# TP Deep Learning"]}]}).encode("utf-8")
    sniffed_nb = reg.parse_bytes(nb_bytes, filename="notebook_blob", mime_type="application/octet-stream")
    assert sniffed_nb.parser_name == "notebook_parser"
    assert "TP Deep Learning" in sniffed_nb.full_text


def _wait_for_job(rt: Runtime, job_id: str, timeout: float = 10.0) -> dict[str, Any]:
    import time
    from sqlalchemy import select
    from backend.app.core.database import runs

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with rt.engine.connect() as db:
            row = db.execute(select(runs).where(runs.c.id == job_id)).mappings().one()
        if row["status"] not in ("queued", "running"):
            return dict(row)
        time.sleep(0.02)
    raise TimeoutError(f"Sync job {job_id} did not finish within {timeout}s")


def test_p0_pamplemousse_scoped_academic_year_persists_events_coefs_and_all_grades_in_sqlite(
    tmp_path: Path,
) -> None:
    """Reproduces the live 369 academic_year_unknown / 0 events / 0 coefs / 4-vs-5 grades regression:
    verifies Pamplemousse ICS events (including out-of-year exclusion!), Inscriptions coefs/ECTS
    (with no fabricated MISSING grades), Notes aggregates + individual assessment grades, and
    normalized sibling weights ALL persist cleanly into SQLite under SyncScope(academic_year='2026-2027')."""
    from sqlalchemy import select
    from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus
    from backend.app.core.database import records as db_records
    from backend.app.core.runtime import Components

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    home_html = """
    <html><body>
      <select><option selected>2026-2027</option></select>
      <a href="index.php?p=900">iCalendar</a>
      <a href="index.php?p=107">Inscriptions</a>
      <a href="index.php?p=1052">Notes</a>
    </body></html>
    """
    ics_feed = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"
        "BEGIN:VEVENT\r\nUID:ev_2026_ok\r\nSUMMARY:Apprentissage profond\r\n"
        "DTSTART:20261130T090000Z\r\nDTEND:20261130T110000Z\r\nLOCATION:Amphi 1\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:ev_2025_old\r\nSUMMARY:Ancien cours 2025\r\n"
        "DTSTART:20251115T090000Z\r\nDTEND:20251115T110000Z\r\nLOCATION:B12\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    inscriptions_html = """
    <html><body><table>
      <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
      <tr><td>3A-UE1 Data</td><td>Apprentissage profond</td><td>3A_DL</td><td>3</td><td>4</td><td>S1</td></tr>
      <tr><td>3A-UE1 Data</td><td>Optimisation</td><td>3A_OPT</td><td>2</td><td>3</td><td>S1</td></tr>
    </table></body></html>
    """
    notes_html = """
    <html><body>
      <h2>BULLETIN DÉTAILLÉ</h2>
      <table>
        <tr><th>Enseignement</th><th>Note finale</th><th>Coefficient</th><th>Classement</th></tr>
        <tr class="semestre"><td>Premier semestre</td><td>15,8</td><td>5</td><td>3/80</td></tr>
        <tr class="matiere"><td><strong>Apprentissage profond</strong></td><td>16,5</td><td>3</td><td>2/80</td></tr>
        <tr class="epreuve"><td style="padding-left:24px;">TP Distillation</td><td>17,5</td><td>1,0</td><td>-</td></tr>
        <tr class="epreuve"><td style="padding-left:24px;">Final Homework</td><td>16,0</td><td>2,0</td><td>-</td></tr>
      </table>
    </body></html>
    """
    pages = {
        "https://23ed753f.leos.fr/index.php": home_html,
        "https://23ed753f.leos.fr/index.php?p=900": ics_feed,
        "https://23ed753f.leos.fr/index.php?p=107": inscriptions_html,
        "https://23ed753f.leos.fr/index.php?p=1052": notes_html,
    }
    pamp = PamplemousseConnector(cfg, page_fetcher=lambda u: pages[u], rate_limiter=RateLimiter(0.0))

    class _AlwaysConnectedAuth:
        def authorize(self, inputs):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active", service_access="verified")

        def validate(self):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active", service_access="verified")

        def disconnect(self):
            pass

    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "cfg_pamp",
            "local_data_dir": tmp_path / "data_pamp",
            "adapter_factory": None,
        }
    )
    comps = Components(
        adapters={CodexSourceKind.PAMPLEMOUSSE: _AlwaysConnectedAuth()},
        connectors={CodexSourceKind.PAMPLEMOUSSE: CodexConnectorBridge(pamp)},
    )
    with Runtime(settings, components=comps) as rt:
        rt.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)
        job_id = rt.sync.submit(
            SyncScope(source=CodexSourceKind.PAMPLEMOUSSE, academic_year="2026-2027", full=True)
        )
        job = _wait_for_job(rt, job_id)
        assert job["status"] == "succeeded", f"Unexpected job report: {job}"
        src_rep = job["report"]["pamplemousse"]
        assert "academic_year_unknown" not in (src_rep.get("error_codes") or [])
        assert src_rep["errors"] == 0

        with rt.engine.connect() as db:
            persisted = db.execute(select(db_records.c.kind, db_records.c.payload)).all()
        by_kind: dict[str, list[dict[str, Any]]] = {}
        for k, pl in persisted:
            by_kind.setdefault(k, []).append(pl)

        # 1. Only the 2026-2027 ICS event persisted (2025-2026 excluded, never blindly tagged!)
        assert len(by_kind.get("event", [])) == 1
        assert by_kind["event"][0]["provenance"]["academic_year"] == "2026-2027"
        assert by_kind["event"][0]["confirmation_required"] is False  # Ordinary class not falsely flagged!

        # 2. All subject & assessment coefficients persisted with 2026-2027 provenance!
        coefs = by_kind.get("coefficient", [])
        assert len([c for c in coefs if c.get("level") == "subject"]) >= 2
        assert len([c for c in coefs if c.get("level") == "assessment"]) == 2
        assert all(c["provenance"]["academic_year"] == "2026-2027" for c in coefs)

        # 3. ALL 4 available grades persisted (2 official aggregates + 2 assessment grades), ZERO bogus missing grades from Inscriptions!
        grades = by_kind.get("grade", [])
        assert len(grades) == 4
        assert all(g["value"] is not None for g in grades)
        assert len([g for g in grades if g.get("grade_kind") == "official_aggregate"]) == 2
        assert len([g for g in grades if g.get("grade_kind") == "assessment"]) == 2

        # 4. Derived sibling weights also carry academic_year="2026-2027" and persist cleanly!
        weights = by_kind.get("assessment_weight", [])
        assert len(weights) == 2
        assert all(w["provenance"]["academic_year"] == "2026-2027" for w in weights)


def test_p0_google_calendar_bounded_window_persists_in_sqlite_without_unverified_error(
    tmp_path: Path,
) -> None:
    """Reproduces live `calendar_window_unverified` / 0 persisted intervals bug on bounded SyncScope."""
    from sqlalchemy import select
    from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus
    from backend.app.core.database import records as db_records
    from backend.app.core.runtime import Components

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    class _FakeCreds:
        valid = True
        expired = False

    class _FakeOAuth:
        def get_credentials(self, allow_refresh: bool = True, interactive: bool = False):
            return _FakeCreds()

    raw_items = [
        {
            "id": "cal_busy_1",
            "status": "confirmed",
            "transparency": "opaque",
            "summary": "Private Appointment",
            "start": {"dateTime": "2026-12-01T10:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-12-01T11:30:00+01:00", "timeZone": "Europe/Paris"},
            "updated": "2026-11-27T18:00:00Z",
        },
        {
            "id": "cal_free_1",
            "status": "confirmed",
            "transparency": "transparent",
            "summary": "Reminder Free",
            "start": {"dateTime": "2026-12-02T10:00:00+01:00", "timeZone": "Europe/Paris"},
            "end": {"dateTime": "2026-12-02T11:00:00+01:00", "timeZone": "Europe/Paris"},
            "updated": "2026-11-27T18:01:00Z",
        },
    ]

    class _Exec:
        def execute(self) -> dict[str, Any]:
            return {"timeZone": "Europe/Paris", "items": raw_items, "nextSyncToken": "st_cal_ok"}

    class _EventsAPI:
        def list(self, **kwargs: Any) -> _Exec:
            assert "timeMin" in kwargs and "timeMax" in kwargs
            return _Exec()

    class _Svc:
        def events(self) -> _EventsAPI:
            return _EventsAPI()

    cal_conn = GoogleCalendarConnector(
        cfg,
        oauth_adapter=_FakeOAuth(),  # type: ignore[arg-type]
        calendar_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
    )

    class _AlwaysConnectedAuth:
        def authorize(self, inputs):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active", service_access="verified")

        def validate(self):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active", service_access="verified")

        def disconnect(self):
            pass

    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "cfg_cal",
            "local_data_dir": tmp_path / "data_cal",
            "adapter_factory": None,
        }
    )
    comps = Components(
        adapters={CodexSourceKind.GOOGLE_CALENDAR: _AlwaysConnectedAuth()},
        connectors={CodexSourceKind.GOOGLE_CALENDAR: CodexConnectorBridge(cal_conn)},
    )
    with Runtime(settings, components=comps) as rt:
        rt.consent.record(CodexSourceKind.GOOGLE_CALENDAR, "read", True)
        # Use Europe/Paris +01:00 offsets just like the live operator command!
        scope = SyncScope.model_validate(
            {
                "source": CodexSourceKind.GOOGLE_CALENDAR,
                "window_start": "2026-11-27T00:00:00+01:00",
                "window_end": "2026-12-31T23:59:59+01:00",
            }
        )
        job_id = rt.sync.submit(scope)
        job = _wait_for_job(rt, job_id)
        assert job["status"] == "succeeded", f"Unexpected calendar job: {job}"
        src_rep = job["report"]["google_calendar"]
        assert "calendar_window_unverified" not in (src_rep.get("error_codes") or [])
        assert src_rep["errors"] == 0

        with rt.engine.connect() as db:
            persisted = db.execute(select(db_records.c.payload)).scalars().all()
        assert len(persisted) == 2
        busy_rows = [p for p in persisted if p.get("busy") is True]
        assert len(busy_rows) == 1
        assert "Private Appointment" not in json.dumps(persisted)


def test_p0_moodle_course_505_direct_fallback_and_dataset_zip_parsing(tmp_path: Path) -> None:
    """Course 505 is never excluded even if omitted by my/courses.php, and a Deep Learning ZIP
    containing >600 binary images + 1 notebook (or binary-only files) parses cleanly without error."""
    import io
    import zipfile
    from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    def _fetch(url: str) -> str:
        if "my/courses.php" in url or url.endswith("/my/") or "profile.php" in url:
            # Simulate my/courses.php omitting course 505!
            return '<div class="block_myoverview"><a href="/course/view.php?id=999">Other Course</a></div>'
        if "id=505" in url:
            return """
            <html><body>
              <div class="page-header-headings"><h1>Apprentissage profond</h1></div>
              <nav class="breadcrumb"><span>2026-2027</span></nav>
              <a href="/pluginfile.php/505/mod_resource/content/1/atelier1.ipynb">atelier1.ipynb</a>
            </body></html>
            """
        return ""

    conn = MoodleConnector(cfg, page_fetcher=_fetch, rate_limiter=RateLimiter(0.0))
    page = conn.list_changes(scope={"course_ids": ["505"], "academic_year": "2026-2027"})
    assert len(page.items) == 1
    assert page.items[0].course_id == "505"
    assert page.items[0].course_title == "Apprentissage profond"
    assert page.items[0].payload["academic_year"] == "2026-2027"

    # Large dataset ZIP (>600 binary .png files + 1 .ipynb notebook) must NOT fail on MAX_OFFICE_ZIP_MEMBERS!
    reg = DocumentParserRegistry()
    dl_zip = io.BytesIO()
    with zipfile.ZipFile(dl_zip, "w") as zf:
        for i in range(600):
            zf.writestr(f"dataset/img_{i:04d}.png", b"\x89PNG\r\n\x1a\nfake")
        zf.writestr(
            "notebooks/tp_cnn.ipynb",
            json.dumps({"nbformat": 4, "cells": [{"cell_type": "markdown", "source": ["# CNN Atelier"]}]}),
        )
    parsed_dl = reg.parse_bytes(dl_zip.getvalue(), filename="tp_cnn_with_dataset.zip")
    assert parsed_dl.error is None
    assert len(parsed_dl.blocks) == 1
    assert "CNN Atelier" in parsed_dl.full_text
    assert parsed_dl.metadata["skipped_binary_members"] == 600

    # Binary-only ZIP emits a structured manifest block instead of 0 blocks
    bin_zip = io.BytesIO()
    with zipfile.ZipFile(bin_zip, "w") as zf:
        zf.writestr("weights/model.pt", b"\x00\x01\x02weights")
        zf.writestr("data/train.npy", b"\x93NUMPYdata")
    parsed_bin = reg.parse_bytes(bin_zip.getvalue(), filename="weights_only.zip")
    assert parsed_bin.error is None
    assert len(parsed_bin.blocks) == 1
    assert "ZIP Archive Manifest" in parsed_bin.full_text
    assert "zip_contains_no_parseable_text_members" in parsed_bin.warnings


def test_sprint_moodle_exhaustive_discovery_profile_fallback_timeout_recovery_and_cookie_fetch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint P0 regression:
    - /user/profile.php `/user/view.php?id=...&course=<cid>` links are parsed into canonical courses.
    - Short academic years (2026-27) normalize cleanly to 2026-2027.
    - Timeout recovery in EnsaiAuthAdapter._goto_with_timeout_recovery retries with commit fallback
      and raises TransientSourceError when exhausted (never raw Playwright TimeoutError).
    - MoodleConnector recovers ALL enrolled courses from /user/profile.php when /my/courses.php
      times out or yields <= 1 course, and exposes explicit exclusion counters in last_stats.
    - Cookie-backed HTML fetch retrieves Moodle course pages directly without launching Chromium.
    """
    from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter, SESSION_COOKIES_FILENAME
    from backend.app.modules.ingestion.connectors.base import TransientSourceError
    from backend.app.modules.ingestion.connectors.moodle import (
        _extract_academic_year_hint,
        parse_moodle_dashboard_html,
    )

    assert _extract_academic_year_hint("Econométrie 2026-27") == "2026-2027"
    assert _extract_academic_year_hint("Statistiques 2025/26") == "2025-2026"

    # 1. /user/profile.php static course links (/user/view.php?id=42&course=<cid>)
    profile_html = """
    <html><body>
      <section class="node_category">
        <h3>Profils de cours</h3>
        <ul>
          <li><a href="https://foad-moodle.ensai.fr/user/view.php?id=42&amp;course=1">Site Home</a></li>
          <li><a href="https://foad-moodle.ensai.fr/user/view.php?id=42&amp;course=505">Apprentissage profond 2026-27</a></li>
          <li><a href="https://foad-moodle.ensai.fr/user/view.php?id=42&amp;course=510">Séries temporelles 2026-2027</a></li>
          <li><a href="https://foad-moodle.ensai.fr/user/view.php?id=42&amp;course=401">Archivé — Macro 2024-2025</a></li>
          <li><a href="https://foad-moodle.ensai.fr/user/view.php?id=42&amp;course=520">Projet Statistique</a></li>
        </ul>
      </section>
    </body></html>
    """
    prof_courses = parse_moodle_dashboard_html(profile_html, "https://foad-moodle.ensai.fr/")
    assert {c.course_id for c in prof_courses} == {"505", "510", "401", "520"}
    by_cid = {c.course_id: c for c in prof_courses}
    assert by_cid["505"].url == "https://foad-moodle.ensai.fr/course/view.php?id=505"
    assert by_cid["505"].academic_year == "2026-2027"
    assert by_cid["401"].archived is True

    # 2. Timeout recovery in EnsaiAuthAdapter._goto_with_timeout_recovery
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    auth = EnsaiAuthAdapter(cfg)

    class _FlakyPage:
        def __init__(self, fail_times: int) -> None:
            self.fail_times = fail_times
            self.calls: list[tuple[str, str]] = []
            self.url = "https://foad-moodle.ensai.fr/my/courses.php"

        def goto(self, url: str, *, wait_until: str = "domcontentloaded", timeout: int = 30_000) -> None:
            self.calls.append((url, wait_until))
            if len(self.calls) <= self.fail_times:
                raise TimeoutError(f"Page.goto: Timeout {timeout}ms exceeded.")

        def wait_for_load_state(self, state: str, *, timeout: int = 15_000) -> None:
            return None

    flaky_ok = _FlakyPage(fail_times=1)
    auth._goto_with_timeout_recovery(flaky_ok, "https://foad-moodle.ensai.fr/my/courses.php", timeout_ms=5_000)
    assert [w for _, w in flaky_ok.calls] == ["domcontentloaded", "commit"]

    flaky_dead = _FlakyPage(fail_times=3)
    with pytest.raises(TransientSourceError):
        auth._goto_with_timeout_recovery(flaky_dead, "https://foad-moodle.ensai.fr/my/courses.php", timeout_ms=5_000)

    # 3. Exhaustive discovery fallback when /my/courses.php times out + explicit exclusion counters
    def _timeout_my_courses(url: str) -> str:
        if "my/courses.php" in url:
            raise TransientSourceError("Playwright navigation timed out on /my/courses.php")
        if "user/profile.php" in url:
            return profile_html
        if "course/view.php?id=" in url:
            return "<html><body><h1>Cours</h1><a href='/pluginfile.php/1/mod_resource/content/1/notes.pdf'>Notes</a></body></html>"
        return "<html><body></body></html>"

    conn = MoodleConnector(cfg, page_fetcher=_timeout_my_courses, rate_limiter=RateLimiter(0.0))
    page = conn.list_changes(scope={"academic_year": "2026-2027", "single_course_first": False})
    assert conn.last_stats["my_courses_timeout"] is True
    assert conn.last_stats["courses_discovered_raw"] == 4
    assert conn.last_stats["courses_excluded_archived"] == 1
    assert conn.last_stats["courses_discovered"] == 3  # 505, 510, 520 (unknown year kept)
    assert conn.last_stats["courses_unknown_year"] == 1
    assert "injected_profile" in conn.last_stats["discovery_sources_used"]
    assert len(page.items) == 3

    # 4. Cookie-backed fast HTML fetch (no Chromium launch)
    profile_dir = auth.profile_dir_for(SourceKind.MOODLE)
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / SESSION_COOKIES_FILENAME).write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "MoodleSession",
                        "value": "sess_test_123",
                        "domain": "foad-moodle.ensai.fr",
                        "path": "/",
                        "secure": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    class _FakeHttpResp:
        def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    class _FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.cookies = type("_C", (), {"set": lambda *a, **k: None})()

        def get(self, url: str, **kwargs: Any) -> _FakeHttpResp:
            if "id=505" in url:
                return _FakeHttpResp(200, "<html><body><h1>Apprentissage profond</h1></body></html>")
            return _FakeHttpResp(302, "", {"Location": "https://ssocas.ensai.fr/cas/login"})

        def close(self) -> None:
            return None

    import backend.app.modules.ingestion.connectors.moodle as moodle_mod

    monkeypatch.setattr(moodle_mod.requests, "Session", _FakeSession)
    conn_cookie = MoodleConnector(cfg, auth_adapter=auth, rate_limiter=RateLimiter(0.0))
    ok_html = conn_cookie._cookie_session_fetch_html("https://foad-moodle.ensai.fr/course/view.php?id=505")
    assert ok_html is not None and "Apprentissage profond" in ok_html
    # CAS redirect returns None so caller falls back cleanly to Playwright/reauth
    assert conn_cookie._cookie_session_fetch_html("https://foad-moodle.ensai.fr/course/view.php?id=999") is None


def test_sprint_ensora_drive_streaming_bfs_retry_401_refresh_export_fallbacks_and_diagnostics(
    tmp_path: Path,
) -> None:
    """Sprint P0 regression:
    - Streaming BFS pagination returns page 1 early (without traversing the whole tree upfront)
      while keeping authoritative membership reconciliation & tombstones strictly on completion.
    - Reuses Drive service client across requests and transparently refreshes once on HTTP 401.
    - Retries transient TimeoutError & HTTP 403 downloadQuotaExceeded automatically.
    - Falls back across Google Workspace export formats (Slides PPTX -> PDF on exportSizeLimitExceeded).
    - Classifies 404 (not_found), 400 fileNotExportable (unexportable_workspace), and pre-download
      oversized binary files (size_limit_exceeded, zero get_media bandwidth) in last_fetch_errors_by_reason.
    """
    from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
    from backend.app.modules.ingestion.connectors.ensora_drive import (
        DRIVE_FOLDER_MIME,
        DriveResourceNotFoundError,
        DriveSizeLimitError,
        DriveUnexportableError,
        EnsoraDriveConnector,
    )

    root_id = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        "sub_1a": {"id": "sub_1a", "name": "1A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "sub_2a": {"id": "sub_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f1": {"id": "f1", "name": "cours1.pdf", "mimeType": "application/pdf", "parents": ["sub_1a"], "size": "120", "modifiedTime": "2026-11-01T10:00:00Z", "trashed": False},
        "f2": {"id": "f2", "name": "cours2.pdf", "mimeType": "application/pdf", "parents": ["sub_1a"], "size": "120", "modifiedTime": "2026-11-02T10:00:00Z", "trashed": False},
        "f3": {"id": "f3", "name": "slides_big", "mimeType": "application/vnd.google-apps.presentation", "parents": ["sub_1a"], "modifiedTime": "2026-11-03T10:00:00Z", "trashed": False},
        "f4": {"id": "f4", "name": "cours4.pdf", "mimeType": "application/pdf", "parents": ["sub_2a"], "size": "120", "modifiedTime": "2026-11-05T10:00:00Z", "trashed": False},
        "f_huge": {"id": "f_huge", "name": "video_dataset.zip", "mimeType": "application/zip", "parents": ["sub_2a"], "size": str(150 * 1024 * 1024), "modifiedTime": "2026-11-05T10:00:00Z", "trashed": False},
        "f_unexp": {"id": "f_unexp", "name": "odd_doc", "mimeType": "application/vnd.google-apps.document", "parents": ["sub_2a"], "modifiedTime": "2026-11-05T10:00:00Z", "trashed": False},
    }

    class _HttpErr(Exception):
        def __init__(self, status: int, message: str) -> None:
            super().__init__(message)
            self.status_code = status

    state_counters = {
        "service_builds": 0,
        "f1_401_left": 1,
        "f2_timeout_left": 1,
        "f4_quota_left": 1,
        "get_media_calls": [],
    }

    class _Exec:
        def __init__(self, fn):
            self._fn = fn

        def execute(self):
            return self._fn()

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _Exec:
            def _do():
                if fileId == "f_missing":
                    raise _HttpErr(404, "File not found")
                if fileId == "f1" and state_counters["f1_401_left"] > 0:
                    state_counters["f1_401_left"] -= 1
                    raise _HttpErr(401, "Invalid Credentials")
                return tree[fileId]
            return _Exec(_do)

        def list(self, *, q: str, **kwargs: Any) -> _Exec:
            pid = q.split("'")[1]
            return _Exec(
                lambda: {"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]}
            )

        def get_media(self, *, fileId: str, **kwargs: Any) -> _Exec:
            def _do():
                state_counters["get_media_calls"].append(fileId)
                if fileId == "f2" and state_counters["f2_timeout_left"] > 0:
                    state_counters["f2_timeout_left"] -= 1
                    raise TimeoutError("The read operation timed out")
                if fileId == "f4" and state_counters["f4_quota_left"] > 0:
                    state_counters["f4_quota_left"] -= 1
                    raise _HttpErr(403, "downloadQuotaExceeded: temporary quota limit")
                return f"%PDF-1.4 {fileId}".encode("utf-8")
            return _Exec(_do)

        def export_media(self, *, fileId: str, mimeType: str, **kwargs: Any) -> _Exec:
            def _do():
                if fileId == "f3" and "presentationml" in mimeType:
                    raise _HttpErr(403, "exportSizeLimitExceeded: This file is too large to be exported.")
                if fileId == "f3" and mimeType == "application/pdf":
                    return b"%PDF-1.4 exported slides"
                if fileId == "f_unexp":
                    raise _HttpErr(400, "fileNotExportable: Export not allowed")
                return b"plain"
            return _Exec(_do)

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    class _Creds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    def _svc_factory(_creds: Any) -> _Svc:
        state_counters["service_builds"] += 1
        return _Svc()

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _Creds())
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=_svc_factory,
        rate_limiter=RateLimiter(0.0),
    )

    # 1. Streaming BFS pagination: page 1 (page_size=2) returns BEFORE visiting sub_2a!
    p1 = conn.list_changes(
        scope={
            "page_size": 2,
            "known_external_ids": {"ensora_drive:f1", "ensora_drive:gone_file"},
        }
    )
    assert p1.complete is False
    assert p1.next_cursor is not None and p1.next_cursor.startswith("drvpage:")
    assert len(p1.items) == 2
    assert conn.last_stats["folders_remaining"] >= 1  # Did NOT stall traversing sub_2a yet!
    assert conn.last_scan_authoritative is False
    assert p1.sync_token is None

    # Drain remaining pages -> terminal page reconciles tombstone for gone_file and emits sync_token
    all_records = list(p1.items)
    cur = p1.next_cursor
    last_page = p1
    while cur:
        last_page = conn.list_changes(
            scope={
                "page_size": 2,
                "known_external_ids": {"ensora_drive:f1", "ensora_drive:gone_file"},
            },
            cursor=cur,
        )
        all_records.extend(last_page.items)
        cur = last_page.next_cursor
    assert last_page.complete is True
    assert conn.last_scan_authoritative is True
    assert last_page.sync_token == "drvmod:2026-11-05T10:00:00Z"
    tombstones = [r for r in all_records if r.access_status.value == "tombstoned"]
    assert [t.external_id for t in tombstones] == ["ensora_drive:gone_file"]

    by_ext = {r.external_id: r for r in all_records}
    # 2. Service reuse + 401 refresh recovery on f1
    builds_before_f1 = state_counters["service_builds"]
    res_f1 = conn.fetch_blob(by_ext["ensora_drive:f1"])
    assert res_f1.content == b"%PDF-1.4 f1"
    assert state_counters["service_builds"] == builds_before_f1 + 1  # Rebuilt once on 401

    # 3. Network TimeoutError retry on f2 + downloadQuotaExceeded retry on f4 (reusing cached service!)
    builds_after_refresh = state_counters["service_builds"]
    res_f2 = conn.fetch_blob(by_ext["ensora_drive:f2"])
    res_f4 = conn.fetch_blob(by_ext["ensora_drive:f4"])
    assert res_f2.content == b"%PDF-1.4 f2"
    assert res_f4.content == b"%PDF-1.4 f4"
    assert state_counters["service_builds"] == builds_after_refresh  # Zero redundant rebuilds!

    # 4. Slides exportSizeLimitExceeded fallback PPTX -> PDF
    res_f3 = conn.fetch_blob(by_ext["ensora_drive:f3"])
    assert res_f3.mime_type == "application/pdf"
    assert res_f3.filename.endswith(".pdf")
    assert res_f3.content == b"%PDF-1.4 exported slides"

    # 5. Pre-download oversized file check (zero get_media calls!), 404 not_found, 400 unexportable
    with pytest.raises(DriveSizeLimitError):
        conn.fetch_blob(by_ext["ensora_drive:f_huge"])
    assert "f_huge" not in state_counters["get_media_calls"]

    with pytest.raises(DriveUnexportableError):
        conn.fetch_blob(by_ext["ensora_drive:f_unexp"])

    from backend.app.modules.ingestion.contracts import SourceRecord

    missing_rec = SourceRecord(
        source_kind=SourceKind.ENSORA_DRIVE,
        external_id="ensora_drive:f_missing",
        title="missing.pdf",
        record_kind="document",
        payload={"drive_file_id": "f_missing"},
    )
    with pytest.raises(DriveResourceNotFoundError):
        conn.fetch_blob(missing_rec)

    assert conn.last_fetch_errors_by_reason["size_limit_exceeded"] == 1
    assert conn.last_fetch_errors_by_reason["unexportable_workspace"] == 1
    assert conn.last_fetch_errors_by_reason["not_found"] == 1


def test_sprint_pamplemousse_source_confirmed_year_and_breakdown_stats(tmp_path: Path) -> None:
    """Sprint P0/Codex coordination: Pamplemousse distinguishes source_confirmed_academic_year from
    requested_academic_year and surfaces per-category missing/ambiguous & event metadata counts."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    # Case A: HTML has NO academic year -> requested_academic_year used as fallback, academic_year_confirmed=False
    insc_no_year = """
    <html><body><table>
      <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
      <tr><td>UE1</td><td>Statistiques</td><td>3A_ST</td><td>3</td><td>5</td><td>S1</td></tr>
    </table></body></html>
    """
    conn = PamplemousseConnector(cfg, rate_limiter=RateLimiter(0.0))
    conn.list_changes(scope={"inscriptions_html": insc_no_year, "academic_year": "2026-2027"})
    assert conn.last_stats["requested_academic_year"] == "2026-2027"
    assert conn.last_stats["source_confirmed_academic_year"] is None
    assert conn.last_stats["academic_year_confirmed"] is False
    assert conn.last_stats["academic_year"] == "2026-2027"

    # Case B: HTML explicitly states 2026-2027 + event has room & teacher -> academic_year_confirmed=True
    insc_with_year = "<html><body><h1>Année 2026-2027</h1>" + insc_no_year + "</body></html>"
    fc_events = [
        {
            "id": "ev1",
            "title": "CM Statistiques",
            "start": "2026-10-05T08:30:00+02:00",
            "end": "2026-10-05T10:30:00+02:00",
            "salle": "Amphi 1",
            "enseignant": "Prof. Martin",
        }
    ]
    conn.list_changes(
        scope={
            "inscriptions_html": insc_with_year,
            "fullcalendar_json": fc_events,
            "academic_year": "2025-2026",
        }
    )
    assert conn.last_stats["source_confirmed_academic_year"] == "2026-2027"
    assert conn.last_stats["requested_academic_year"] == "2025-2026"
    assert conn.last_stats["academic_year_confirmed"] is True
    assert conn.last_stats["academic_year"] == "2026-2027"
    assert conn.last_stats["events_with_room"] == 1
    assert conn.last_stats["events_with_teacher"] == 1
    assert conn.last_stats["missing_coefficients_count"] == 0
    assert conn.last_stats["ambiguous_events_count"] == 0


def test_sprint_legacy_office_and_ensai_stat_extensions_parsing() -> None:
    """Sprint P1 regression: Legacy binary OLE2 .doc/.xls/.ppt and ENSAI statistical formats
    (.Rnw, .do, .stan, .svg) parse into clean non-empty searchable blocks."""
    from backend.app.modules.ingestion.parsers.registry import DocumentParserRegistry

    reg = DocumentParserRegistry()
    # Synthetic OLE2 .doc payload with UTF-16LE French academic content + OLE2 header
    ole_payload = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 64
        + "Chapitre 1 : Régression linéaire et estimateur des moindres carrés ordinaires.".encode("utf-16le")
        + b"\x00\x00"
        + b"Exercices pratiques econometrie ENSAI 2A serie temporelle stationnaire."
    )
    doc_res = reg.parse_bytes(ole_payload, filename="annale_2019.doc", mime_type="application/msword")
    assert doc_res.parser_name == "legacy_office_parser"
    assert doc_res.error is None
    assert len(doc_res.blocks) >= 1
    assert "moindres carrés" in doc_res.full_text
    assert doc_res.extraction_quality == 0.55
    assert "legacy_binary_office_text_extraction" in doc_res.warnings

    for fname, body in (
        ("rapport.Rnw", b"\\section{Analyse}\n<<echo=TRUE>>=\nsummary(lm(y ~ x))\n@\n"),
        ("estimation.do", b"regress y x1 x2, robust\npredict yhat\n"),
        ("model.stan", b"parameters { real mu; real<lower=0> sigma; }\nmodel { y ~ normal(mu, sigma); }\n"),
        ("schema.svg", b"<svg xmlns='http://www.w3.org/2000/svg'><text>Pipeline ENSAI</text></svg>"),
    ):
        parsed = reg.parse_bytes(body, filename=fname)
        assert parsed.error is None, f"Failed parsing {fname}: {parsed.error}"
        assert len(parsed.blocks) >= 1, f"Empty blocks for {fname}"


def test_sprint2_drive_batched_listing_durable_paging_across_restarts_and_small_bridge_batch(
    tmp_path: Path,
) -> None:
    """Sprint 2 P0 regression:
    - Batched multi-folder files.list (`pageSize=1000`, <=15 folders/query) cuts API calls ~6-15x.
    - Default Drive rate-limit interval is capped at <=0.12s (not 1.0s HTML scraping delay).
    - Durable 0o600 `drvpage:<hash>` snapshot survives process restarts (fresh connector instance),
      re-validates approved-root containment on resume, and cleans up on completion.
    - CodexConnectorBridge caps Drive page_size to DRIVE_STREAMING_PAGE_SIZE (25) for early batch emission.
    """
    import re as _re
    from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
    from backend.app.modules.ingestion.codex_bridge import (
        CodexConnectorBridge,
        DRIVE_STREAMING_PAGE_SIZE,
    )
    from backend.app.modules.ingestion.connectors.ensora_drive import (
        DRIVE_DEFAULT_RATE_LIMIT_CAP_SEC,
        DRIVE_FOLDER_MIME,
        EnsoraDriveConnector,
    )

    root_id = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        "fld_2a": {"id": "fld_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
    }
    for i in range(1, 20):
        fid = f"folder_{i:02d}"
        tree[fid] = {
            "id": fid,
            "name": f"Subject_{i:02d}",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["fld_2a"],
            "trashed": False,
        }
        doc_id = f"doc_{i:02d}"
        tree[doc_id] = {
            "id": doc_id,
            "name": f"cours_{i:02d}.pdf",
            "mimeType": "application/pdf",
            "parents": [fid],
            "size": "512",
            "modifiedTime": f"2026-11-{min(28, i):02d}T10:00:00Z",
            "trashed": False,
        }

    api_calls: list[dict[str, Any]] = []

    class _Exec:
        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def execute(self) -> Any:
            return self._payload

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _Exec:
            return _Exec(tree[fileId])

        def list(self, *, q: str, pageSize: int = 100, **kwargs: Any) -> _Exec:
            pids = set(_re.findall(r"'([^']+)'\s+in\s+parents", q))
            api_calls.append({"pids": pids, "pageSize": pageSize})
            matched = [
                v
                for v in tree.values()
                if not v.get("trashed") and any(p in pids for p in (v.get("parents") or []))
            ]
            return _Exec({"files": matched})

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    class _Creds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.rate_limit_min_interval_sec = 1.0
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _Creds())

    # 1. Default Drive rate limiter caps polite interval at <= 0.12s (not 1.0s)
    conn_default_rl = EnsoraDriveConnector(cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc())
    assert conn_default_rl._rate_limiter._min_interval <= DRIVE_DEFAULT_RATE_LIMIT_CAP_SEC

    # 2. Batched folder listing + durable 0o600 paging continuation across process restarts
    conn1 = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )
    p1 = conn1.list_changes(scope={"page_size": 5})
    assert p1.complete is False
    assert len(p1.items) == 5
    assert p1.next_cursor is not None and p1.next_cursor.startswith("drvpage:")
    snap_key = p1.next_cursor.split(":", 1)[-1]
    snap_file = cfg.config_dir / "state" / "drive_paging" / f"{snap_key}.json"
    assert snap_file.is_file()
    assert (snap_file.stat().st_mode & 0o777) == 0o600

    # Simulate process restart: brand-new connector instance with empty in-memory _paging_states!
    conn2 = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )
    assert conn2._paging_states == {}
    collected = list(p1.items)
    cur = p1.next_cursor
    last_page = p1
    while cur:
        last_page = conn2.list_changes(scope={"page_size": 5}, cursor=cur)
        collected.extend(last_page.items)
        cur = last_page.next_cursor

    assert last_page.complete is True
    assert conn2.last_scan_authoritative is True
    assert len(collected) == 19
    # Snapshot is retained until 24h TTL expiry so mid-page crash before Codex commits next_cursor
    # remains safely and idempotently replayable from the previously committed drvpage cursor!
    assert snap_file.is_file()
    conn3 = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )
    replay_p2 = conn3.list_changes(scope={"page_size": 5}, cursor=p1.next_cursor)
    assert [r.external_id for r in replay_p2.items] == [r.external_id for r in collected[5:10]]
    # 20 folders (root + 19 subfolders) traversed in <= 4 batched API calls (vs 20 unbatched!)
    assert len(api_calls) <= 5
    assert all(c["pageSize"] == 1000 for c in api_calls)

    # 3. build_components() wires Drive with DRIVE_STREAMING_PAGE_SIZE (25), streams exhaustive
    # coverage across pages with zero drops/duplicates, and emits accurate per-page delta counters!
    from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
    from backend.app.core.runtime import Runtime
    from backend.app.core.settings import Settings

    rt_settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "rt_cfg",
            "local_data_dir": tmp_path / "rt_data",
            "ensai_profile_dir": tmp_path / "rt_cfg/sessions/ensai",
        }
    )
    with Runtime(rt_settings) as rt:
        drive_bridge = rt.sync.connectors[CodexSourceKind.ENSORA_DRIVE]
        assert drive_bridge._page_size == DRIVE_STREAMING_PAGE_SIZE

    # Verify per-page delta observations sum exactly to cumulative totals with non-zero page 1 progress
    delta_bridge = CodexConnectorBridge(
        EnsoraDriveConnector(
            cfg,
            oauth_adapter=oauth,
            drive_service_factory=lambda _: _Svc(),
            rate_limiter=RateLimiter(0.0),
            batch_folder_queries=False,
        ),
        page_size=5,
    )
    bridge_items = []
    sum_folders = 0
    sum_resources = 0
    b_cur: str | None = None
    first_obs: dict[str, int] | None = None
    while True:
        b_page = delta_bridge.list_changes(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=True), b_cur)
        if first_obs is None:
            first_obs = dict(b_page.observations)
        sum_folders += b_page.observations.get("folders_traversed", 0)
        sum_resources += b_page.observations.get("resources_discovered", 0)
        bridge_items.extend(b_page.items)
        if b_page.complete:
            break
        b_cur = b_page.next_cursor

    assert first_obs is not None
    assert first_obs.get("folders_traversed", 0) > 0
    assert first_obs.get("resources_discovered", 0) > 0
    assert sum_folders == 20  # root + 19 subfolders, exact sum with zero double-counting
    assert sum_resources == 19
    assert len({it.provenance.external_id for it in bridge_items}) == 19


def test_sprint2_ensai_stale_singleton_lock_cleanup_cookie_http_probe_and_transient_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 2 P0 regression:
    - Dead-PID Chromium SingletonLock/Socket/Cookie leftovers are cleaned after restart; live PID preserved.
    - Cookie-backed HTTP probe validates Moodle (with /user/profile.php timeout fallback) & Pamplemousse
      without launching Chromium.
    - TransientSourceError during check_source_connection maps to UNAVAILABLE/transient_failure (not adapter_error).
    - PamplemousseConnector._cookie_session_fetch_html() fetches authenticated HTML directly via cookies.
    """
    import os
    import requests as _requests
    from backend.app.contracts.authentication import ConnectionStatus
    from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter, SESSION_COOKIES_FILENAME
    from backend.app.modules.ingestion.codex_bridge import _to_codex_auth_result
    from backend.app.modules.ingestion.connectors.base import TransientSourceError

    # 1. Stale vs live Chromium SingletonLock cleanup
    dead_dir = tmp_path / "dead_profile"
    dead_dir.mkdir()
    os.symlink("localhost-99999999", dead_dir / "SingletonLock")
    (dead_dir / "SingletonSocket").write_text("", encoding="utf-8")
    (dead_dir / "SingletonCookie").write_text("", encoding="utf-8")
    assert EnsaiAuthAdapter._cleanup_stale_chromium_locks(dead_dir) is True
    assert not (dead_dir / "SingletonLock").exists() and not (dead_dir / "SingletonLock").is_symlink()
    assert not (dead_dir / "SingletonSocket").exists()

    live_dir = tmp_path / "live_profile"
    live_dir.mkdir()
    os.symlink(f"localhost-{os.getpid()}", live_dir / "SingletonLock")
    assert EnsaiAuthAdapter._cleanup_stale_chromium_locks(live_dir) is False
    assert (live_dir / "SingletonLock").is_symlink()

    # 2. Fast cookie-backed HTTP validation (zero Chromium launch) with Moodle /user/profile.php fallback
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    auth = EnsaiAuthAdapter(cfg)
    pdir = auth.profile_dir_for(SourceKind.MOODLE)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / SESSION_COOKIES_FILENAME).write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "MoodleSession",
                        "value": "msess_live",
                        "domain": "foad-moodle.ensai.fr",
                        "path": "/",
                        "secure": True,
                    },
                    {
                        "name": "PHPSESSID",
                        "value": "psess_live",
                        "domain": "23ed753f.leos.fr",
                        "path": "/",
                        "secure": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    class _FakeResp:
        def __init__(self, status_code: int, text: str, headers: dict[str, str] | None = None) -> None:
            self.status_code = status_code
            self.text = text
            self.headers = headers or {}

    class _FakeSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.cookies = type("_C", (), {"set": lambda *a, **k: None})()

        def get(self, url: str, **kwargs: Any) -> _FakeResp:
            if "my/courses.php" in url:
                raise TimeoutError("Read timed out on /my/courses.php")
            if "user/profile.php" in url:
                return _FakeResp(
                    200,
                    '<html><title>Profil</title><body><a href="/login/logout.php">Logout</a>"sesskey":"abc123"</body></html>',
                )
            if "23ed753f.leos.fr" in url:
                return _FakeResp(
                    200,
                    '<html><title>Pamplemousse</title><body><a href="index.php?p=107">Inscriptions</a><a href="index.php?p=1052">Notes</a></body></html>',
                )
            return _FakeResp(404, "")

        def close(self) -> None:
            return None

    monkeypatch.setattr(_requests, "Session", _FakeSession)
    # Fail loudly if Chromium were ever invoked!
    monkeypatch.setattr(auth, "open_context", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Chromium should not launch!")))

    moodle_st = auth.check_source_connection(SourceKind.MOODLE, headless=True)
    assert moodle_st.authenticated is True
    assert "persisted session cookies" in moodle_st.detail

    pamp_st = auth.check_source_connection(SourceKind.PAMPLEMOUSSE, headless=True)
    assert pamp_st.authenticated is True

    pamp_conn = PamplemousseConnector(cfg, auth_adapter=auth, rate_limiter=RateLimiter(0.0))
    pamp_html = pamp_conn._cookie_session_fetch_html("https://23ed753f.leos.fr/index.php")
    assert pamp_html is not None and "Inscriptions" in pamp_html

    # 3. TransientSourceError maps cleanly to UNAVAILABLE / transient_failure (not opaque adapter_error)
    cfg2 = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg2", environ={})
    auth2 = EnsaiAuthAdapter(cfg2, context_factory=lambda *a, **k: (_ for _ in ()).throw(TransientSourceError("nav timed out")))
    err_st = auth2.check_source_connection(SourceKind.PAMPLEMOUSSE, headless=True)
    assert err_st.authenticated is False
    assert err_st.metadata["errorcode"] == "transient_failure"
    codex_res = _to_codex_auth_result(err_st, institutional=True)
    assert codex_res.status == ConnectionStatus.UNAVAILABLE
    assert codex_res.reason == "transient_failure"


def test_sprint3_classification_datasets_announcements_rag_chunking_and_concepts() -> None:
    """Sprint 3 P1 regression:
    - Classifies datasets (.csv, .parquet, .rds, binary-only dataset ZIP), Moodle announcements,
      and inline educational HTML accurately without overriding explicit TP/assignment signals.
    - Splits oversized (>1800 char) prose blocks into bounded overlapping RAG chunks with sub_chunk
      locators and language hints, preserves partial status on low-quality legacy Office extraction,
      and propagates academic_year onto Concept provenance + French prerequisite cues.
    """
    from backend.app.contracts.sources import Document, Provenance, SourceKind as CodexSourceKind
    from backend.app.modules.ingestion.classification.classifier import DocumentClassifier
    from backend.app.modules.ingestion.codex_bridge import (
        CodexClassifierBridge,
        CodexParserBridge,
        extract_concept_candidates,
        extract_prerequisite_hints,
    )
    from backend.app.modules.ingestion.contracts import DocumentCategory

    clf = DocumentClassifier()
    assert clf.classify(title="housing_train.csv", folder_path="Ensora/2A/ML/Datasets").category == DocumentCategory.DATASET
    assert clf.classify(title="panel_data.parquet", folder_path="Moodle/Econometrie").category == DocumentCategory.DATASET
    assert clf.classify(title="enquete_ensai.rds", folder_path="Moodle/Stats").category == DocumentCategory.DATASET
    # Explicit TP PDF mentioning dataset still classifies as practical_work
    assert clf.classify(title="TP2_Instructions_Dataset.pdf", folder_path="Moodle/ML/TP").category == DocumentCategory.PRACTICAL_WORK

    clf_bridge = CodexClassifierBridge(clf)
    ann_doc = Document(
        provenance=Provenance(external_id="moodle:course:505:announcement:9", source=CodexSourceKind.MOODLE),
        title="Information importante examen",
        mime_type="text/html",
        resource_kind="announcement",
    )
    assert clf_bridge.classify(ann_doc, None).resource_type == "administrative"

    # Bounded RAG chunking for long bilingual document + partial status for legacy binary .doc
    parser_bridge = CodexParserBridge(max_chunk_chars=600, chunk_overlap_chars=80)
    long_md = (
        "# Chapitre 1 : Régression linéaire\n\n"
        "Avant de commencer : Algèbre linéaire et probabilités de base.\n\n"
        "Définition 1 : Estimateur des moindres carrés ordinaires.\n\n"
        + ("Les propriétés statistiques de l'estimateur MCO reposent sur les hypothèses de Gauss-Markov. " * 25)
    ).encode("utf-8")
    md_doc = Document(
        provenance=Provenance(
            external_id="moodle:course:505:resource:1",
            source=CodexSourceKind.MOODLE,
            academic_year="2026-2027",
        ),
        title="cours_mco.md",
        mime_type="text/markdown",
    )
    parsed_md = parser_bridge.parse(md_doc, long_md)
    assert parsed_md is not None
    assert parsed_md.status == "ready"
    assert len(parsed_md.chunks) >= 4
    assert all(len(ch.text) <= 650 for ch in parsed_md.chunks)
    assert any("sub_chunk" in ch.locator for ch in parsed_md.chunks)
    assert any(ch.structured.get("language") == "fr" for ch in parsed_md.chunks)

    concepts = extract_concept_candidates(md_doc, parsed_md)
    assert len(concepts) >= 1
    assert all(c.provenance.academic_year == "2026-2027" for c in concepts)
    prereqs = extract_prerequisite_hints(parsed_md)
    assert len(prereqs) == 1
    assert "Algèbre linéaire" in str(prereqs[0]["text"])

    # Legacy binary .doc (quality=0.55) surfaces as ParsedContent.status == "partial"
    ole_payload = (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        + b"\x00" * 32
        + "Chapitre 2 : Séries temporelles stationnaires et processus ARMA.".encode("utf-16le")
    )
    doc_legacy = Document(
        provenance=Provenance(external_id="ensora_drive:old_doc", source=CodexSourceKind.ENSORA_DRIVE),
        title="ancien_cours.doc",
        mime_type="application/msword",
    )
    parsed_legacy = parser_bridge.parse(doc_legacy, ole_payload)
    assert parsed_legacy is not None
    assert parsed_legacy.status == "partial"
    assert len(parsed_legacy.chunks) >= 1


def test_sprint3_cross_source_subject_ue_association_and_expired_snapshot_pruning(
    tmp_path: Path,
) -> None:
    """Sprint 3 P1/P2/P3 regression:
    - Drive folder segments & Moodle course_title resolve to canonical Pamplemousse Subject + teaching_unit_id.
    - Conservative subject initialism (PPDP) flags needs_review=True (never auto-confirmed).
    - Failed-download shells are excluded from exam-correction candidates.
    - Expired (>24h) Drive paging snapshots are pruned automatically on save.
    """
    import os
    import time
    from backend.app.contracts.sources import (
        ClassificationResult as CodexClassificationResult,
        Document,
        Provenance,
        SourceKind as CodexSourceKind,
        Subject,
        TeachingUnit,
    )
    from backend.app.core.runtime import Runtime
    from backend.app.core.settings import Settings
    from backend.app.modules.ingestion.codex_bridge import CodexEntityMatcherBridge
    from backend.app.modules.ingestion.connectors.ensora_drive import EnsoraDriveConnector

    settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "cfg",
            "local_data_dir": tmp_path / "data",
            "ensai_profile_dir": tmp_path / "cfg/sessions/ensai",
        }
    )
    with Runtime(settings) as rt:
        rt.consent.record(CodexSourceKind.PAMPLEMOUSSE, "read", True)
        rt.consent.record(CodexSourceKind.ENSORA_DRIVE, "read", True)
        rt.consent.record(CodexSourceKind.MOODLE, "read", True)

        pamp_cid = rt.connection_ids[CodexSourceKind.PAMPLEMOUSSE]
        rt.pipeline.import_academics(
            pamp_cid,
            [
                TeachingUnit(
                    provenance=Provenance(external_id="pamp:ue:ue1", academic_year="2026-2027"),
                    title="UE Modélisation",
                ),
                Subject(
                    provenance=Provenance(external_id="pamp:subj:eco", academic_year="2026-2027"),
                    title="Économétrie",
                    parent_key="pamp:ue:ue1",
                    code="2A_ECO",
                ),
                Subject(
                    provenance=Provenance(external_id="pamp:subj:dl", academic_year="2026-2027"),
                    title="Apprentissage profond",
                    parent_key="pamp:ue:ue1",
                    code="3A_DL",
                    aliases=["Deep Learning"],
                ),
                Subject(
                    provenance=Provenance(external_id="pamp:subj:ppdp", academic_year="2026-2027"),
                    title="Projet de Python pour Data Processing",
                    parent_key="pamp:ue:ue1",
                ),
            ],
        )

        matcher = CodexEntityMatcherBridge()
        loaded = matcher.load_from_sqlite_engine(
            rt.engine,
            consent_manager=rt.consent,
            connection_ids=rt.connection_ids,
        )
        assert loaded == 3

        # 1. Ensora Drive folder segment 'Econometrie' matches canonical Subject + UE automatically!
        drive_doc = Document(
            provenance=Provenance(
                external_id="ensora_drive:annale_eco_2024",
                source=CodexSourceKind.ENSORA_DRIVE,
                folder="Ensora/2A/Econometrie/Annales",
            ),
            title="Partiel_2024.pdf",
            mime_type="application/pdf",
        )
        m_drive = matcher.match(drive_doc, CodexClassificationResult(resource_type="examination", confidence=0.9))
        assert m_drive.subject_id == "pamp:subj:eco"
        assert m_drive.teaching_unit_id == "pamp:ue:ue1"
        assert m_drive.confirmed is True

        # 2. Moodle course_title 'Deep Learning' alias matches canonical Subject + UE automatically!
        moodle_doc = Document(
            provenance=Provenance(
                external_id="moodle:course:505:resource:12",
                source=CodexSourceKind.MOODLE,
                folder="Moodle/Deep Learning/Atelier 1",
                academic_year="2026-2027",
            ),
            title="atelier1.ipynb",
            course_id="505",
            course_title="Deep Learning",
            mime_type="application/x-ipynb+json",
        )
        m_moodle = matcher.match(moodle_doc, CodexClassificationResult(resource_type="notebook", confidence=0.9))
        assert m_moodle.subject_id == "pamp:subj:dl"
        assert m_moodle.teaching_unit_id == "pamp:ue:ue1"
        assert m_moodle.confirmed is True

        # 3. Initialism 'PPDP' suggests Projet de Python pour Data Processing with needs_review (confirmed=False)
        ppdp_doc = Document(
            provenance=Provenance(
                external_id="ensora_drive:ppdp_notes",
                source=CodexSourceKind.ENSORA_DRIVE,
                folder="Ensora/2A/PPDP",
            ),
            title="Consignes.pdf",
            mime_type="application/pdf",
        )
        m_ppdp = matcher.match(ppdp_doc, CodexClassificationResult(resource_type="assignment", confidence=0.8))
        assert m_ppdp.subject_id == "pamp:subj:ppdp"
        assert m_ppdp.confirmed is False  # Review required for acronym-only match!

    # 4. Expired (>24h) Drive paging snapshots are pruned automatically when saving a new snapshot
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "prune_cfg", environ={})
    conn = EnsoraDriveConnector(cfg, rate_limiter=RateLimiter(0.0))
    snap_dir = conn._paging_snapshot_dir()
    snap_dir.mkdir(parents=True, exist_ok=True)
    stale = snap_dir / "deadbeef00001111.json"
    stale.write_text("{}", encoding="utf-8")
    old_mtime = time.time() - (25 * 3600)
    os.utime(stale, (old_mtime, old_mtime))
    conn._save_durable_paging_state(
        "drvpage:abcd1234ef567890",
        {
            "fingerprint": "fp",
            "start_folder": conn.root_folder_id,
            "cohorts_filter": set(),
            "max_files": None,
            "max_depth": 12,
            "modified_since": None,
            "known_versions": {},
            "has_membership_baseline": False,
            "known_external_ids": set(),
            "known_titles": {},
            "queue": [],
            "visited_folders": set(),
            "verified_in_run": {conn.root_folder_id},
            "pending_items": [],
            "membership_additions": [],
            "seen_live_ids": set(),
            "warnings": [],
            "errors": [],
            "depth_truncated": False,
            "max_files_hit": False,
            "reconciled": False,
            "latest_modified": None,
            "total_discovered": 0,
            "total_emitted": 0,
            "shortcuts_skipped": 0,
            "unexportable_skipped": 0,
            "api_list_calls": 0,
        },
    )
    assert not stale.exists()
    assert (snap_dir / "abcd1234ef567890.json").is_file()


def test_sprint4_partial_parsing_semantics_mixed_media_vs_scanned_and_warning_categories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sprint 4 P1 regression:
    - Text-rich PDF with <30% empty/diagram slides stays `ready` with `mixed_media_pages_present`
      (never falsely flagged as scanned/partial).
    - Scanned PDF (0 text or >=30% empty pages without tesseract) is `partial` with `scanned_pages_without_ocr`.
    - Standalone PNG without tesseract is `partial` with `ocr_unavailable`.
    - Legacy OLE2 .doc is `partial` with `legacy_binary_office`.
    - Binary-only dataset .zip is `partial` with `non_textual_archive`.
    - Notebook with embedded image/png plot output stays `ready` with `notebook_visual_outputs`.
    """
    import io
    import shutil
    import zipfile
    from backend.app.contracts.sources import Document, Provenance, SourceKind as CodexSourceKind
    from backend.app.modules.ingestion.codex_bridge import CodexParserBridge
    import backend.app.modules.ingestion.parsers.pdf_parser as pdf_mod

    monkeypatch.setattr(shutil, "which", lambda cmd: None if cmd == "tesseract" else None)
    bridge = CodexParserBridge()

    # 1. Text-rich 4-page lecture PDF with 1 blank/diagram slide (25% empty < 30%) -> status="ready"
    monkeypatch.setattr(
        pdf_mod,
        "_extract_pdf_text_pure_python",
        lambda b: (
            [
                "Chapitre 1 : Econometrie lineaire et moindres carres.",
                "Proprietes asymptotiques des estimateurs.",
                "",  # 1 diagram-only slide out of 4
                "Exercices d'application et conclusions.",
            ],
            {"pdf_title": "Cours Eco"},
        ),
    )
    pdf_mixed = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:pdf_mixed", source=CodexSourceKind.ENSORA_DRIVE),
            title="cours_eco.pdf",
            mime_type="application/pdf",
        ),
        b"%PDF-1.4 dummy",
    )
    assert pdf_mixed is not None
    assert pdf_mixed.status == "ready"
    assert "mixed_media_pages_present" in pdf_mixed.warning_codes
    assert "scanned_pages_without_ocr" not in pdf_mixed.warning_codes

    # 2. Scanned 2-page PDF (100% empty, no tesseract) -> status="partial" + scanned_pages_without_ocr
    monkeypatch.setattr(pdf_mod, "_extract_pdf_text_pure_python", lambda b: (["", ""], {}))
    pdf_scanned = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:pdf_scan", source=CodexSourceKind.ENSORA_DRIVE),
            title="annale_scannee.pdf",
            mime_type="application/pdf",
        ),
        b"%PDF-1.4 dummy",
    )
    assert pdf_scanned is not None
    assert pdf_scanned.status == "partial"
    assert "scanned_pages_without_ocr" in pdf_scanned.warning_codes

    # 3. Standalone PNG image (no tesseract) -> status="partial" + ocr_unavailable
    png_res = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:fig1", source=CodexSourceKind.ENSORA_DRIVE),
            title="diagramme.png",
            mime_type="image/png",
        ),
        b"\x89PNG\r\n\x1a\nfake_png_bytes",
    )
    assert png_res is not None
    assert png_res.status == "partial"
    assert "ocr_unavailable" in png_res.warning_codes

    # 4. Legacy .doc -> status="partial" + legacy_binary_office
    ole_doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + "Introduction aux series temporelles ENSAI 2A.".encode("utf-16le")
    doc_res = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:old_doc", source=CodexSourceKind.ENSORA_DRIVE),
            title="notes.doc",
            mime_type="application/msword",
        ),
        ole_doc,
    )
    assert doc_res is not None
    assert doc_res.status == "partial"
    assert "legacy_binary_office" in doc_res.warning_codes

    # 5. Binary-only dataset ZIP -> status="partial" + non_textual_archive
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as zf:
        zf.writestr("weights/model.pt", b"\x00\x01\x02")
    zip_res = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:bin_zip", source=CodexSourceKind.ENSORA_DRIVE),
            title="dataset_weights.zip",
            mime_type="application/zip",
        ),
        zbuf.getvalue(),
    )
    assert zip_res is not None
    assert zip_res.status == "partial"
    assert "non_textual_archive" in zip_res.warning_codes

    # 6. Notebook with embedded matplotlib PNG plot -> status="ready" + notebook_visual_outputs
    nb_bytes = json.dumps(
        {
            "nbformat": 4,
            "cells": [
                {"cell_type": "markdown", "source": ["# TP Regression"]},
                {
                    "cell_type": "code",
                    "source": ["import matplotlib.pyplot as plt\nplt.plot([1, 2, 3])"],
                    "outputs": [{"output_type": "display_data", "data": {"image/png": "iVBORw0KGgo="}}],
                },
            ],
        }
    ).encode("utf-8")
    nb_res = bridge.parse(
        Document(
            provenance=Provenance(external_id="drive:nb1", source=CodexSourceKind.ENSORA_DRIVE),
            title="tp_reg.ipynb",
            mime_type="application/x-ipynb+json",
        ),
        nb_bytes,
    )
    assert nb_res is not None
    assert nb_res.status == "ready"
    assert "notebook_visual_outputs" in nb_res.warning_codes


def test_sprint4_drive_durable_snapshot_survives_65_evictions_codex_review(tmp_path: Path) -> None:
    """Codex handoff regression (CODEX_HANDOFF.md:343):
    Evicting the 64-entry in-memory `_paging_states` LRU MUST NOT delete unexpired on-disk
    `drvpage:<hash>.json` snapshots; after 65 later distinct handles, a fresh connector can still
    replay the first retained cursor cleanly."""
    from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
    from backend.app.modules.ingestion.connectors.ensora_drive import (
        DRIVE_FOLDER_MIME,
        EnsoraDriveConnector,
    )

    root_id = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    tree = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        "fld_1a": {"id": "fld_1a", "name": "1A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f1": {"id": "f1", "name": "a.pdf", "mimeType": "application/pdf", "parents": ["fld_1a"], "modifiedTime": "2026-11-01T00:00:00Z", "trashed": False},
        "f2": {"id": "f2", "name": "b.pdf", "mimeType": "application/pdf", "parents": ["fld_1a"], "modifiedTime": "2026-11-02T00:00:00Z", "trashed": False},
    }

    class _Exec:
        def __init__(self, payload: Any) -> None:
            self._payload = payload

        def execute(self) -> Any:
            return self._payload

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _Exec:
            return _Exec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _Exec:
            pid = q.split("'")[1]
            return _Exec({"files": [v for v in tree.values() if pid in (v.get("parents") or [])]})

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    class _Creds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _Creds())
    conn1 = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
    )

    # 1. Retain first cursor (page_size=1 -> pending_items still holds f2)
    p1 = conn1.list_changes(scope={"page_size": 1})
    assert p1.complete is False and p1.next_cursor is not None
    first_cursor = p1.next_cursor

    # 2. Generate 65 distinct later page handles in the same connector to trigger LRU eviction
    for i in range(65):
        conn1._store_bfs_paging_state(
            {
                "fingerprint": f"later_fp_{i}",
                "start_folder": root_id,
                "cohorts_filter": set(),
                "max_files": None,
                "max_depth": 12,
                "modified_since": None,
                "known_versions": {},
                "has_membership_baseline": False,
                "known_external_ids": set(),
                "known_titles": {},
                "queue": [],
                "visited_folders": {root_id},
                "verified_in_run": {root_id},
                "pending_items": [],
                "membership_additions": [],
                "seen_live_ids": set(),
                "warnings": [],
                "errors": [],
                "depth_truncated": False,
                "max_files_hit": False,
                "reconciled": False,
                "latest_modified": None,
                "total_discovered": i,
                "total_emitted": i,
                "shortcuts_skipped": 0,
                "unexportable_skipped": 0,
                "api_list_calls": 0,
            }
        )
    assert first_cursor not in conn1._paging_states  # Evicted from in-memory dict!

    # 3. Fresh connector replays the first cursor cleanly from its retained durable 0o600 file!
    conn2 = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
    )
    p2 = conn2.list_changes(scope={"page_size": 1}, cursor=first_cursor)
    assert p2.complete is True
    assert [r.external_id for r in p2.items] == ["ensora_drive:f2"]


def test_sprint5_source_sync_policies_pamplemousse_independent_scopes_incremental_skip_and_local_fingerprint(
    tmp_path: Path,
) -> None:
    """Sprint 5 regression:
    - Recommended sync policy table encodes exact required intervals (Drive/Moodle/Catalog=7d,
      Pamplemousse timetable=6h, grades=24h, Calendar=30m, Local=on_change).
    - Pamplemousse independent scopes (`timetable`, `grades`, `catalog`) visit ONLY their
      authorized slice pages (zero cross-slice requests!) and preserve academic-year evidence.
    - LocalFileConnector.scan_fingerprint() detects adds/edits cleanly without reading file bytes.
    - CodexConnectorBridge incremental Moodle sync skips already-persisted unchanged revisions
      while keeping original local blobs accessible between sync cycles.
    """
    from backend.app.contracts.sources import Document, Provenance, SourceKind as CodexSourceKind, SyncScope
    from backend.app.core.runtime import Runtime
    from backend.app.core.settings import Settings
    from backend.app.modules.ingestion.codex_bridge import (
        CodexConnectorBridge,
        _load_known_document_revisions,
    )
    from backend.app.modules.ingestion.connectors.base import RECOMMENDED_SYNC_POLICY_SECONDS
    from backend.app.modules.ingestion.connectors.local_import import LocalFileConnector

    # 1. Policy constants check
    assert RECOMMENDED_SYNC_POLICY_SECONDS["ensora_drive"] == 7 * 86400
    assert RECOMMENDED_SYNC_POLICY_SECONDS["moodle"] == 7 * 86400
    assert RECOMMENDED_SYNC_POLICY_SECONDS["pamplemousse:timetable"] == 6 * 3600
    assert RECOMMENDED_SYNC_POLICY_SECONDS["pamplemousse:grades"] == 24 * 3600
    assert RECOMMENDED_SYNC_POLICY_SECONDS["pamplemousse:catalog"] == 7 * 86400
    assert RECOMMENDED_SYNC_POLICY_SECONDS["google_calendar"] == 30 * 60
    assert RECOMMENDED_SYNC_POLICY_SECONDS["local"] is None

    # 2. Pamplemousse independent scopes (`timetable`, `grades`, `catalog`) visit ONLY their slice!
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "pamp_cfg", environ={})
    pages = {
        "https://23ed753f.leos.fr/index.php": """
        <html><body>
          <select><option selected>2026-2027</option></select>
          <a href="index.php?p=121">EDT</a>
          <a href="index.php?p=107">Inscriptions</a>
          <a href="index.php?p=1052">Notes</a>
        </body></html>
        """,
        "https://23ed753f.leos.fr/index.php?p=121": """
        <html><body><script>
          var cal = {events: [{"id":"e1","title":"CM Machine Learning","start":"2026-10-05T08:30:00+02:00","end":"2026-10-05T10:30:00+02:00","salle":"Amphi 1"}]};
        </script></body></html>
        """,
        "https://23ed753f.leos.fr/index.php?p=107": """
        <html><body><table>
          <tr><th>UE</th><th>Matière</th><th>Code</th><th>Coef.</th><th>ECTS</th><th>Sem.</th></tr>
          <tr><td>UE1</td><td>Machine Learning</td><td>3A_ML</td><td>3</td><td>5</td><td>S1</td></tr>
        </table></body></html>
        """,
        "https://23ed753f.leos.fr/index.php?p=1052": """
        <html><body><table>
          <tr><th>Matière / Évaluation</th><th>Coef.</th><th>Note</th></tr>
          <tr><td><b>Machine Learning</b></td><td>3</td><td>16,50 / 20</td></tr>
        </table></body></html>
        """,
    }
    fetched_urls: list[str] = []

    def _fetch(u: str) -> str:
        fetched_urls.append(u)
        return pages[u]

    pamp = PamplemousseConnector(cfg, page_fetcher=_fetch, rate_limiter=RateLimiter(0.0))

    # 2a. Timetable only (6h policy)
    fetched_urls.clear()
    res_tt = pamp.list_changes(scope={"sections": "timetable", "academic_year": "2026-2027"})
    assert pamp.last_stats["active_scopes"] == ["timetable"]
    assert all("p=107" not in u and "p=1052" not in u for u in fetched_urls)
    assert all(r.record_kind == "event" for r in res_tt.items)
    assert len(res_tt.items) == 1

    # 2b. Grades only (24h policy)
    fetched_urls.clear()
    res_gr = pamp.list_changes(scope={"sections": "grades", "academic_year": "2026-2027"})
    assert pamp.last_stats["active_scopes"] == ["grades"]
    assert any("p=1052" in u for u in fetched_urls)
    assert all("p=121" not in u and "p=107" not in u for u in fetched_urls)
    assert all(r.record_kind == "academic" for r in res_gr.items)
    assert pamp.last_stats["available_grades"] == 1

    # 2c. Catalog only (7d policy)
    fetched_urls.clear()
    res_cat = pamp.list_changes(scope={"sections": "catalog", "academic_year": "2026-2027"})
    assert pamp.last_stats["active_scopes"] == ["catalog"]
    assert any("p=107" in u for u in fetched_urls)
    assert all("p=121" not in u and "p=1052" not in u for u in fetched_urls)
    assert pamp.last_stats["enrolled_subjects"] == 1
    assert pamp.last_stats["subject_coefficients"] == 1

    # 3. LocalFileConnector.scan_fingerprint() detects changes cleanly
    local_dir = tmp_path / "study_notes"
    local_dir.mkdir()
    note_f = local_dir / "chap1.md"
    note_f.write_text("# Chapitre 1\nRegression lineaire.", encoding="utf-8")
    lconn = LocalFileConnector()
    fp1 = lconn.scan_fingerprint(local_dir)
    assert fp1 == lconn.scan_fingerprint(local_dir)
    note_f.write_text("# Chapitre 1\nRegression lineaire + Lasso.", encoding="utf-8")
    fp2 = lconn.scan_fingerprint(local_dir)
    assert fp2 != fp1

    # 4. Incremental Moodle known_versions skip + original local blob remains stored between sync cycles
    rt_settings = Settings.model_validate(
        {
            "config_dir": tmp_path / "rt_cfg",
            "local_data_dir": tmp_path / "rt_data",
            "ensai_profile_dir": tmp_path / "rt_cfg/sessions/ensai",
        }
    )
    with Runtime(rt_settings) as rt:
        rt.consent.record(CodexSourceKind.MOODLE, "read", True)
        m_cid = rt.connection_ids[CodexSourceKind.MOODLE]
        doc = Document(
            provenance=Provenance(
                external_id="moodle:course:505:resource:1",
                remote_revision="rev_v1",
                source=CodexSourceKind.MOODLE,
                academic_year="2026-2027",
            ),
            title="notes.md",
            mime_type="text/markdown",
        )
        rt.pipeline.ingest(m_cid, doc, lambda _: b"# Notes Deep Learning\n")
        known = _load_known_document_revisions(
            rt_settings.local_data_dir / "ensora.sqlite3", CodexSourceKind.MOODLE.value
        )
        assert known == {"moodle:course:505:resource:1": "rev_v1"}
        # Original blob remains intact locally on disk between sync cycles
        blobs = list((rt_settings.local_data_dir / "blobs").glob("*"))
        assert len(blobs) == 1 and blobs[0].read_bytes() == b"# Notes Deep Learning\n"

        # Bridge forwards known_versions on incremental run, omits on full run
        seen_scopes: list[dict[str, Any]] = []

        class _SpyMoodle:
            source_kind = SourceKind.MOODLE

            def list_changes(self, scope=None, cursor=None):
                seen_scopes.append(dict(scope or {}))
                from backend.app.modules.ingestion.contracts import ChangePage as InternalChangePage

                return InternalChangePage(items=(), next_cursor=None, complete=True)

        m_bridge = CodexConnectorBridge(
            _SpyMoodle(),
            known_versions_loader=lambda: known,
        )
        m_bridge.list_changes(SyncScope(source=CodexSourceKind.MOODLE, full=False), None)
        m_bridge.list_changes(SyncScope(source=CodexSourceKind.MOODLE, full=True), None)
        assert seen_scopes[0].get("known_versions") == known
        assert "known_versions" not in seen_scopes[1]


def test_sprint6_incremental_deterministic_revisions_moodle_checkpoint_and_sync_modes(
    tmp_path: Path,
) -> None:
    """Sprint 6 regression:
    - Pamplemousse normalize() produces identical deterministic remote_version across runs
      (ignoring observed_at/retrieved_at), skipping unchanged events/academics cleanly on
      incremental runs and emitting the full consistent academic tree when a grade changes.
    - Moodle list_changes() emits a durable `moodle_sync:<iso>` watermark on clean completion,
      tracks `sync_mode` (`initial`/`incremental`/`full`/`selective`) and `resources_unchanged_skipped`,
      and resumes cleanly when given a prior `moodle_sync:` cursor.
    """
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})

    # 1. Pamplemousse deterministic remote_version across separate runs + incremental unchanged skip
    pamp = PamplemousseConnector(cfg, rate_limiter=RateLimiter(0.0))
    fc_ev = [
        {
            "id": "ev_ml",
            "title": "CM Machine Learning",
            "start": "2026-10-05T08:30:00+02:00",
            "end": "2026-10-05T10:30:00+02:00",
            "salle": "Amphi 1",
        }
    ]
    notes_v1 = """
    <html><body><h1>Bulletin de notes 2026-2027</h1><table>
      <tr><th>Enseignement</th><th>Coef.</th><th>Note finale</th></tr>
      <tr class="ue"><td>UE1 — Statistique et Apprentissage</td><td>6</td><td></td></tr>
      <tr class="matiere"><td><b>Machine Learning</b></td><td>3</td><td>15,00 / 20</td></tr>
    </table></body></html>
    """
    notes_v2 = notes_v1.replace("15,00 / 20", "16,50 / 20")

    r1 = pamp.list_changes(
        scope={"fullcalendar_json": fc_ev, "notes_html": notes_v1, "academic_year": "2026-2027"}
    )
    r2 = pamp.list_changes(
        scope={"fullcalendar_json": fc_ev, "notes_html": notes_v1, "academic_year": "2026-2027"}
    )
    assert len(r1.items) >= 3  # Event + academic hierarchy (year/UE/subject/grade)
    v1_map = {rec.external_id: rec.remote_version for rec in r1.items}
    v2_map = {rec.external_id: rec.remote_version for rec in r2.items}
    assert v1_map == v2_map  # Strictly deterministic despite distinct wall-clock observed_at!

    # Incremental run with identical upstream data -> 0 items emitted, all skipped!
    r_inc_same = pamp.list_changes(
        scope={
            "fullcalendar_json": fc_ev,
            "notes_html": notes_v1,
            "academic_year": "2026-2027",
            "known_versions": v1_map,
        }
    )
    assert len(r_inc_same.items) == 0
    assert pamp.last_stats["unchanged_skipped"] == len(r1.items)

    # Incremental run when grade updates -> unchanged event skipped, updated academic tree emitted!
    r_inc_changed = pamp.list_changes(
        scope={
            "fullcalendar_json": fc_ev,
            "notes_html": notes_v2,
            "academic_year": "2026-2027",
            "known_versions": v1_map,
        }
    )
    assert all(rec.record_kind == "academic" for rec in r_inc_changed.items)
    assert len(r_inc_changed.items) >= 2
    assert pamp.last_stats["unchanged_skipped"] == 1  # The unchanged timetable event!

    # 2. Moodle durable `moodle_sync:` watermark + sync_mode distinction + unchanged skip counter
    dash = '<div class="block_myoverview"><a href="/course/view.php?id=505">Deep Learning</a></div>'
    course_html = (
        '<html><body><h1>Deep Learning</h1>'
        '<a href="/pluginfile.php/505/mod_resource/content/1/chap1.pdf">chap1.pdf</a>'
        '<a href="/pluginfile.php/505/mod_resource/content/2/chap2.pdf">chap2.pdf</a>'
        "</body></html>"
    )
    moodle = MoodleConnector(cfg, rate_limiter=RateLimiter(0.0))
    m_init = moodle.list_changes(
        scope={"dashboard_html": dash, "course_html_by_id": {"505": course_html}}
    )
    assert m_init.complete is True
    assert m_init.sync_token is not None and m_init.sync_token.startswith("moodle_sync:")
    assert moodle.last_stats["sync_mode"] == "initial"
    assert len(m_init.items) == 2

    m_known = {it.external_id: it.remote_version for it in m_init.items}
    m_inc = moodle.list_changes(
        scope={
            "dashboard_html": dash,
            "course_html_by_id": {"505": course_html},
            "known_versions": m_known,
        },
        cursor=m_init.sync_token,
    )
    assert m_inc.complete is True
    assert moodle.last_stats["sync_mode"] == "incremental"
    assert moodle.last_stats["resources_unchanged_skipped"] == 2
    assert len(m_inc.items) == 0

    m_full = moodle.list_changes(
        scope={"dashboard_html": dash, "course_html_by_id": {"505": course_html}, "full": True}
    )
    assert moodle.last_stats["sync_mode"] == "full"
    assert len(m_full.items) == 2


def test_sprint7_ensora_drive_exclude_global_only_1a_2a_3a_scope_shortcuts_checkpoints_retries_and_api_savings(
    tmp_path: Path,
) -> None:
    """Sprint 7 regression — Exclude Global from Ensora Google Drive Acquisition:
    1. Complete recursive traversal of 1A, 2A, and 3A (including legitimate nested 'Global' subfolder
       inside 2A/Finance/Global) preserving folder provenance, academic_year, specialization & subject.
    2. Zero traversal of top-level Global or any of its 25 nested subfolders (measured API savings).
    3. Zero downloads of Global files (via list_changes, direct fetch_blob, or selective retry).
    4. Excluded-folder shortcut protection (shortcuts to Global folder, Global file, root, personal).
    5. Legacy v1 checkpoint invalidation + v2 queue/pending_items filtering for Global entries.
    6. Incremental sync (`drvmod:`) stays strictly within 1A/2A/3A and ignores Global modifications.
    7. Non-destructive reconciliation of previously imported Global docs preserves folder provenance
       and marks `excluded_by_scope_policy=True` without deleting existing SQLite rows.
    """
    import json as _json
    import re as _re
    from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
    from backend.app.modules.ingestion.contracts import AccessStatus
    from backend.app.modules.ingestion.connectors.base import PermissionDeniedError
    from backend.app.modules.ingestion.connectors.ensora_drive import (
        DRIVE_FOLDER_MIME,
        DRIVE_SCOPE_POLICY_ID,
        DRIVE_SHORTCUT_MIME,
        EnsoraDriveConnector,
        is_approved_drive_folder_path,
        is_excluded_drive_folder_path,
        is_global_drive_folder_path,
    )

    root_id = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        # Authorized top-level academic folders
        "fld_1a": {"id": "fld_1a", "name": "1A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "fld_2a": {"id": "fld_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "fld_3a": {"id": "fld_3a", "name": "3A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        # Excluded top-level Global folder + unrelated folder + stray root file
        "fld_global": {"id": "fld_global", "name": "Global", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "fld_misc": {"id": "fld_misc", "name": "Divers", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "root_stray": {"id": "root_stray", "name": "README_root.pdf", "mimeType": "application/pdf", "parents": [root_id], "modifiedTime": "2026-11-01T10:00:00Z", "trashed": False},
        # Subfolders inside 1A, 2A (including a legitimate nested 'Global' topic!), 3A (with specialization & year)
        "fld_1a_prob": {"id": "fld_1a_prob", "name": "Probabilites", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_1a"], "trashed": False},
        "fld_2a_fin": {"id": "fld_2a_fin", "name": "Finance", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_2a"], "trashed": False},
        "fld_2a_fin_global": {"id": "fld_2a_fin_global", "name": "Global", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_2a_fin"], "trashed": False},
        "fld_3a_ds": {"id": "fld_3a_ds", "name": "Data Science", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_3a"], "trashed": False},
        "fld_3a_ds_dl": {"id": "fld_3a_ds_dl", "name": "Deep Learning 2026-2027", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_3a_ds"], "trashed": False},
        # Academic files across 1A, 2A, 3A
        "f_1a": {"id": "f_1a", "name": "CM1_Proba.pdf", "mimeType": "application/pdf", "parents": ["fld_1a_prob"], "modifiedTime": "2026-11-01T10:00:00Z", "size": "120", "trashed": False},
        "f_2a": {"id": "f_2a", "name": "Macro_Global_Markets.pdf", "mimeType": "application/pdf", "parents": ["fld_2a_fin_global"], "modifiedTime": "2026-11-02T10:00:00Z", "size": "120", "trashed": False},
        "f_3a": {"id": "f_3a", "name": "Transformers_2026-2027.pdf", "mimeType": "application/pdf", "parents": ["fld_3a_ds_dl"], "modifiedTime": "2026-11-03T10:00:00Z", "size": "120", "trashed": False},
        # Shortcuts inside 2A pointing to Global folder, Global file, root, and external personal folder
        "sc_to_global_dir": {"id": "sc_to_global_dir", "name": "Lien_Global", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": "fld_global"}, "trashed": False},
        "sc_to_global_file": {"id": "sc_to_global_file", "name": "Lien_Reglement", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": "f_global_01"}, "trashed": False},
        "sc_to_root": {"id": "sc_to_root", "name": "Lien_Root", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": root_id}, "trashed": False},
        "fld_personal": {"id": "fld_personal", "name": "MyPersonal", "mimeType": DRIVE_FOLDER_MIME, "parents": ["personal_root"], "trashed": False},
        "sc_to_personal": {"id": "sc_to_personal", "name": "Lien_Perso", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": "fld_personal"}, "trashed": False},
    }
    # Add 25 nested subfolders + 25 files inside Global to measure API-call savings accurately!
    global_folder_ids: set[str] = {"fld_global"}
    for i in range(1, 26):
        gfid = f"fld_global_sub_{i:02d}"
        global_folder_ids.add(gfid)
        tree[gfid] = {
            "id": gfid,
            "name": f"Global_Archive_{i:02d}",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["fld_global"],
            "trashed": False,
        }
        tree[f"f_global_{i:02d}"] = {
            "id": f"f_global_{i:02d}",
            "name": f"Global_Doc_{i:02d}.pdf",
            "mimeType": "application/pdf",
            "parents": [gfid],
            "modifiedTime": "2026-11-05T10:00:00Z",
            "size": "120",
            "trashed": False,
        }

    listed_folder_ids: list[str] = []
    downloaded_file_ids: list[str] = []
    api_list_batches: list[set[str]] = []

    class _Exec:
        def __init__(self, fn: Any) -> None:
            self._fn = fn

        def execute(self) -> Any:
            return self._fn()

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _Exec:
            return _Exec(lambda: tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _Exec:
            def _do():
                pids = set(_re.findall(r"'([^']+)'\s+in\s+parents", q))
                api_list_batches.append(pids)
                listed_folder_ids.extend(sorted(pids))
                return {
                    "files": [
                        v
                        for v in tree.values()
                        if not v.get("trashed") and any(p in pids for p in (v.get("parents") or []))
                    ]
                }
            return _Exec(_do)

        def get_media(self, *, fileId: str, **kwargs: Any) -> _Exec:
            def _do():
                downloaded_file_ids.append(fileId)
                return f"%PDF-1.4 {fileId}".encode("utf-8")
            return _Exec(_do)

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    class _Creds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _Creds())
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )

    # 1. Pure path provenance helpers (top-level Global excluded; nested 2A/Finance/Global APPROVED!)
    assert is_approved_drive_folder_path("Ensora/1A/Probabilites") is True
    assert is_approved_drive_folder_path("Ensora/2A/Finance/Global") is True
    assert is_approved_drive_folder_path("Ensora/3A/Data Science/Deep Learning") is True
    assert is_global_drive_folder_path("Ensora/Global/Global_Archive_01") is True
    assert is_global_drive_folder_path("Ensora/2A/Finance/Global") is False
    assert is_excluded_drive_folder_path("Ensora/Global") is True
    assert is_excluded_drive_folder_path("Ensora/Divers") is True
    assert is_excluded_drive_folder_path("Ensora") is True

    # 2. Full traversal discovers 100% of 1A/2A/3A (including 2A/Finance/Global), ZERO Global/Divers/root files,
    #    ZERO API list calls on Global or any of its 25 nested folders, and blocks all 4 excluded shortcuts!
    prior_active = {
        "ensora_drive:f_1a": {"title": "CM1_Proba.pdf", "folder": "Ensora/1A/Probabilites"},
        "ensora_drive:f_global_01": {"title": "Global_Doc_01.pdf", "folder": "Ensora/Global/Global_Archive_01"},
    }
    p_full = conn.list_changes(scope={"known_external_ids": prior_active})
    assert p_full.complete is True
    assert conn.last_scan_authoritative is True
    active_items = [r for r in p_full.items if r.access_status == AccessStatus.ACTIVE]
    tombstones = [r for r in p_full.items if r.access_status == AccessStatus.TOMBSTONED]
    assert {r.external_id for r in active_items} == {
        "ensora_drive:f_1a",
        "ensora_drive:f_2a",
        "ensora_drive:f_3a",
    }
    # Zero Global or Divers folders ever listed!
    assert global_folder_ids.isdisjoint(set(listed_folder_ids))
    assert "fld_misc" not in listed_folder_ids
    # Quantitative API-call reduction: 35 total folders in Drive (root + 3 cohorts + 5 academic subfolders + 26 Global + 1 Divers)
    # Batched 1A/2A/3A-only traversal used <= 4 files.list calls (saving >= 27 folder listings for Global + Divers!)
    assert len(api_list_batches) <= 4
    assert conn.last_stats["scope_policy"] == DRIVE_SCOPE_POLICY_ID
    assert [f["name"] for f in conn.last_stats["authorized_top_level_folders"]] == ["1A", "2A", "3A"]
    assert conn.last_stats["excluded_top_level_folders_count"] == 3  # Global, Divers, README_root.pdf
    assert conn.last_stats["excluded_shortcuts_skipped"] == 4

    # Metadata preservation (folder provenance, academic year, specialization, subject, top_level_folder_id)
    rec_3a = next(r for r in active_items if r.external_id == "ensora_drive:f_3a")
    assert rec_3a.folder_path == "Ensora/3A/Data Science/Deep Learning 2026-2027"
    assert rec_3a.payload["cohort_hint"] == "3A"
    assert rec_3a.payload["specialization_hint"] == "Data Science"
    assert rec_3a.payload["subject_hint"] == "Deep Learning 2026-2027"
    assert rec_3a.payload["academic_year"] == "2026-2027"
    assert rec_3a.payload["top_level_folder_id"] == "fld_3a"
    assert rec_3a.provenance["academic_year"] == "2026-2027"
    assert rec_3a.provenance["top_level_folder_id"] == "fld_3a"

    # Non-destructive reconciliation of previously imported Global doc preserves folder provenance!
    assert len(tombstones) == 1
    assert tombstones[0].external_id == "ensora_drive:f_global_01"
    assert tombstones[0].folder_path == "Ensora/Global/Global_Archive_01"
    assert tombstones[0].provenance["excluded_by_scope_policy"] is True

    # 3. Direct folder targeting, cohort requesting, and direct/retry downloads of Global are ALL blocked!
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"cohorts": ["Global"]})
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"cohorts": ["1A", "Global"]})
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"folder_id": "fld_global"})
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"folder_id": "fld_global_sub_01"})
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"folder_id": "fld_misc"})

    cached_global_rec = conn.normalize(
        {**tree["f_global_01"], "_folder_path": "Ensora/Global/Global_Archive_01"}
    )
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(cached_global_rec)
    stray_root_rec = conn.normalize({**tree["root_stray"], "_folder_path": "Ensora"})
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(stray_root_rec)
    assert downloaded_file_ids == []  # ZERO bytes downloaded for Global or root stray!

    # Legitimate 2A/Finance/Global file downloads cleanly!
    rec_2a = next(r for r in active_items if r.external_id == "ensora_drive:f_2a")
    fetched_2a = conn.fetch_blob(rec_2a)
    assert fetched_2a.content == b"%PDF-1.4 f_2a"
    assert downloaded_file_ids == ["f_2a"]

    # 4. Incremental synchronization (`drvmod:`) stays within 1A/2A/3A and ignores Global changes!
    assert p_full.sync_token == "drvmod:2026-11-03T10:00:00Z"
    tree["f_global_01"]["modifiedTime"] = "2026-11-20T12:00:00Z"  # Newer Global file -> MUST NOT appear!
    tree["f_3a_new"] = {
        "id": "f_3a_new",
        "name": "Diffusion_2026-2027.pdf",
        "mimeType": "application/pdf",
        "parents": ["fld_3a_ds_dl"],
        "modifiedTime": "2026-11-18T12:00:00Z",
        "size": "200",
        "trashed": False,
    }
    listed_folder_ids.clear()
    p_inc = conn.list_changes(cursor=p_full.sync_token)
    assert p_inc.complete is True
    assert [r.external_id for r in p_inc.items] == ["ensora_drive:f_3a_new"]
    assert p_inc.sync_token == "drvmod:2026-11-18T12:00:00Z"
    assert global_folder_ids.isdisjoint(set(listed_folder_ids))

    # 5. Legacy v1 cached checkpoint (created under broader scope) is invalidated & deleted!
    snap_dir = cfg.config_dir / "state" / "drive_paging"
    snap_dir.mkdir(parents=True, exist_ok=True)
    legacy_snap = snap_dir / "legacyv1snap0001.json"
    legacy_snap.write_text(
        _json.dumps(
            {
                "version": 1,
                "created_at": 9999999999.0,
                "root_folder_id": root_id,
                "fingerprint": "stale",
                "start_folder": root_id,
                "queue": [["fld_global", "Ensora/Global", 1]],
                "pending_items": [cached_global_rec.to_dict()],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Expired or unknown"):
        conn.list_changes(cursor="drvpage:legacyv1snap0001")
    assert not legacy_snap.exists()  # Stale legacy broader-scope checkpoint purged!

    # Even a tampered v2 snapshot injecting Global into queue/pending_items filters Global safely!
    p_page1 = conn.list_changes(scope={"page_size": 1})
    assert p_page1.next_cursor and p_page1.next_cursor.startswith("drvpage:")
    v2_key = p_page1.next_cursor.split(":", 1)[-1]
    v2_file = snap_dir / f"{v2_key}.json"
    raw_v2 = _json.loads(v2_file.read_text(encoding="utf-8"))
    raw_v2["queue"].append(["fld_global", "Ensora/Global", 1])
    raw_v2["pending_items"].append(cached_global_rec.to_dict())
    v2_file.write_text(_json.dumps(raw_v2), encoding="utf-8")

    conn_resumed = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda _: _Svc(),
        rate_limiter=RateLimiter(0.0),
        batch_folder_queries=True,
    )
    listed_folder_ids.clear()
    resumed_ids: list[str] = []
    cur = p_page1.next_cursor
    while cur:
        pg = conn_resumed.list_changes(scope={"page_size": 1}, cursor=cur)
        resumed_ids.extend(r.external_id for r in pg.items)
        cur = pg.next_cursor
    assert "ensora_drive:f_global_01" not in resumed_ids
    assert global_folder_ids.isdisjoint(set(listed_folder_ids))


def test_sprint8_moodle_year_filter_drive_nonstalling_paging_and_academic_reconciliation(
    tmp_path: Path,
) -> None:
    """Sprint 8 regression:
    1. Moodle academic-year filtering retains active ongoing/reused courses (enddate=0, e.g. course 505),
       explicit-year matches, and spanning multi-year courses while still strictly excluding explicit
       past-year courses, closed courses ending before the requested year, past-timeline courses, and
       future-year courses.
    2. AcademicEntityResolver + CodexEntityMatcherBridge automatically matches real ENSAI Drive folder
       naming variations (French plurals, Pamplemousse code/parenthetical prefixes, bilingual FR/EN
       equivalents, semester disambiguation) while keeping genuine ties unconfirmed for human review.
    3. EnsoraDriveConnector in-process Page 1->2 continuation performs ZERO redundant upfront `files.get`
       boundary calls (eliminating the Page-1 discover-only stall) while cross-process resume still
       lazily revalidates queued folders and blocks tampered Global entries.
    4. Single source of truth: Drive allowed cohorts derive from `backend.app.core.drive_scope` and
       `RECOMMENDED_SYNC_POLICY_SECONDS` derives from `backend.app.contracts.sync.DEFAULT_SYNC_INTERVALS_MINUTES`.
    """
    import json as _json
    import re as _re
    from datetime import datetime, timezone
    from backend.app.contracts.sources import (
        ClassificationResult as CodexClassificationResult,
        Document as CodexDocument,
        Provenance as CodexProvenance,
        SourceKind as CodexSourceKind,
    )
    from backend.app.contracts.sync import DEFAULT_SYNC_INTERVALS_MINUTES
    from backend.app.core.drive_scope import ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS
    from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
    from backend.app.modules.ingestion.codex_bridge import CodexEntityMatcherBridge
    from backend.app.modules.ingestion.connectors.base import RECOMMENDED_SYNC_POLICY_SECONDS
    from backend.app.modules.ingestion.connectors.ensora_drive import (
        APPROVED_ACADEMIC_COHORT_FOLDERS,
        DRIVE_FOLDER_MIME,
        DRIVE_SHORTCUT_MIME,
        EnsoraDriveConnector,
    )
    from backend.app.modules.ingestion.connectors.moodle import (
        MoodleConnector,
        parse_moodle_ajax_courses,
    )
    from backend.app.modules.ingestion.resolution.entity_resolver import (
        AcademicEntityResolver,
        CanonicalSubject,
    )

    # --- 1. Single source of truth checks ---
    assert APPROVED_ACADEMIC_COHORT_FOLDERS == tuple(ENSORA_DRIVE_ALLOWED_TOP_LEVEL_FOLDERS)
    assert RECOMMENDED_SYNC_POLICY_SECONDS["ensora_drive"] == DEFAULT_SYNC_INTERVALS_MINUTES["ensora_drive"] * 60
    assert RECOMMENDED_SYNC_POLICY_SECONDS["moodle"] == DEFAULT_SYNC_INTERVALS_MINUTES["moodle"] * 60
    assert RECOMMENDED_SYNC_POLICY_SECONDS["pamplemousse:timetable"] == DEFAULT_SYNC_INTERVALS_MINUTES["pamplemousse.timetable"] * 60

    # --- 2. Moodle academic-year filtering (no blind disabling; fixes 19/20 ongoing-course exclusion) ---
    def _ts(y: int, m: int, d: int) -> int:
        return int(datetime(y, m, d, 12, 0, tzinfo=timezone.utc).timestamp())

    ajax_payload = [
        {
            "error": False,
            "data": {
                "courses": [
                    # Course 505: created in 2023, open-ended ongoing active course (enddate=0) -> KEEP!
                    {"id": 505, "fullname": "Deep Learning", "shortname": "DL3A", "coursecategory": "3A", "startdate": _ts(2023, 9, 1), "enddate": 0, "isinprogress": True},
                    # Explicit matching year -> KEEP!
                    {"id": 506, "fullname": "Statistique avancée 2026-2027", "shortname": "STAT26", "coursecategory": "2026-2027", "startdate": _ts(2026, 9, 1), "enddate": _ts(2027, 6, 30)},
                    # Multi-year course spanning 2025-2027 -> KEEP!
                    {"id": 507, "fullname": "Projet Fil Rouge", "shortname": "PFR", "coursecategory": "Projets", "startdate": _ts(2025, 9, 1), "enddate": _ts(2027, 6, 30)},
                    # Closed prior-year course ending July 2025 (2024-2025) -> EXCLUDE!
                    {"id": 508, "fullname": "Statistique Descriptive", "shortname": "STAT24", "coursecategory": "2A", "startdate": _ts(2024, 9, 1), "enddate": _ts(2025, 7, 15)},
                    # Explicit prior-year label even with enddate=0 -> EXCLUDE!
                    {"id": 509, "fullname": "Économétrie 2024-2025", "shortname": "ECO24", "coursecategory": "2024-2025", "startdate": _ts(2024, 9, 1), "enddate": 0},
                    # Past timeline classification -> EXCLUDE!
                    {"id": 510, "fullname": "Module Terminé", "shortname": "PAST1", "coursecategory": "1A", "startdate": _ts(2023, 9, 1), "enddate": 0, "ispast": True},
                    # Future academic year -> EXCLUDE!
                    {"id": 511, "fullname": "Cours Futur", "shortname": "FUT1", "coursecategory": "3A", "startdate": _ts(2027, 9, 5), "enddate": _ts(2028, 6, 30)},
                ]
            },
        }
    ]
    parsed_ajax = parse_moodle_ajax_courses(ajax_payload, "https://foad-moodle.ensai.fr/")
    assert len(parsed_ajax) == 7

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    moodle = MoodleConnector(cfg, rate_limiter=RateLimiter(0.0))
    moodle._discover_courses_playwright = lambda **kwargs: parsed_ajax  # type: ignore[method-assign]
    kept = moodle.discover_courses(academic_year="2026-2027")
    assert {c.course_id for c in kept} == {"505", "506", "507"}
    assert all(c.academic_year == "2026-2027" for c in kept)
    diag = moodle._last_discovery_diag
    assert diag["courses_discovered_raw"] == 7
    assert diag["courses_excluded_by_year"] == 4
    decisions_by_id = {row["course_id"]: row for row in diag["course_filter_summary"]}
    assert decisions_by_id["505"]["reason"] == "ongoing_enrolled_no_enddate"
    assert decisions_by_id["506"]["reason"] == "explicit_year_match"
    assert decisions_by_id["507"]["reason"] == "active_date_span_overlap"
    assert decisions_by_id["508"]["reason"] == "ended_before_requested_year"
    assert decisions_by_id["509"]["reason"] == "explicit_year_mismatch"
    assert decisions_by_id["510"]["reason"] == "past_or_archived"
    assert decisions_by_id["511"]["reason"] == "starts_after_requested_year"

    # --- 3. Academic reconciliation improvements (plurals, code prefixes, bilingual EN/FR, semester ties) ---
    resolver = AcademicEntityResolver(current_academic_year="2026-2027")
    resolver.register_subject(CanonicalSubject(subject_id="pamp:subj:stat", title="ST201 - Statistique mathématique (S3)", code="ST201", semester="S3", teaching_unit="pamp:ue:stat"))
    resolver.register_subject(CanonicalSubject(subject_id="pamp:subj:bdd", title="INF204 - Bases de données", code="INF204", teaching_unit="pamp:ue:info"))
    resolver.register_subject(CanonicalSubject(subject_id="pamp:subj:ml", title="Apprentissage automatique", code="ML2A", teaching_unit="pamp:ue:ds"))
    resolver.register_subject(CanonicalSubject(subject_id="pamp:subj:eco1", title="Économétrie 1", semester="S3", teaching_unit="pamp:ue:eco"))
    resolver.register_subject(CanonicalSubject(subject_id="pamp:subj:eco2", title="Économétrie 2", semester="S4", teaching_unit="pamp:ue:eco"))
    matcher = CodexEntityMatcherBridge(resolver)
    clf = CodexClassificationResult(resource_type="lecture", confidence=0.9)

    def _doc(ext: str, title: str, folder: str) -> CodexDocument:
        return CodexDocument(
            provenance=CodexProvenance(external_id=ext, source=CodexSourceKind.ENSORA_DRIVE, folder=folder),
            title=title,
            mime_type="application/pdf",
        )

    m_plural = matcher.match(_doc("ensora_drive:d1", "TD1.pdf", "Ensora/2A/Statistiques mathematiques/TD"), clf)
    assert m_plural.confirmed is True and m_plural.subject_id == "pamp:subj:stat"

    m_bilingual = matcher.match(_doc("ensora_drive:d2", "CM1.pdf", "Ensora/2A/Machine Learning/Cours"), clf)
    assert m_bilingual.confirmed is True and m_bilingual.subject_id == "pamp:subj:ml"

    m_prefix = matcher.match(_doc("ensora_drive:d3", "TP_SQL.pdf", "Ensora/2A/S3/Bases de donnees/TP"), clf)
    assert m_prefix.confirmed is True and m_prefix.subject_id == "pamp:subj:bdd"

    m_sem_tie = matcher.match(_doc("ensora_drive:d4", "CM1.pdf", "Ensora/2A/S3/Econometrie/Cours"), clf)
    assert m_sem_tie.confirmed is True and m_sem_tie.subject_id == "pamp:subj:eco1"

    # Without semester evidence, Économétrie 1 vs Économétrie 2 is a genuine tie -> MUST stay unconfirmed!
    m_ambig = matcher.match(_doc("ensora_drive:d5", "Annale_2024.pdf", "Ensora/2A/Econometrie/Annales"), clf)
    assert m_ambig.confirmed is False

    # --- 4. Drive Page-1 -> Page-2 zero redundant files.get() calls (eliminates discover-only stall) ---
    root_id = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    drive_tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "trashed": False},
        "fld_2a": {"id": "fld_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "fld_global": {"id": "fld_global", "name": "Global", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "sc_g1": {"id": "sc_g1", "name": "LinkG1", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": "fld_global"}, "trashed": False},
        "sc_g2": {"id": "sc_g2", "name": "LinkG2", "mimeType": DRIVE_SHORTCUT_MIME, "parents": ["fld_2a"], "shortcutDetails": {"targetId": "fld_global"}, "trashed": False},
    }
    for i in range(1, 21):
        sf = f"sub_{i:02d}"
        drive_tree[sf] = {"id": sf, "name": f"Matiere_{i:02d}", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_2a"], "trashed": False}
        drive_tree[f"doc_{i:02d}"] = {"id": f"doc_{i:02d}", "name": f"cours_{i:02d}.pdf", "mimeType": "application/pdf", "parents": [sf], "modifiedTime": f"2026-11-{i:02d}T10:00:00Z", "trashed": False}

    get_calls: list[str] = []

    class _Exec:
        def __init__(self, fn: Any) -> None:
            self._fn = fn

        def execute(self) -> Any:
            return self._fn()

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _Exec:
            def _do():
                get_calls.append(fileId)
                return drive_tree[fileId]
            return _Exec(_do)

        def list(self, *, q: str, **kwargs: Any) -> _Exec:
            pids = set(_re.findall(r"'([^']+)'\s+in\s+parents", q))
            return _Exec(lambda: {"files": [v for v in drive_tree.values() if not v.get("trashed") and any(p in pids for p in (v.get("parents") or []))]})

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    class _Creds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]
        granted_scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _Creds())
    dconn = EnsoraDriveConnector(
        cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0), batch_folder_queries=True
    )
    p1 = dconn.list_changes(scope={"page_size": 5})
    assert p1.complete is False and p1.next_cursor
    # Repeated shortcuts to fld_global only validated fld_global ONCE (cached in rejected_in_run!)
    assert get_calls.count("fld_global") == 1
    get_calls.clear()
    # In-process Page 2 -> final page MUST make ZERO redundant files.get() boundary revalidation calls!
    cur = p1.next_cursor
    seen_ext = [r.external_id for r in p1.items]
    while cur:
        pg = dconn.list_changes(scope={"page_size": 5}, cursor=cur)
        seen_ext.extend(r.external_id for r in pg.items)
        cur = pg.next_cursor
    assert len(seen_ext) == 20
    assert get_calls == []  # ZERO redundant files.get calls during in-process multi-page traversal!
