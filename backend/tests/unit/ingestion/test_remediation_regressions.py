"""Targeted regression tests for Codex review findings GEMINI-REV-001 through GEMINI-REV-016."""

from __future__ import annotations

import io
import json
import os
import stat
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.contracts.authentication import ConnectionStatus, ReauthenticationRequired
from backend.app.contracts.sources import (
    Assessment,
    AssessmentWeight,
    CalendarEvent,
    Document,
    Provenance,
    PublishedGrade,
    RetryableSourceError,
    SourceKind as CodexSourceKind,
    Subject,
    SubjectCoefficient,
    SyncScope,
)
from backend.app.core.database import documents as db_documents, records as db_records
from backend.app.core.runtime import Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter
from backend.app.modules.ingestion.auth.google_cloud import CommandResult, GoogleCloudSetupHelper
from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
from backend.app.modules.ingestion.codex_bridge import (
    CodexClassifierBridge,
    CodexConnectorBridge,
    CodexEnsaiAuthBridge,
    CodexEntityMatcherBridge,
    CodexGoogleAuthBridge,
    CodexParserBridge,
    _to_codex_records,
    runtime_config_from_codex_settings,
)
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
)
from backend.app.modules.ingestion.connectors.ensora_drive import (
    DRIVE_FOLDER_MIME,
    EnsoraDriveConnector,
)
from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector
from backend.app.modules.ingestion.connectors.local_import import LocalFileConnector
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.contracts import (
    ChangePage as InternalChangePage,
    SourceKind,
    SourceRecord,
)
from backend.app.modules.ingestion.credentials import (
    InMemoryCredentialStore,
    SecureCredentialManager,
)
from backend.app.modules.ingestion.parsers.office_parser import DocxDocumentParser, PptxDocumentParser


class _DummyCreds:
    valid = True
    expired = False
    refresh_token = "rt"
    scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
    granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]


class _FakeExec:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


def test_rev001_ensora_drive_revalidates_moved_folder_and_rejects_trashed_download(tmp_path: Path) -> None:
    """GEMINI-REV-001: Revalidate live ancestry on moved folders and reject trashed files on download."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "fld_2a": {"id": "fld_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "sub_ml": {"id": "sub_ml", "name": "ML", "mimeType": DRIVE_FOLDER_MIME, "parents": ["fld_2a"], "trashed": False},
        "doc1": {
            "id": "doc1",
            "name": "CM1.pdf",
            "mimeType": "application/pdf",
            "parents": ["sub_ml"],
            "modifiedTime": "2026-11-01T10:00:00Z",
            "trashed": False,
        },
        "outside": {"id": "outside", "name": "Personal", "mimeType": DRIVE_FOLDER_MIME, "parents": ["other_root"], "trashed": False},
    }

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            pid = q.split("'")[1]
            return _FakeExec({"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]})

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(b"%PDF-1.4\n%%EOF")

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    conn = EnsoraDriveConnector(cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))

    # First discovery inside Ensora root succeeds
    page = conn.list_changes()
    assert len(page.items) == 1
    doc_rec = page.items[0]

    # Now simulate sub_ml moved outside the Ensora root after discovery!
    tree["sub_ml"]["parents"] = ["outside"]
    with pytest.raises(PermissionDeniedError):
        conn.list_changes(scope={"folder_id": "sub_ml"})
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(doc_rec)

    # Move back inside 2A, then trash the file -> fetch_blob must reject trashed file!
    tree["sub_ml"]["parents"] = ["fld_2a"]
    tree["doc1"]["trashed"] = True
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(doc_rec)


def test_rev002_google_calendar_multipage_incremental_and_initial_invariants(tmp_path: Path) -> None:
    """GEMINI-REV-002: Incremental multi-page keeps syncToken & singleEvents=True (omitting orderBy/timeMin); initial freezes window."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.GOOGLE_CALENDAR, credentials_loader=lambda p, s: _DummyCreds())

    recorded_calls: list[dict[str, Any]] = []

    class _Events:
        def list(self, **params: Any) -> _FakeExec:
            recorded_calls.append(dict(params))
            if params.get("pageToken") == "p2":
                return _FakeExec(
                    {
                        "timeZone": "Europe/Paris",
                        "nextSyncToken": "sync_after_p2",
                        "items": [
                            {
                                "id": "e2",
                                "status": "confirmed",
                                "summary": "Page 2",
                                "start": {"dateTime": "2026-12-02T10:00:00Z"},
                                "end": {"dateTime": "2026-12-02T11:00:00Z"},
                            }
                        ],
                    }
                )
            return _FakeExec(
                {
                    "timeZone": "Europe/Paris",
                    "nextPageToken": "p2",
                    "items": [
                        {
                            "id": "e1",
                            "status": "confirmed",
                            "summary": "Page 1",
                            "start": {"dateTime": "2026-12-01T10:00:00Z"},
                            "end": {"dateTime": "2026-12-01T11:00:00Z"},
                        }
                    ],
                }
            )

    class _Svc:
        def events(self) -> _Events:
            return _Events()

    conn = GoogleCalendarConnector(cfg, oauth_adapter=oauth, calendar_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))

    # 1. Incremental 2-page sync preserves syncToken and singleEvents=True (omitting only timeMin/timeMax/orderBy)
    p1 = conn.list_changes(cursor="sync:tok_v1")
    assert p1.complete is False and p1.next_cursor is not None
    p2 = conn.list_changes(cursor=p1.next_cursor)
    assert p2.complete is True and p2.next_cursor == "sync:sync_after_p2"
    assert recorded_calls[0]["syncToken"] == "tok_v1"
    assert recorded_calls[1]["syncToken"] == "tok_v1"
    assert recorded_calls[1]["pageToken"] == "p2"
    assert recorded_calls[0]["singleEvents"] is True
    assert recorded_calls[1]["singleEvents"] is True
    assert "timeMin" not in recorded_calls[1]
    assert "orderBy" not in recorded_calls[1]

    # 2. Initial 2-page sync freezes exact timeMin/timeMax across pages
    recorded_calls.clear()
    ip1 = conn.list_changes(cursor=None)
    ip2 = conn.list_changes(cursor=ip1.next_cursor)
    assert ip2.complete is True
    assert recorded_calls[0]["timeMin"] == recorded_calls[1]["timeMin"]
    assert recorded_calls[0]["timeMax"] == recorded_calls[1]["timeMax"]
    assert recorded_calls[1]["singleEvents"] is True


def test_rev003_rev004_cancelled_tombstones_missing_duration_and_privacy_fields() -> None:
    """GEMINI-REV-003 & REV-004: ID-only cancelled tombstone works, missing end never fabricates 1h, typed fields survive."""
    # 1. ID-only cancelled event converts to valid CalendarEvent tombstone
    cancelled_rec = SourceRecord(
        source_kind=SourceKind.GOOGLE_CALENDAR,
        external_id="google_calendar:primary:gone_1",
        title="Cancelled event",
        record_kind="event",
        payload={"status": "cancelled", "busy": False},
    )
    converted = _to_codex_records(cancelled_rec)
    assert len(converted) == 1
    assert isinstance(converted[0], CalendarEvent)
    assert converted[0].status == "cancelled"
    assert converted[0].starts_at is None and converted[0].ends_at is None

    # 2. Timed event missing end_at is isolated as conversion error (NO fabricated 1-hour duration!)
    incomplete_rec = SourceRecord(
        source_kind=SourceKind.PAMPLEMOUSSE,
        external_id="pamplemousse:event:bad_1",
        title="Incomplete slot",
        record_kind="event",
        payload={"status": "confirmed", "start_at": "2026-12-07T08:00:00Z", "end_at": None},
    )
    good_pamp = SourceRecord(
        source_kind=SourceKind.PAMPLEMOUSSE,
        external_id="pamplemousse:event:sout_1",
        title="Sout.ID PPDP",
        record_kind="event",
        payload={
            "status": "confirmed",
            "start_at": "2026-12-07T08:30:00Z",
            "end_at": "2026-12-07T10:30:00Z",
            "event_type": "presentation",
            "teacher": "Prof X",
            "confirmation_required": True,
            "confirmation_reason": "Presentation slot; confirm group.",
            "recurrence_key": "r1",
            "original_timezone": "Europe/Paris",
            "busy": True,
        },
    )

    class _FakeConn:
        source_kind = SourceKind.PAMPLEMOUSSE

        def list_changes(self, scope=None, cursor=None):
            return InternalChangePage(
                items=(incomplete_rec, good_pamp),
                next_cursor="durable_tok",
                complete=True,
            )

    bridge = CodexConnectorBridge(_FakeConn())
    page = bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE), None)
    # Healthy event survives; incomplete event isolated; durable cursor withheld because of error!
    assert len(page.items) == 1
    ev = page.items[0]
    assert isinstance(ev, CalendarEvent)
    assert ev.event_type == "presentation"
    assert ev.teacher == "Prof X"
    assert ev.confirmation_required is True
    assert ev.original_timezone == "Europe/Paris"
    assert page.next_cursor is None  # REV-006: checkpoint blocked on conversion error
    assert bridge.last_checkpoint_blocked is True

    # 3. GoogleCalendarConnector bridge defaults to availability_only=True
    seen_scope: dict[str, Any] = {}

    class _CalConn:
        source_kind = SourceKind.GOOGLE_CALENDAR

        def list_changes(self, scope=None, cursor=None):
            seen_scope.update(scope or {})
            return InternalChangePage(items=(), next_cursor="sync:ok", complete=True)

    cal_bridge = CodexConnectorBridge(_CalConn())
    cal_bridge.list_changes(SyncScope(source=CodexSourceKind.GOOGLE_CALENDAR), None)
    assert seen_scope.get("availability_only") is True


def test_rev005_rev006_moodle_parent_subject_academic_facts_and_error_checkpointing() -> None:
    """GEMINI-REV-005 & REV-006: Moodle emits parent Subject; academic facts keep independent weights/grades; missing grade never becomes Subject; Drive partial error blocks checkpoint."""
    # 1. Moodle assignment emits parent Subject before Assessment
    assign_rec = SourceRecord(
        source_kind=SourceKind.MOODLE,
        external_id="moodle:course:101:assignment:55",
        title="DM1 Regression",
        record_kind="assignment",
        course_id="101",
        course_title="Machine Learning 2A",
        payload={"deadline_at": "2026-12-10T23:59:00Z"},
    )
    moodle_facts = _to_codex_records(assign_rec, emitted_parents=set())
    assert len(moodle_facts) == 2
    assert isinstance(moodle_facts[0], Subject)
    assert moodle_facts[0].provenance.external_id == "moodle:course:101"
    assert isinstance(moodle_facts[1], Assessment)
    assert moodle_facts[1].parent_key == "moodle:course:101"

    # 2. Academic subject with coefficient emits Subject + SubjectCoefficient
    subj_rec = SourceRecord(
        source_kind=SourceKind.ACADEMIC_IMPORT,
        external_id="ml_subj",
        title="Machine Learning",
        record_kind="academic",
        payload={"record_type": "subject", "title": "Machine Learning", "subject_code": "ML", "weight": "3"},
    )
    subj_facts = _to_codex_records(subj_rec)
    assert isinstance(subj_facts[0], Subject)
    assert isinstance(subj_facts[1], SubjectCoefficient)
    assert subj_facts[1].weight == Decimal("3") and subj_facts[1].parent_key == "ml_subj"

    # 3. Weighted assessment with grade emits Assessment + AssessmentWeight + PublishedGrade
    assess_rec = SourceRecord(
        source_kind=SourceKind.ACADEMIC_IMPORT,
        external_id="midterm_1",
        title="Midterm",
        record_kind="academic",
        payload={
            "record_type": "assessment",
            "title": "Midterm",
            "parent_external_id": "ml_subj",
            "weight": "0.5",
            "weight_mode": "fraction",
            "grade_value": "14.5",
            "grade_scale_max": "20",
        },
    )
    a_facts = _to_codex_records(assess_rec)
    assert [type(x) for x in a_facts] == [Assessment, AssessmentWeight, PublishedGrade]

    # 4. Missing grade row NEVER falls through to Subject
    missing_grade = SourceRecord(
        source_kind=SourceKind.PAMPLEMOUSSE,
        external_id="g_missing",
        title="DL Missing Grade",
        record_kind="academic",
        payload={"record_type": "grade", "title": "DL Missing Grade", "grade_value": None},
    )
    assert _to_codex_records(missing_grade) == []

    # 5. Upstream folder listing errors with 0 items raise RetryableSourceError (not false success)
    class _BrokenDrive:
        source_kind = SourceKind.ENSORA_DRIVE

        def list_changes(self, scope=None, cursor=None):
            return InternalChangePage(
                items=(),
                next_cursor=None,
                complete=True,
                sync_token="2026-12-01T00:00:00Z",
                errors=("Error listing folder Ensora/2A: timeout",),
            )

    with pytest.raises(RetryableSourceError):
        CodexConnectorBridge(_BrokenDrive()).list_changes(SyncScope(source=CodexSourceKind.ENSORA_DRIVE), None)


def test_rev007_rev008_oauth_verified_grants_atomic_0600_and_remote_probe(tmp_path: Path) -> None:
    """GEMINI-REV-007 & REV-008: OAuth persists verified grants at 0o600 atomically, revokes via form body, and bridge probes remotely."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.ensure_private_directories()
    cfg.google_credentials_path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "cid.apps.googleusercontent.com",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        ),
        encoding="utf-8",
    )

    # Token file saved with ONLY calendar scope must NOT be overstated as having Drive scope
    cfg.google_token_path.write_text(
        json.dumps(
            {
                "token": "t",
                "refresh_token": "r",
                "client_id": "cid",
                "client_secret": "cs",
                "token_uri": "https://oauth2.googleapis.com/token",
                "granted_scopes": [GOOGLE_SCOPE_CALENDAR_READONLY],
                "scopes": [GOOGLE_SCOPE_CALENDAR_READONLY],
            }
        ),
        encoding="utf-8",
    )
    drive_oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE)
    assert drive_oauth.status().authenticated is False

    # Atomic save enforces 0o600
    class _SaveCreds:
        granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY]
        scopes = [GOOGLE_SCOPE_DRIVE_READONLY]

        def to_json(self) -> str:
            return json.dumps({"token": "abc", "refresh_token": "xyz"})

    drive_oauth._save_credentials(_SaveCreds())
    mode = stat.S_IMODE(os.stat(cfg.google_token_path).st_mode)
    assert mode == 0o600
    saved = json.loads(cfg.google_token_path.read_text(encoding="utf-8"))
    assert saved["granted_scopes"] == [GOOGLE_SCOPE_DRIVE_READONLY]

    # REV-008: CodexGoogleAuthBridge maps remote probe errors distinctly
    ok_oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _DummyCreds())
    assert CodexGoogleAuthBridge(ok_oauth, remote_probe=lambda: {"ok": True}).validate().status == ConnectionStatus.CONNECTED
    assert CodexGoogleAuthBridge(ok_oauth, remote_probe=lambda: (_ for _ in ()).throw(PermissionDeniedError("403"))).validate().status == ConnectionStatus.ACCESS_DENIED
    assert CodexGoogleAuthBridge(ok_oauth, remote_probe=lambda: (_ for _ in ()).throw(TransientSourceError("503"))).validate().status == ConnectionStatus.UNAVAILABLE
    with pytest.raises(ReauthenticationRequired):
        CodexGoogleAuthBridge(ok_oauth, remote_probe=lambda: (_ for _ in ()).throw(AuthenticationRequiredError("401"))).validate()


def test_rev009_office_parsers_reject_oversized_zip_expansion() -> None:
    """GEMINI-REV-009: PPTX and DOCX parsers enforce member count and expanded byte budgets."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "A" * 50_000)
    docx_parser = DocxDocumentParser(max_member_bytes=4096, max_total_bytes=8192)
    res = docx_parser.parse(buf.getvalue(), filename="bomb.docx", mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert res.error is not None
    assert res.extraction_quality == 0.0

    buf2 = io.BytesIO()
    with zipfile.ZipFile(buf2, "w") as zf:
        for i in range(10):
            zf.writestr(f"ppt/slides/slide{i + 1}.xml", "<sld/>")
    pptx_parser = PptxDocumentParser(max_members=5)
    res2 = pptx_parser.parse(buf2.getvalue(), filename="many.pptx", mime_type="application/vnd.openxmlformats-officedocument.presentationml.presentation")
    assert res2.error is not None


def test_rev010_moodle_rest_discovery_and_bounded_redirect_guarded_download(tmp_path: Path) -> None:
    """GEMINI-REV-010: Moodle REST acquisition works when configured, and fetch_blob checks redirects + streaming size."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path, environ={})
    cfg.moodle_ws_enabled = True
    cfg.max_download_bytes = 64
    store = InMemoryCredentialStore()
    creds = SecureCredentialManager(store)
    creds.store_moodle_ws_token("ws_tok_test")

    class _FakeResp:
        def __init__(self, *, json_data=None, chunks=None, url=None, headers=None, status_code=200):
            self._json = json_data
            self._chunks = chunks or []
            self.url = url or cfg.moodle_base_url
            self.headers = headers or {}
            self.status_code = status_code

        def json(self):
            return self._json

        def iter_content(self, chunk_size=65536):
            yield from self._chunks

    def fake_get(url: str, *, params: dict[str, Any] | None = None, stream: bool = False, timeout: float = 10.0):
        fn = (params or {}).get("wsfunction")
        if fn == "core_webservice_get_site_info":
            return _FakeResp(json_data={"userid": 7, "username": "etud"})
        if fn == "core_enrol_get_users_courses":
            return _FakeResp(json_data=[{"id": 101, "fullname": "Machine Learning"}])
        if fn == "core_course_get_contents":
            return _FakeResp(
                json_data=[
                    {
                        "name": "Semaine 1",
                        "modules": [
                            {
                                "id": 10,
                                "modname": "resource",
                                "name": "CM1.pdf",
                                "url": "https://foad-moodle.ensai.fr/mod/resource/view.php?id=10",
                                "contents": [
                                    {
                                        "filename": "CM1.pdf",
                                        "fileurl": "https://foad-moodle.ensai.fr/webservice/pluginfile.php/10/CM1.pdf",
                                        "mimetype": "application/pdf",
                                        "timemodified": 1700000000,
                                    }
                                ],
                            }
                        ],
                    }
                ]
            )
        if fn == "mod_assign_get_assignments":
            return _FakeResp(json_data={"courses": []})
        if "redirect_evil" in url:
            return _FakeResp(url="https://evil.example/payload.pdf", chunks=[b"x"])
        if "too_large" in url:
            return _FakeResp(url=url, chunks=[b"x" * 40, b"y" * 40])
        return _FakeResp(url=url, chunks=[b"%PDF-1.4\n%%EOF"], headers={"Content-Type": "application/pdf"})

    conn = MoodleConnector(cfg, credential_manager=creds, http_get=fake_get, rate_limiter=RateLimiter(0.0))
    page = conn.list_changes()
    assert len(page.items) == 1
    assert page.items[0].title == "CM1.pdf"

    # Normal bounded streaming download succeeds
    blob = conn.fetch_blob(page.items[0])
    assert blob.content.startswith(b"%PDF-")

    # Cross-origin redirect blocked
    redir_rec = conn.normalize({"course_id": "101", "module_id": "2", "title": "r", "url": "https://foad-moodle.ensai.fr/redirect_evil", "download_url": "https://foad-moodle.ensai.fr/redirect_evil"})
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(redir_rec)

    # Stream exceeding max_download_bytes blocked
    big_rec = conn.normalize({"course_id": "101", "module_id": "3", "title": "b", "url": "https://foad-moodle.ensai.fr/too_large", "download_url": "https://foad-moodle.ensai.fr/too_large"})
    with pytest.raises(ValueError):
        conn.fetch_blob(big_rec)


def test_rev011_local_cross_root_ids_fetch_containment_and_ics_bridge(tmp_path: Path) -> None:
    """GEMINI-REV-011: Distinct roots produce distinct IDs, fetch rechecks symlinks, and local ICS parses into CalendarEvents."""
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    root_a.mkdir()
    root_b.mkdir()
    (root_a / "notes.md").write_text("# A", encoding="utf-8")
    (root_b / "notes.md").write_text("# B", encoding="utf-8")
    (root_a / "agenda.ics").write_text(
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:loc1\r\nSUMMARY:Review\r\n"
        "DTSTART:20261205T090000Z\r\nDTEND:20261205T100000Z\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n",
        encoding="utf-8",
    )

    conn = LocalFileConnector()
    page_a = conn.list_changes(scope={"root_path": root_a})
    page_b = conn.list_changes(scope={"root_path": root_b})

    doc_a = next(r for r in page_a.items if r.record_kind == "document")
    doc_b = next(r for r in page_b.items if r.record_kind == "document")
    assert doc_a.external_id != doc_b.external_id

    # Replace file with symlink after discovery -> fetch_blob must reject it!
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    (root_a / "notes.md").unlink()
    (root_a / "notes.md").symlink_to(outside)
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(doc_a)

    # Local ICS flows cleanly through CodexConnectorBridge as CalendarEvent
    bridge = CodexConnectorBridge(conn)
    cpage = bridge.list_changes(SyncScope(source=CodexSourceKind.LOCAL, folder_id=str(root_a)), None)
    assert any(isinstance(item, CalendarEvent) and item.title == "Review" for item in cpage.items)


def test_rev012_rev013_rev014_rev015_bridge_parser_catalog_cli_and_settings(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """GEMINI-REV-012..015: Parser preserves block metadata, classifier uses parsed chunks, catalog-backed matcher hydrates SQLite, CLI redacts output, settings keep explicit paths & keyring fail disables auto-submit."""
    # REV-012: Parser preserves block kind/code_language & Classifier uses parsed chunk text
    doc = Document(
        provenance=Provenance(external_id="ensora_drive:f1", folder="Ensora/2A"),
        title="unnamed_file.md",
        mime_type="text/markdown",
    )
    md = b"# Synthese\n\nElements de correction et bareme indicatif.\n\n```python\ndef f(x):\n    return x\n```\n"
    parsed = CodexParserBridge().parse(doc, md)
    assert parsed is not None
    assert any(ch.locator.get("code_language") == "python" and ch.locator.get("block_kind") == "code" for ch in parsed.chunks)

    # Classifier uses parsed body text ("elements de correction" -> examination_correction)
    clf = CodexClassifierBridge().classify(doc, parsed)
    assert clf.resource_type == "examination_correction"

    # REV-013: Catalog-backed entity matcher hydrates persisted Subject records from Codex SQLite
    settings = Settings(
        config_dir=tmp_path / "cfg",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "cfg/sessions/ensai",
        moodle_auth_mode="rest",
        pamplemousse_auth_mode="browser",
        google_credentials_path=tmp_path / "explicit_missing_creds.json",
    )
    # REV-015: explicit missing path preserved verbatim + source auth mode carried
    rcfg = runtime_config_from_codex_settings(settings)
    assert rcfg.google_credentials_path == (tmp_path / "explicit_missing_creds.json").absolute()
    assert rcfg.moodle_auth_mode == "rest" and rcfg.moodle_ws_enabled is True

    with Runtime(settings) as rt:
        rt.consent.record(CodexSourceKind.ACADEMIC_IMPORT, "read", True)
        rt.pipeline.import_academics(
            rt.connection_ids[CodexSourceKind.ACADEMIC_IMPORT],
            [Subject(provenance=Provenance(external_id="subj_ppdp"), title="Privacy Publishing", code="PPDP")],
        )
        matcher = CodexEntityMatcherBridge()
        assert matcher.load_from_sqlite_engine(rt.engine) == 1
        m = matcher.match(
            Document(provenance=Provenance(external_id="moodle:course:1:resource:2"), title="CM1_PPDP.pdf", mime_type="application/pdf"),
            clf,
        )
        assert m.subject_id == "subj_ppdp" and m.confirmed is True

    # REV-015: Keyring write failure disables automated submit cleanly
    class _BrokenStore:
        def get_secret(self, ns, k): return None
        def set_secret(self, ns, k, v): raise RuntimeError("no keyring")
        def delete_secret(self, ns, k): pass

    seen_auto: list[bool | None] = []

    class _SpyEnsai:
        def authenticate(self, source, *, interactive=True, allow_automated_submit=None):
            seen_auto.append(allow_automated_submit)
            from backend.app.modules.ingestion.contracts import AuthState, AuthStatus
            return AuthStatus(source=source, state=AuthState.NEEDS_LOGIN, authenticated=False, detail="interactive")

    ebridge = CodexEnsaiAuthBridge(
        _SpyEnsai(),
        SourceKind.MOODLE,
        sessions=None,  # type: ignore[arg-type]
        cred_manager=SecureCredentialManager(_BrokenStore()),
    )
    ebridge.authorize({"username": SecretStr("u"), "password": SecretStr("p")})
    assert seen_auto == [False]
    assert ebridge.last_action_code == "keyring_unavailable_interactive_login_required"

    # REV-014: GoogleCloudSetupHelper masks developer email; CLI import-academic routes to unified Runtime & prints only redacted counters
    gcp = GoogleCloudSetupHelper(
        rcfg,
        which_fn=lambda name: "/usr/bin/gcloud" if name == "gcloud" else None,
        command_runner=lambda cmd: CommandResult(0, json.dumps([{"account": "alice.student@ensai.fr", "status": "ACTIVE"}]), "")
        if cmd[:3] == ["gcloud", "auth", "list"]
        else CommandResult(1, "", ""),
    )
    rep = gcp.inspect()
    assert rep.active_account == "a***@ensai.fr"
    assert "alice.student" not in json.dumps(rep.to_dict())

    acad_yaml = tmp_path / "curr.yaml"
    acad_yaml.write_text(
        "academic_year: '2026-2027'\nunits:\n  - id: ue1\n    name: UE Secret\n    subjects:\n      - id: s1\n        name: ML\n        coefficient: 3\n        assessments:\n          - {id: a1, weight: 1.0, grade: 17.5}\n",
        encoding="utf-8",
    )
    import scripts.p1_sources_cli as cli

    rc = cli.main(["--config-dir", str(tmp_path / "cfg"), "import-academic", "--path", str(acad_yaml), "--data-dir", str(tmp_path / "cli_data")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "17.5" not in out  # raw grade value never printed to stdout!
    assert '"imported":' in out


def test_rev017_rev018_rev019_rev020_and_new_codex_contract_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """GEMINI-REV-017..020 + Codex remediation contract additions (resume_cursor, error_codes, structured chunks, reauthorize, Concept extraction)."""
    from backend.app.modules.ingestion.auth.onboarding import OnboardingOrchestrator
    from backend.app.modules.ingestion.codex_bridge import extract_concept_candidates

    # REV-017: OnboardingOrchestrator.inspect_only(allow_network=False) performs zero network/browser calls
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "rev17_cfg", environ={})
    cfg.ensure_private_directories()
    (cfg.ensai_profile_dir / "Cookies").write_text("dummy", encoding="utf-8")

    def _forbid_browser(*args, **kwargs):
        raise AssertionError("inspect_only(allow_network=False) must NOT launch browser")

    ensai_no_net = EnsaiAuthAdapter(cfg, SecureCredentialManager(InMemoryCredentialStore()), context_factory=_forbid_browser)
    orch = OnboardingOrchestrator(cfg, ensai_auth=ensai_no_net)
    local_rep = orch.inspect_only(allow_network=False)
    assert local_rep.source_statuses["moodle"].metadata["local_only"] is True
    assert local_rep.source_statuses["moodle"].metadata["profile_present"] is True

    # REV-017: CLI validate is strictly consent-gated via Codex Runtime
    import scripts.p1_sources_cli as cli

    rc_val = cli.main(["--config-dir", str(tmp_path / "rev17_cfg"), "validate", "--source", "moodle", "--data-dir", str(tmp_path / "rev17_data")])
    assert rc_val == 0
    val_out = json.loads(capsys.readouterr().out)
    assert val_out["moodle"]["status"] == "ACCESS_DENIED"
    assert val_out["moodle"]["reason"] == "consent_required"

    # REV-018: Disconnecting one source on a shared profile preserves the shared browser profile
    ensai_shared = EnsaiAuthAdapter(cfg, SecureCredentialManager(InMemoryCredentialStore()))
    st_disc = ensai_shared.disconnect(SourceKind.MOODLE, clear_shared_profile=False)
    assert st_disc.metadata["shared_profile_cleared"] is False
    assert (cfg.ensai_profile_dir / "Cookies").exists()
    with pytest.raises(PermissionError):
        ensai_shared.clear_shared_session(confirmed=False)
    st_clear = ensai_shared.clear_shared_session(confirmed=True)
    assert st_clear.metadata["shared_profile_cleared"] is True
    assert not (cfg.ensai_profile_dir / "Cookies").exists()

    # REV-019: enable_required_apis accepts ONLY allowlisted exact missing subset
    seen_cmds: list[list[str]] = []
    gcp = GoogleCloudSetupHelper(
        cfg,
        which_fn=lambda name: "/usr/bin/gcloud" if name == "gcloud" else None,
        command_runner=lambda cmd: (seen_cmds.append(cmd), CommandResult(0, "ok", ""))[1],
    )
    gcp.enable_required_apis(approved=True, apis=("calendar-json.googleapis.com",))
    assert seen_cmds == [["gcloud", "services", "enable", "calendar-json.googleapis.com", "--project=ensora-516015"]]
    with pytest.raises(ValueError):
        gcp.enable_required_apis(approved=True, apis=("compute.googleapis.com",))

    # REV-020 + Codex contract fields: structured chunks, warning_codes, Provenance.source, Concept candidates, ChangePage.resume_cursor/error_codes
    doc = Document(
        provenance=Provenance(external_id="ensora_drive:doc99", source=CodexSourceKind.ENSORA_DRIVE, folder="Ensora/2A/ML"),
        title="CM1_ML.md",
        mime_type="text/markdown",
        subject_id="subj_ml",
    )
    md = b"# Chapitre 1\n\nTh\xc3\xa9or\xc3\xa8me 1 : Convergence du gradient stochastique.\n\nSoit $$L(\\theta) = \\frac{1}{n}\\sum_i \\ell_i(\\theta)$$.\n\n| Algo | Taux |\n|---|---|\n| SGD | O(1/t) |\n"
    parsed = CodexParserBridge().parse(doc, md)
    assert parsed is not None
    assert any(ch.block_kind == "table" and "table_rows" in ch.structured for ch in parsed.chunks)
    concepts = extract_concept_candidates(doc, parsed)
    assert any("Théorème 1" in c.title for c in concepts)
    assert any("Formula:" in c.title for c in concepts)
    assert all(c.parent_key == "subj_ml" and c.provenance.source == CodexSourceKind.ENSORA_DRIVE for c in concepts)

    # ChangePage carries resume_cursor, warning_codes, error_codes cleanly
    class _WarnConn:
        source_kind = SourceKind.ENSORA_DRIVE

        def list_changes(self, scope=None, cursor=None):
            rec = SourceRecord(
                source_kind=SourceKind.ENSORA_DRIVE,
                external_id="ensora_drive:f9",
                title="Notes.pdf",
                mime_type="application/pdf",
                folder_path="Ensora/2A/ML",
            )
            return InternalChangePage(
                items=(rec,),
                next_cursor=None,
                complete=True,
                sync_token="2026-12-01T12:00:00Z",
                warnings=("Skipped external shortcut",),
            )

    wpage = CodexConnectorBridge(_WarnConn()).list_changes(SyncScope(source=CodexSourceKind.ENSORA_DRIVE), None)
    assert wpage.resume_cursor == "2026-12-01T12:00:00Z"
    assert wpage.warning_codes == ["source_warning"]
    assert wpage.error_codes == []
    assert wpage.items[0].provenance.source == CodexSourceKind.ENSORA_DRIVE

    # CodexGoogleAuthBridge.reauthorize invokes oauth.reauthorize + remote probe
    reauth_called = {"n": 0}

    class _FakeOAuth:
        def reauthorize(self):
            reauth_called["n"] += 1
            from backend.app.modules.ingestion.contracts import AuthState, AuthStatus
            return AuthStatus(source=SourceKind.ENSORA_DRIVE, state=AuthState.AUTHENTICATED, authenticated=True, detail="ok")

    res_reauth = CodexGoogleAuthBridge(_FakeOAuth(), remote_probe=lambda: {"id": "root"}).reauthorize({})
    assert reauth_called["n"] == 1
    assert res_reauth.status == ConnectionStatus.CONNECTED
