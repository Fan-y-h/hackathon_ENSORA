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
