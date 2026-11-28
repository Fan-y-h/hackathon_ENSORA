"""Unit tests for Moodle, Pamplemousse, Ensora Drive, Google Calendar, and Local/Academic connectors."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
from backend.app.modules.ingestion.config import (
    DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID,
    GOOGLE_SCOPE_CALENDAR_READONLY,
    GOOGLE_SCOPE_DRIVE_READONLY,
    SourcesRuntimeConfig,
)
from backend.app.modules.ingestion.connectors.base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
    TransientSourceError,
    retry_with_backoff,
)
from backend.app.modules.ingestion.connectors.ensora_drive import (
    DRIVE_FOLDER_MIME,
    DRIVE_SHORTCUT_MIME,
    EnsoraDriveConnector,
)
from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector
from backend.app.modules.ingestion.connectors.local_import import (
    LocalFileConnector,
    StructuredAcademicImporter,
)
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.connectors.pamplemousse import PamplemousseConnector
from backend.app.modules.ingestion.contracts import (
    AccessStatus,
    CalendarEventType,
    SourceKind,
    ValueStatus,
)


class _DummyCreds:
    valid = True
    expired = False
    refresh_token = "rt"
    scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
    granted_scopes = scopes


class _HttpErr(Exception):
    def __init__(self, status_code: int, message: str = "error") -> None:
        super().__init__(message)
        self.status_code = status_code


def _fast_limiter() -> RateLimiter:
    return RateLimiter(0.0)


def test_retry_policy_retries_transient_but_never_auth_or_permission() -> None:
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientSourceError("HTTP 503", status_code=503)
        return "ok"

    assert retry_with_backoff(flaky, max_retries=3, base_delay_sec=0.0, sleeper=lambda _: None) == "ok"
    assert calls["n"] == 3

    auth_calls = {"n": 0}

    def auth_fail():
        auth_calls["n"] += 1
        raise AuthenticationRequiredError("401")

    with pytest.raises(AuthenticationRequiredError):
        retry_with_backoff(auth_fail, max_retries=3, sleeper=lambda _: None)
    assert auth_calls["n"] == 1


def test_moodle_course_discovery_resource_extraction_and_safety(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    dashboard_html = """
    <html><body>
      <a href="/course/view.php?id=101">Machine Learning 2A</a>
      <a href="/course/view.php?id=202">PPDP Privacy</a>
      <a href="https://external.example.org/course/view.php?id=999">External Link</a>
    </body></html>
    """
    course_101_html = """
    <html><body>
      <h3 class="sectionname">Chapitre 1 : Régression</h3>
      <a href="/pluginfile.php/10/mod_resource/content/1/CM1_Regression.pdf" data-modified="2026-10-01T10:00:00Z">CM1_Regression.pdf</a>
      <a href="/mod/assign/view.php?id=55" data-deadline="2026-12-10T23:59:00Z">DM1 - Date de remise: 2026-12-10 23:59</a>
      <a href="/mod/forum/view.php?id=77">Annonces du cours</a>
      <a href="https://untrusted.example.com/payload.pdf">External PDF</a>
    </body></html>
    """
    course_202_html = """
    <html><body>
      <h3 class="sectionname">TD &amp; Notebooks</h3>
      <a href="/pluginfile.php/20/mod_resource/content/1/TP1_PPDP.ipynb">TP1_PPDP.ipynb</a>
    </body></html>
    """

    conn = MoodleConnector(
        cfg,
        rate_limiter=_fast_limiter(),
        blob_fetcher=lambda url: (b"%PDF-1.4\n%%EOF", "CM1_Regression.pdf", "application/pdf"),
    )

    # Single-course-first limits discovery to 1 course
    page1 = conn.list_changes(
        scope={
            "single_course_first": True,
            "dashboard_html": dashboard_html,
            "course_html_by_id": {"101": course_101_html, "202": course_202_html},
        }
    )
    assert len(page1.items) == 3  # external link ignored!
    assert any(r.record_kind == "assignment" and r.payload["deadline_at"] == "2026-12-10T23:59:00Z" for r in page1.items)
    assert any(r.record_kind == "announcement" for r in page1.items)
    assert page1.warnings

    # Pagination across all courses + incremental known_versions skip
    known = {r.external_id: r.remote_version for r in page1.items if r.remote_version}
    p_first = conn.list_changes(
        scope={
            "page_size": 1,
            "known_versions": known,
            "dashboard_html": dashboard_html,
            "course_html_by_id": {"101": course_101_html, "202": course_202_html},
        }
    )
    assert p_first.complete is False
    assert p_first.next_cursor == "1"
    assert len(p_first.items) == 0  # skipped unchanged course 101

    p_second = conn.list_changes(
        scope={
            "page_size": 1,
            "known_versions": known,
            "dashboard_html": dashboard_html,
            "course_html_by_id": {"101": course_101_html, "202": course_202_html},
        },
        cursor=p_first.next_cursor,
    )
    assert p_second.complete is True
    assert len(p_second.items) == 1
    assert p_second.items[0].mime_type == "application/x-ipynb+json"

    # Fetch blob within same origin succeeds, cross-origin blocked
    pdf_item = next(r for r in page1.items if r.record_kind == "document")
    fetched = conn.fetch_blob(pdf_item)
    assert fetched.size_bytes > 0
    bad_item = conn.normalize(
        {
            "course_id": "101",
            "module_id": "999",
            "title": "Evil",
            "url": "https://evil.example/file.pdf",
            "download_url": "https://evil.example/file.pdf",
        }
    )
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(bad_item)


def test_pamplemousse_timetable_ambiguity_and_academic_extraction(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    conn = PamplemousseConnector(cfg, rate_limiter=_fast_limiter())

    # 1. HTML timetable with red-colored event and Sout.ID ambiguity
    timetable_html = """
    <table>
      <tr><th>Date</th><th>Début</th><th>Fin</th><th>Matière</th><th>Salle</th><th>Enseignant</th><th>Groupe</th></tr>
      <tr data-color="red"><td>07/12/2026</td><td>08:30</td><td>10:30</td><td>PPDP</td><td>Amphi 1</td><td>Prof A</td><td></td></tr>
      <tr><td>08/12/2026</td><td>14:00</td><td>16:00</td><td>Sout.ID Projet</td><td>B204</td><td>Prof B</td><td>G1</td></tr>
      <tr><td>09/12/2026</td><td>10:00</td><td>12:00</td><td>CM Machine Learning</td><td>Amphi 2</td><td>Prof C</td><td>2A</td></tr>
    </table>
    """
    # 2. Academic HTML distinguishing confirmed vs missing coefficients/marks
    academic_html = """
    <table>
      <tr><th>Année</th><th>Semestre</th><th>UE</th><th>Code</th><th>Matière</th><th>Coef</th><th>Note</th></tr>
      <tr><td>2026-2027</td><td>S5</td><td>UE Data Science</td><td>ML2A</td><td>Machine Learning</td><td>3</td><td>14,5</td></tr>
      <tr><td>2026-2027</td><td>S5</td><td>UE Data Science</td><td>DL2A</td><td>Deep Learning</td><td></td><td></td></tr>
      <tr><td>2026-2027</td><td>S5</td><td>UE Stats</td><td>ST2A</td><td>Statistique</td><td>2</td><td>0</td></tr>
    </table>
    """
    page = conn.list_changes(
        scope={"timetable_html": timetable_html, "academic_html": academic_html}
    )
    events = [r for r in page.items if r.record_kind == "event"]
    academics = [r for r in page.items if r.record_kind == "academic"]
    assert len(events) == 3
    assert len(academics) == 3

    # Red event is NOT blindly classified as confirmed written exam; confirmation_required=True
    ppdp = events[0].payload
    assert ppdp["event_type"] == CalendarEventType.OTHER.value
    assert ppdp["confirmation_required"] is True
    assert ppdp["start_at"] == "2026-12-07T07:30:00Z"  # Europe/Paris CET (+01:00) -> UTC

    sout = events[1].payload
    assert sout["event_type"] == CalendarEventType.PRESENTATION.value
    assert sout["confirmation_required"] is True

    # Academic records: missing coef/grade stay None + MISSING; actual grade 0 stays "0" + CONFIRMED
    ml, dl, stat = [a.payload for a in academics]
    assert ml["weight"] == "3" and ml["weight_status"] == ValueStatus.CONFIRMED.value
    assert ml["grade_value"] == "14.5" and ml["grade_status"] == ValueStatus.CONFIRMED.value
    assert dl["weight"] is None and dl["weight_status"] == ValueStatus.MISSING.value
    assert dl["grade_value"] is None and dl["grade_status"] == ValueStatus.MISSING.value
    assert stat["grade_value"] == "0" and stat["grade_status"] == ValueStatus.CONFIRMED.value
    assert stat["rule_verification_status"] == ValueStatus.UNKNOWN.value

    # 3. ICS fallback with weekly RRULE & EXDATE
    ics = (
        "BEGIN:VCALENDAR\r\n"
        "BEGIN:VEVENT\r\n"
        "UID:pamp-rrule-1\r\n"
        "SUMMARY:TD Statistique\r\n"
        "DTSTART;TZID=Europe/Paris:20261201T090000\r\n"
        "DTEND;TZID=Europe/Paris:20261201T110000\r\n"
        "RRULE:FREQ=WEEKLY;COUNT=3\r\n"
        "EXDATE;TZID=Europe/Paris:20261208T090000\r\n"
        "LOCATION:Salle C102\r\n"
        "END:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    )
    ics_events = conn.import_ics_text(ics)
    assert len(ics_events) == 2  # 3 occurrences minus 1 EXDATE
    assert ics_events[0].event_type == CalendarEventType.TD
    assert ics_events[0].room == "Salle C102"


def test_ensora_drive_traversal_boundaries_exports_and_incremental(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    drive_tree = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": []},
        "fld_2a": {"id": "fld_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id]},
        "fld_ml": {"id": "fld_ml", "name": "Machine Learning", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_2a"]},
        "fld_other": {"id": "fld_other", "name": "PersonalPrivate", "mimeType": DRIVE_FOLDER_MIME, "parents": ["personal_root"]},
        "file_pdf": {
            "id": "file_pdf",
            "name": "Partiel_ML_2024.pdf",
            "mimeType": "application/pdf",
            "parents": ["fld_ml"],
            "modifiedTime": "2026-10-05T12:00:00Z",
            "md5Checksum": "abc123",
            "size": "1024",
        },
        "file_gdoc": {
            "id": "file_gdoc",
            "name": "Fiche_ML",
            "mimeType": "application/vnd.google-apps.document",
            "parents": ["fld_ml"],
            "modifiedTime": "2026-11-01T12:00:00Z",
            "version": "7",
        },
        "shortcut_ext": {
            "id": "shortcut_ext",
            "name": "OutsideLink",
            "mimeType": DRIVE_SHORTCUT_MIME,
            "parents": ["fld_ml"],
            "shortcutDetails": {"targetId": "fld_other"},
        },
        "file_outside": {
            "id": "file_outside",
            "name": "SecretPersonalTax.pdf",
            "mimeType": "application/pdf",
            "parents": ["fld_other"],
        },
    }

    class _FakeExec:
        def __init__(self, result: Any) -> None:
            self._result = result

        def execute(self) -> Any:
            return self._result

    class _FakeFiles:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            if fileId not in drive_tree:
                raise _HttpErr(404)
            return _FakeExec(drive_tree[fileId])

        def list(self, *, q: str, pageToken: str | None = None, **kwargs: Any) -> _FakeExec:
            parent_id = q.split("'")[1]
            children = [f for f in drive_tree.values() if parent_id in (f.get("parents") or [])]
            return _FakeExec({"files": children})

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(b"%PDF-1.4\n%%EOF")

        def export_media(self, *, fileId: str, mimeType: str) -> _FakeExec:
            return _FakeExec(b"%PDF-1.4 exported\n%%EOF")

    class _FakeDriveService:
        def files(self) -> _FakeFiles:
            return _FakeFiles()

    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: _DummyCreds(),
    )
    conn = EnsoraDriveConnector(
        cfg,
        oauth_adapter=oauth,
        drive_service_factory=lambda creds: _FakeDriveService(),
        rate_limiter=_fast_limiter(),
    )

    # Personal Drive explicitly blocked
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"personal_drive": True})

    # Folder outside Ensora root explicitly blocked
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"folder_id": "fld_other"})

    page = conn.list_changes()
    assert len(page.items) == 2
    assert any("Skipped shortcut" in w for w in page.warnings)
    pdf_rec = next(r for r in page.items if r.external_id == "ensora_drive:file_pdf")
    gdoc_rec = next(r for r in page.items if r.external_id == "ensora_drive:file_gdoc")
    assert pdf_rec.folder_path == "Ensora/2A/Machine Learning"
    assert pdf_rec.payload["cohort_hint"] == "2A"
    assert gdoc_rec.mime_type == "application/pdf"

    # Incremental modified_since skips older file
    inc_page = conn.list_changes(scope={"modified_since": "2026-10-10T00:00:00Z"})
    assert len(inc_page.items) == 1
    assert inc_page.items[0].external_id == "ensora_drive:file_gdoc"

    # Export Google Doc -> PDF succeeds
    exported = conn.fetch_blob(gdoc_rec)
    assert exported.filename == "Fiche_ML.pdf"
    assert exported.exported_from_mime == "application/vnd.google-apps.document"

    # Downloading outside file blocked even if crafted record passed
    outside_rec = conn.normalize({"id": "file_outside", "name": "Secret.pdf", "mimeType": "application/pdf"})
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(outside_rec)


def test_google_calendar_sync_410_recovery_and_privacy_availability(tmp_path: Path) -> None:
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.GOOGLE_CALENDAR,
        credentials_loader=lambda p, s: _DummyCreds(),
    )

    call_log: list[dict[str, Any]] = []

    class _FakeExec:
        def __init__(self, res: Any) -> None:
            self._res = res

        def execute(self) -> Any:
            return self._res

    class _FakeEvents:
        def list(self, **params: Any) -> _FakeExec:
            call_log.append(params)
            if params.get("syncToken") == "stale_token":
                raise _HttpErr(410, "Sync token is no longer valid")
            return _FakeExec(
                {
                    "timeZone": "Europe/Paris",
                    "nextSyncToken": "fresh_sync_token_42",
                    "items": [
                        {
                            "id": "ev1",
                            "status": "confirmed",
                            "summary": "Doctor appointment",
                            "description": "Very private medical notes",
                            "start": {"dateTime": "2026-12-03T14:00:00+01:00"},
                            "end": {"dateTime": "2026-12-03T15:00:00+01:00"},
                        },
                        {
                            "id": "ev2",
                            "status": "confirmed",
                            "summary": "Holiday",
                            "start": {"date": "2026-12-25"},
                            "end": {"date": "2026-12-26"},
                        },
                        {
                            "id": "ev3",
                            "status": "cancelled",
                            "summary": "Deleted meeting",
                        },
                    ],
                }
            )

    class _FakeCalService:
        def events(self) -> _FakeEvents:
            return _FakeEvents()

    conn = GoogleCalendarConnector(
        cfg,
        oauth_adapter=oauth,
        calendar_service_factory=lambda creds: _FakeCalService(),
        rate_limiter=_fast_limiter(),
    )

    page = conn.list_changes(scope={"availability_only": True}, cursor="sync:stale_token")
    assert page.complete is True
    assert page.sync_token == "sync:fresh_sync_token_42"
    assert page.next_cursor == "sync:fresh_sync_token_42"
    assert any("HTTP 410" in w for w in page.warnings)
    assert len(page.items) == 3

    ev1, ev2, ev3 = page.items
    # Personal title and description are minimized/redacted
    assert ev1.title == "Busy"
    assert ev1.payload["description"] is None
    assert ev1.payload["start_at"] == "2026-12-03T13:00:00Z"
    assert ev2.payload["all_day_date"] == "2026-12-25"
    assert ev3.access_status == AccessStatus.TOMBSTONED

    # Availability intervals omit cancelled events and personal details
    ics_events = conn.import_ics_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:p1\r\nSUMMARY:Private\r\n"
        "DTSTART:20261204T100000Z\r\nDTEND:20261204T113000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    intervals = conn.extract_availability_intervals(ics_events)
    assert len(intervals) == 1
    assert intervals[0].status == "busy"
    assert intervals[0].start_at == "2026-12-04T10:00:00Z"


def test_local_file_connector_and_structured_academic_importer(tmp_path: Path) -> None:
    root = tmp_path / "study_root"
    sub = root / "2A" / "ML"
    sub.mkdir(parents=True)
    md_file = sub / "Chapitre1_Notes.md"
    md_file.write_text("# Chapitre 1\nRegression lineaire\n", encoding="utf-8")

    # Escaping symlink outside root must be rejected
    outside = tmp_path / "secret.txt"
    outside.write_text("do not read", encoding="utf-8")
    escape_link = sub / "escape.txt"
    escape_link.symlink_to(outside)

    conn = LocalFileConnector()
    page = conn.list_changes(scope={"root_path": root})
    assert len(page.items) == 1
    assert page.items[0].external_id.startswith("local:")
    assert page.items[0].external_id.endswith(":2A/ML/Chapitre1_Notes.md")
    assert any("Blocked" in w for w in page.warnings)

    # Repeat import with known_hashes yields zero items
    known = {page.items[0].external_id: page.items[0].content_hash}
    page2 = conn.list_changes(scope={"root_path": root, "known_hashes": known})
    assert len(page2.items) == 0

    # Structured academic YAML (exact nested schema from docs/GRADE_TRACKER.md)
    curriculum_yaml = """
academic_year: "2026-2027"
semester: 5
specialization: ID
units:
  - id: ue_data_science
    name: Data Science
    coefficient: 5
    subjects:
      - id: machine_learning
        name: Machine Learning
        coefficient: 3
        assessments:
          - {id: final_exam, weight: 0.5}
          - id: continuous_assessment
            weight: 0.5
            components:
              - {id: midterm, weight: 0.5, grade: 0}
              - id: quizzes
                weight: 0.5
                components:
                  - {id: qcm_1, weight: 0.125, grade: 16}
                  - {id: qcm_2, weight: 0.125}
"""
    importer = StructuredAcademicImporter()
    records = importer.import_yaml_text(curriculum_yaml)
    by_id = {r.external_id: r for r in records}
    # Parents appear strictly before children
    id_order = [r.external_id for r in records]
    assert id_order.index("ue_data_science") < id_order.index("machine_learning")
    assert id_order.index("continuous_assessment") < id_order.index("quizzes")
    assert id_order.index("quizzes") < id_order.index("qcm_1")
    # Zero vs missing distinction preserved
    assert by_id["midterm"].grade_value == Decimal("0")
    assert by_id["midterm"].grade_status == ValueStatus.CONFIRMED
    assert by_id["qcm_2"].grade_value is None
    assert by_id["qcm_2"].grade_status == ValueStatus.MISSING

    # Invalid fraction sum > 1 rejected
    bad_yaml = """
academic_year: "2026-2027"
units:
  - id: ue1
    subjects:
      - id: s1
        assessments:
          - {id: a1, weight: 0.7}
          - {id: a2, weight: 0.6}
"""
    with pytest.raises(ValueError):
        importer.import_yaml_text(bad_yaml)


def test_pamplemousse_assessment_markers_and_exact_curriculum_guard():
    from backend.app.modules.ingestion.connectors.pamplemousse import (
        classify_timetable_event, resolve_timetable_subject_code,
    )

    kind, confirmation, _ = classify_timetable_event("Sout.ID PPDP", group="G1")
    assert kind.value == "presentation" and confirmation
    kind, confirmation, _ = classify_timetable_event("Présentation des débouchés du logiciel SAS", color_hint="red")
    assert kind.value == "class" and not confirmation
    kind, confirmation, _ = classify_timetable_event("Ex. Cloud computing")
    assert kind.value == "exam" and confirmation
    assert classify_timetable_event("Cloud computing", description="Type de cours : Ex.")[0].value == "exam"
    assert classify_timetable_event("Cours Cloud computing", description="Préparation à l'examen")[0].value == "class"

    curriculum = [
        ("deep", "Apprentissage profond", "DL01"),
        ("stat", "Apprentissage statistique", "ST01"),
        ("large", "Apprentissage statistique à grande échelle", "ST02"),
        ("supervised", "Apprentissage supervisé", "SUP1"),
    ]
    assert resolve_timetable_subject_code("Deep Learning 3A groupe commun", None, curriculum) == "DL01"
    assert resolve_timetable_subject_code("CM Apprentissage statistique à grande échelle 3A ID", None, curriculum) == "ST02"
    assert resolve_timetable_subject_code("Supervised Learning", None, curriculum) == "SUP1"
    assert resolve_timetable_subject_code("Apprentissage statistique", "DL01", curriculum) is None
    assert resolve_timetable_subject_code("Deep Learning / Apprentissage supervisé", None, curriculum) is None
