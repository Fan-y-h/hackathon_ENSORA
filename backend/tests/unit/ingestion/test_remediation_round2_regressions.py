"""Targeted Round 2 regression tests for CODEX_REVIEW_ROUND_2 findings & new defects.

Directly reproduces and verifies:
- GEMINI-REV-002 & ROUND2-NEW-001 (Calendar singleEvents invariance & long opaque cursors)
- ROUND2-NEW-002 (Calendar HTTP 410 recovery retires stale availability via CalendarSnapshot)
- ROUND2-NEW-003 (Ensora Drive consumes drvmod: watermark & tombstones on validated full scan)
- ROUND2-NEW-004 (Conversion errors log constant codes only; zero sensitive content leaked)
- ROUND2-NEW-005 & GEMINI-REV-011 (Protected local roots & dirfd-relative symlink/FIFO containment)
- ROUND2-NEW-006 & GEMINI-REV-014 (Private export refuses symlinks & writes atomically 0o600)
- GEMINI-REV-005 (2-level nested academic weights & derived Provenance.source in SQLite)
- GEMINI-REV-007 (OAuth rejects requested-only & legacy scope-only tokens)
- GEMINI-REV-010 (Moodle pre-follow redirect validation & REST-only runtime auth without CAS)
- GEMINI-REV-012 (FetchedDocument metadata propagation into Codex ETL & page_size cap)
- GEMINI-REV-013 (Provenance.source precedence, consent-scoped overrides, persisted exam pairs)
- GEMINI-REV-017 (Standalone CLI onboard non-interactive local-only & interactive unified routing)
- GEMINI-REV-020 (Concept candidates carry ConceptEvidence chunk_ordinal/locator + prerequisite hints)
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus
from backend.app.contracts.sources import (
    Document,
    FetchedDocument,
    Provenance,
    SourceKind as CodexSourceKind,
    Subject,
    SyncScope,
)
from backend.app.core.database import (
    connections as db_connections,
    documents as db_documents,
    record_history as db_record_history,
    records as db_records,
    runs as db_runs,
    versions as db_versions,
)
from backend.app.core.runtime import Components, Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter
from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
from backend.app.modules.ingestion.auth.onboarding import OnboardingOrchestrator
from backend.app.modules.ingestion.codex_bridge import (
    MAX_PAGE_CACHE_ENTRIES,
    CodexClassifierBridge,
    CodexConnectorBridge,
    CodexEntityMatcherBridge,
    CodexMoodleRestAuthBridge,
    CodexParserBridge,
    _to_codex_records,
    build_components,
    extract_concept_candidates,
    extract_prerequisite_hints,
)
from backend.app.modules.ingestion.config import (
    DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID,
    GOOGLE_SCOPE_CALENDAR_READONLY,
    GOOGLE_SCOPE_DRIVE_READONLY,
    SourcesRuntimeConfig,
)
from backend.app.modules.ingestion.connectors.base import PermissionDeniedError, RateLimiter
from backend.app.modules.ingestion.connectors.ensora_drive import (
    DRIVE_FOLDER_MIME,
    EnsoraDriveConnector,
)
from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector
from backend.app.modules.ingestion.connectors.local_import import (
    LocalFileConnector,
    StructuredAcademicImporter,
)
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.contracts import (
    AccessStatus,
    ChangePage as InternalChangePage,
    FetchResult,
    SourceKind,
    SourceRecord,
)
from backend.app.modules.ingestion.credentials import (
    InMemoryCredentialStore,
    SecureCredentialManager,
)


class _VerifiedCreds:
    valid = True
    expired = False
    refresh_token = "rt"
    scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
    granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]


class _FakeExec:
    def __init__(self, result: Any = None, exc: Exception | None = None) -> None:
        self._result = result
        self._exc = exc

    def execute(self) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._result


class _AlwaysConnectedAuth:
    def authorize(self, inputs):
        return AuthenticationResult(status=ConnectionStatus.CONNECTED)

    def validate(self):
        return AuthenticationResult(status=ConnectionStatus.CONNECTED)

    def disconnect(self):
        return None


def _wait_for_job(rt: Runtime, job_id: str, timeout: float = 5.0) -> tuple[str, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with rt.engine.connect() as db:
            row = db.execute(select(db_runs.c.status, db_runs.c.report).where(db_runs.c.id == job_id)).one()
        if row.status not in ("queued", "running"):
            return row.status, row.report
        time.sleep(0.02)
    raise TimeoutError(f"Sync job {job_id} did not finish within {timeout}s")


def test_r2_calendar_long_opaque_cursors_and_single_events_invariant(tmp_path: Path) -> None:
    """GEMINI-REV-002 & ROUND2-NEW-001: Long 300-char Google tokens pass SyncEngine and keep singleEvents=True."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.GOOGLE_CALENDAR, credentials_loader=lambda p, s: _VerifiedCreds())

    long_page_tok = "P" * 300
    long_sync_in = "S" * 300
    long_sync_out = "N" * 300
    recorded_calls: list[dict[str, Any]] = []

    class _Events:
        def list(self, **params: Any) -> _FakeExec:
            recorded_calls.append(dict(params))
            if params.get("pageToken") == long_page_tok:
                return _FakeExec(
                    {
                        "timeZone": "Europe/Paris",
                        "nextSyncToken": long_sync_out,
                        "items": [
                            {
                                "id": "ev_p2",
                                "status": "confirmed",
                                "summary": "Personal Dentist",
                                "start": {"dateTime": "2026-12-03T14:00:00Z"},
                                "end": {"dateTime": "2026-12-03T15:00:00Z"},
                            }
                        ],
                    }
                )
            return _FakeExec(
                {
                    "timeZone": "Europe/Paris",
                    "nextPageToken": long_page_tok,
                    "items": [
                        {
                            "id": "ev_p1",
                            "status": "confirmed",
                            "summary": "Personal Lunch",
                            "start": {"dateTime": "2026-12-03T12:00:00Z"},
                            "end": {"dateTime": "2026-12-03T13:00:00Z"},
                        }
                    ],
                }
            )

    class _Svc:
        def events(self) -> _Events:
            return _Events()

    conn = GoogleCalendarConnector(cfg, oauth_adapter=oauth, calendar_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))
    bridge = CodexConnectorBridge(conn, calendar_availability_only=True)

    settings = Settings(config_dir=tmp_path / "cfg", local_data_dir=tmp_path / "data", ensai_profile_dir=tmp_path / "cfg/sessions/ensai")
    comps = Components(
        adapters={CodexSourceKind.GOOGLE_CALENDAR: _AlwaysConnectedAuth()},
        connectors={CodexSourceKind.GOOGLE_CALENDAR: bridge},
    )
    with Runtime(settings, components=comps) as rt:
        rt.consent.record(CodexSourceKind.GOOGLE_CALENDAR, "read", True)
        # Seed a long 300-char durable syncToken cursor before running incremental multi-page sync
        with rt.engine.begin() as db:
            db.execute(
                db_connections.update()
                .where(db_connections.c.id == rt.connection_ids[CodexSourceKind.GOOGLE_CALENDAR])
                .values(cursor=f"sync:{long_sync_in}")
            )
        job_id = rt.sync.submit(SyncScope(source=CodexSourceKind.GOOGLE_CALENDAR, full=False))
        status, _ = _wait_for_job(rt, job_id)
        assert status == "succeeded"

        # Both incremental pages preserved syncToken AND singleEvents=True, while omitting timeMin/orderBy
        assert len(recorded_calls) == 2
        assert recorded_calls[0]["syncToken"] == long_sync_in
        assert recorded_calls[1]["syncToken"] == long_sync_in
        assert recorded_calls[1]["pageToken"] == long_page_tok
        assert recorded_calls[0]["singleEvents"] is True
        assert recorded_calls[1]["singleEvents"] is True
        assert "timeMin" not in recorded_calls[0] and "timeMin" not in recorded_calls[1]
        assert "orderBy" not in recorded_calls[0] and "orderBy" not in recorded_calls[1]

        with rt.engine.connect() as db:
            saved_cursor = db.scalar(
                select(db_connections.c.cursor).where(
                    db_connections.c.id == rt.connection_ids[CodexSourceKind.GOOGLE_CALENDAR]
                )
            )
            ev_rows = db.execute(select(db_records.c.external_id, db_records.c.payload)).mappings().all()
        assert saved_cursor == f"sync:{long_sync_out}"
        assert {r["external_id"] for r in ev_rows} == {
            "google_calendar:primary:ev_p1",
            "google_calendar:primary:ev_p2",
        }
        # Privacy preserved: personal summaries replaced with anonymous "Busy"
        assert all(r["payload"]["title"] == "Busy" for r in ev_rows)


def test_r2_calendar_410_recovery_retires_stale_availability(tmp_path: Path) -> None:
    """ROUND2-NEW-002: HTTP 410 Gone replacement snapshot retires absent events from SQLite availability."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.GOOGLE_CALENDAR, credentials_loader=lambda p, s: _VerifiedCreds())

    class _Gone410(Exception):
        status_code = 410

    phase = {"step": 1}

    class _Events:
        def list(self, **params: Any) -> _FakeExec:
            if phase["step"] == 1:
                return _FakeExec(
                    {
                        "timeZone": "Europe/Paris",
                        "nextSyncToken": "sync_v1",
                        "items": [
                            {
                                "id": "ev_keep",
                                "status": "confirmed",
                                "summary": "Keep",
                                "start": {"dateTime": "2026-12-04T09:00:00Z"},
                                "end": {"dateTime": "2026-12-04T10:00:00Z"},
                            },
                            {
                                "id": "ev_stale",
                                "status": "confirmed",
                                "summary": "Deleted Later",
                                "start": {"dateTime": "2026-12-04T11:00:00Z"},
                                "end": {"dateTime": "2026-12-04T12:00:00Z"},
                            },
                        ],
                    }
                )
            # Phase 2: incremental syncToken fails with HTTP 410 -> replacement scan returns ONLY ev_keep
            if params.get("syncToken"):
                return _FakeExec(exc=_Gone410("Sync token expired"))
            return _FakeExec(
                {
                    "timeZone": "Europe/Paris",
                    "nextSyncToken": "sync_v2",
                    "items": [
                        {
                            "id": "ev_keep",
                            "status": "confirmed",
                            "summary": "Keep",
                            "start": {"dateTime": "2026-12-04T09:00:00Z"},
                            "end": {"dateTime": "2026-12-04T10:00:00Z"},
                        }
                    ],
                }
            )

    class _Svc:
        def events(self) -> _Events:
            return _Events()

    conn = GoogleCalendarConnector(cfg, oauth_adapter=oauth, calendar_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))
    bridge = CodexConnectorBridge(conn, calendar_availability_only=True)

    settings = Settings(config_dir=tmp_path / "cfg", local_data_dir=tmp_path / "data", ensai_profile_dir=tmp_path / "cfg/sessions/ensai")
    comps = Components(
        adapters={CodexSourceKind.GOOGLE_CALENDAR: _AlwaysConnectedAuth()},
        connectors={CodexSourceKind.GOOGLE_CALENDAR: bridge},
    )
    with Runtime(settings, components=comps) as rt:
        rt.consent.record(CodexSourceKind.GOOGLE_CALENDAR, "read", True)
        # 1. Initial sync writes both ev_keep and ev_stale
        st1, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.GOOGLE_CALENDAR, full=True)))
        assert st1 == "succeeded"

        # 2. Incremental sync hits 410 and recovers with bounded CalendarSnapshot containing only ev_keep
        phase["step"] = 2
        st2, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.GOOGLE_CALENDAR, full=False)))
        assert st2 == "succeeded"

        with rt.engine.connect() as db:
            rows = {
                r["external_id"]: r["payload"]
                for r in db.execute(select(db_records.c.external_id, db_records.c.payload)).mappings()
            }
            hist_count = len(db.execute(select(db_record_history.c.id)).all())

        assert rows["google_calendar:primary:ev_keep"]["status"] == "confirmed"
        assert rows["google_calendar:primary:ev_keep"]["busy"] is True
        assert rows["google_calendar:primary:ev_stale"]["status"] == "cancelled"
        assert rows["google_calendar:primary:ev_stale"]["busy"] is False
        # Historical audit trail preserved
        assert hist_count >= 3


def test_r2_drive_incremental_watermark_and_validated_tombstones(tmp_path: Path) -> None:
    """ROUND2-NEW-003: Ensora Drive consumes drvmod: cursor watermark and emits tombstones only on clean full scans."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID

    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "f_old": {
            "id": "f_old",
            "name": "Old.pdf",
            "mimeType": "application/pdf",
            "parents": [root_id],
            "modifiedTime": "2026-11-01T10:00:00Z",
            "trashed": False,
        },
        "f_gone": {
            "id": "f_gone",
            "name": "MovedAway.pdf",
            "mimeType": "application/pdf",
            "parents": [root_id],
            "modifiedTime": "2026-11-02T10:00:00Z",
            "trashed": False,
        },
    }

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            pid = q.split("'")[1]
            return _FakeExec({"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]})

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _VerifiedCreds())
    conn = EnsoraDriveConnector(cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))

    # 1. Full initial scan emits typed drvmod: watermark
    p1 = conn.list_changes()
    assert p1.complete is True
    assert p1.sync_token == "drvmod:2026-11-02T10:00:00Z"
    assert {r.external_id for r in p1.items} == {"ensora_drive:f_old", "ensora_drive:f_gone"}

    # 2. Add a newer file and run incremental scan passing cursor=p1.sync_token -> skips older files!
    tree["f_new"] = {
        "id": "f_new",
        "name": "New.pdf",
        "mimeType": "application/pdf",
        "parents": [root_id],
        "modifiedTime": "2026-11-10T12:00:00Z",
        "trashed": False,
    }
    p2 = conn.list_changes(cursor=p1.sync_token)
    assert p2.complete is True
    assert [r.external_id for r in p2.items] == ["ensora_drive:f_new"]
    assert p2.sync_token == "drvmod:2026-11-10T12:00:00Z"

    # 3. Trash f_gone and run validated full scan with known_external_ids -> emits tombstone for f_gone
    tree["f_gone"]["trashed"] = True
    p3 = conn.list_changes(scope={"known_external_ids": {"ensora_drive:f_old", "ensora_drive:f_gone", "ensora_drive:f_new"}})
    tombstones = [r for r in p3.items if r.access_status == AccessStatus.TOMBSTONED]
    assert [r.external_id for r in tombstones] == ["ensora_drive:f_gone"]


def test_r2_conversion_error_logging_redacts_sensitive_content(caplog: pytest.LogCaptureFixture) -> None:
    """ROUND2-NEW-004: Record conversion failures log constant codes only; zero sensitive content or IDs leaked."""
    bad_event = SourceRecord(
        source_kind=SourceKind.PAMPLEMOUSSE,
        external_id="pamplemousse:event:SECRET_STUDENT_ID_9999",
        title="PRIVATE_NOTE_XYZ",
        record_kind="event",
        payload={"status": "confirmed", "start_at": "2026-12-07T08:00:00Z", "end_at": None},
    )
    bad_grade = SourceRecord(
        source_kind=SourceKind.ACADEMIC_IMPORT,
        external_id="academic:grade:SECRET_STUDENT_ID_8888",
        title="SECRET_SUBJECT_TITLE",
        record_kind="academic",
        payload={
            "record_type": "grade",
            "title": "SECRET_SUBJECT_TITLE",
            "grade_value": "25.75",  # Exceeds max scale 20 -> Pydantic ValidationError echoing raw value!
            "grade_scale_max": "20",
        },
    )
    good_event = SourceRecord(
        source_kind=SourceKind.PAMPLEMOUSSE,
        external_id="pamplemousse:event:ok_1",
        title="Valid Slot",
        record_kind="event",
        payload={"status": "confirmed", "start_at": "2026-12-07T10:00:00Z", "end_at": "2026-12-07T11:00:00Z"},
    )

    class _FakeConn:
        source_kind = SourceKind.PAMPLEMOUSSE

        def list_changes(self, scope=None, cursor=None):
            return InternalChangePage(items=(bad_event, bad_grade, good_event), next_cursor="c1", complete=True)

    bridge = CodexConnectorBridge(_FakeConn())
    with caplog.at_level(logging.WARNING, logger="backend.app.modules.ingestion.codex_bridge"):
        page = bridge.list_changes(SyncScope(source=CodexSourceKind.PAMPLEMOUSSE), None)

    assert len(page.items) == 1
    assert set(page.error_codes) == {"incomplete_event_interval", "invalid_record_payload"}
    assert page.resume_cursor is None

    combined_logs = "\n".join(caplog.messages) + "\n" + "\n".join(bridge.last_page_errors)
    for secret in ("SECRET_STUDENT_ID_9999", "SECRET_STUDENT_ID_8888", "PRIVATE_NOTE_XYZ", "SECRET_SUBJECT_TITLE", "25.75"):
        assert secret not in combined_logs


def test_r2_local_protected_roots_and_dirfd_containment(tmp_path: Path) -> None:
    """ROUND2-NEW-005 & GEMINI-REV-011: Reject app private dirs, enclosing dirs, intermediate symlinks, and FIFOs."""
    cfg_dir = tmp_path / "app_cfg"
    data_dir = tmp_path / "app_data"
    secrets_dir = cfg_dir / "secrets"
    secrets_dir.mkdir(parents=True)
    data_dir.mkdir(parents=True)
    (secrets_dir / "dummy_token.json").write_text('{"dummy": true}', encoding="utf-8")
    (data_dir / "dummy.txt").write_text("internal", encoding="utf-8")

    settings = Settings(
        config_dir=cfg_dir,
        local_data_dir=data_dir,
        ensai_profile_dir=cfg_dir / "sessions" / "ensai",
        google_credentials_path=secrets_dir / "google_credentials.json",
        google_token_path=secrets_dir / "google_token.json",
    )
    comps = build_components(settings, credentials=None, sessions=None)  # type: ignore[arg-type]
    local_bridge = comps.connectors[CodexSourceKind.LOCAL]

    # 1. Protected config_dir, local_data_dir, secrets_dir, and enclosing parent must all be rejected!
    for forbidden in (cfg_dir, data_dir, secrets_dir, tmp_path):
        with pytest.raises(PermissionDeniedError):
            local_bridge.list_changes(SyncScope(source=CodexSourceKind.LOCAL, folder_id=str(forbidden)), None)

    # 2. Dirfd-relative open rejects intermediate directory symlink swap and non-regular FIFO
    user_root = tmp_path / "student_notes"
    sub = user_root / "chap1"
    sub.mkdir(parents=True)
    note = sub / "course.md"
    note.write_text("# Course", encoding="utf-8")

    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir()
    (outside_dir / "course.md").write_text("SECRET_OUTSIDE", encoding="utf-8")

    conn = LocalFileConnector(protected_roots=(cfg_dir, data_dir))
    page = conn.list_changes(scope={"root_path": user_root})
    rec = page.items[0]
    assert conn.fetch_blob(rec).content == b"# Course"

    # Swap intermediate directory `chap1` with a symlink pointing outside the root
    note.unlink()
    sub.rmdir()
    sub.symlink_to(outside_dir)
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(rec)

    # Replace target with a FIFO (non-regular file)
    sub.unlink()
    sub.mkdir()
    os.mkfifo(note)
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(rec)

    # 3. CLI import-academic rejects files inside protected config_dir via Runtime.validate_local_import_path
    import scripts.p1_sources_cli as cli

    protected_yaml = cfg_dir / "curriculum.yaml"
    protected_yaml.write_text("academic_year: '2026-2027'\nunits: []\n", encoding="utf-8")
    with pytest.raises(ValueError):
        cli.main(["--config-dir", str(cfg_dir), "import-academic", "--path", str(protected_yaml), "--data-dir", str(data_dir)])


def test_r2_private_export_refuses_symlinks_and_writes_atomic_0600(tmp_path: Path) -> None:
    """ROUND2-NEW-006 & GEMINI-REV-014: _write_private_export refuses symlinks and writes regular files atomically at 0o600."""
    import scripts.p1_sources_cli as cli

    victim = tmp_path / "victim.txt"
    victim.write_text("ORIGINAL_CONTENT", encoding="utf-8")
    sym = tmp_path / "export_link.json"
    sym.symlink_to(victim)

    with pytest.raises(PermissionError):
        cli._write_private_export(sym, {"leaked": True})
    assert victim.read_text(encoding="utf-8") == "ORIGINAL_CONTENT"

    out_file = tmp_path / "exports" / "safe.json"
    cli._write_private_export(out_file, {"status": "ok"})
    assert stat.S_IMODE(os.stat(out_file).st_mode) == 0o600
    assert json.loads(out_file.read_text(encoding="utf-8")) == {"status": "ok"}
    # No leftover temporary files
    assert list(out_file.parent.iterdir()) == [out_file]


def test_r2_nested_academic_weights_and_derived_source_provenance_in_sqlite(tmp_path: Path) -> None:
    """GEMINI-REV-005: 2-level nested assessment weights persist cleanly into SQLite with parent_id FKs and Provenance.source."""
    yaml_text = """
academic_year: "2026-2027"
units:
  - id: ue_stats
    name: UE Statistique
    coefficient: 6
    subjects:
      - id: subj_ml
        name: Machine Learning
        code: ML2A
        coefficient: 3
        assessments:
          - id: cc_group
            title: Controle Continu
            weight: 0.4
            weight_mode: fraction
            children:
              - id: cc_tp
                title: TP Note
                weight: 0.5
                weight_mode: fraction
                grade: 16.5
                grade_scale_max: 20
              - id: cc_qcm
                title: QCM
                weight: 0.5
                weight_mode: fraction
                grade: 14.0
                grade_scale_max: 20
          - id: final_exam
            title: Examen Final
            weight: 0.6
            weight_mode: fraction
"""
    curriculum_file = tmp_path / "nested.yaml"
    curriculum_file.write_text(yaml_text, encoding="utf-8")

    acad_records = StructuredAcademicImporter().import_file(curriculum_file)
    codex_items = []
    emitted: set[str] = set()
    for rec in acad_records:
        wrapped = SourceRecord(
            source_kind=SourceKind.ACADEMIC_IMPORT,
            external_id=rec.external_id,
            title=rec.title,
            record_kind="academic",
            payload=rec.to_dict(),
            provenance=rec.provenance,
        )
        codex_items.extend(_to_codex_records(wrapped, emitted_parents=emitted))

    settings = Settings(config_dir=tmp_path / "cfg", local_data_dir=tmp_path / "data", ensai_profile_dir=tmp_path / "cfg/sessions/ensai")
    with Runtime(settings) as rt:
        rt.consent.record(CodexSourceKind.ACADEMIC_IMPORT, "read", True)
        results = rt.pipeline.import_academics(
            rt.connection_ids[CodexSourceKind.ACADEMIC_IMPORT],
            codex_items,
        )
        assert all(r["status"] == "records" for r in results)

        with rt.engine.connect() as db:
            rows = db.execute(
                select(db_records.c.id, db_records.c.external_id, db_records.c.kind, db_records.c.parent_id, db_records.c.payload)
            ).mappings().all()

        by_ext = {r["external_id"]: r for r in rows}
        # Every persisted fact (including derived :coef, :weight, :grade) preserves provenance.source!
        assert len(by_ext) >= 10
        for ext_id, row in by_ext.items():
            assert row["payload"]["provenance"]["source"] == "academic_import", f"Missing source on {ext_id}"

        # Verify 2-level nested assessment & weight parent_id FK chain
        cc_group = by_ext["cc_group"]
        cc_group_w = by_ext["cc_group:weight"]
        cc_tp = by_ext["cc_tp"]
        cc_tp_w = by_ext["cc_tp:weight"]
        cc_tp_g = by_ext["cc_tp:grade"]

        assert cc_group["payload"]["format"] == "assessment_group"
        assert cc_group_w["parent_id"] == cc_group["id"]
        assert cc_tp["parent_id"] == cc_group["id"]
        assert cc_tp_w["parent_id"] == cc_tp["id"]
        assert cc_tp_g["parent_id"] == cc_tp["id"]
        assert Decimal(str(cc_tp_w["payload"]["weight"])) == Decimal("0.5")
        assert Decimal(str(cc_tp_g["payload"]["value"])) == Decimal("16.5")


def test_r2_oauth_rejects_requested_only_and_legacy_scope_only_tokens(tmp_path: Path) -> None:
    """GEMINI-REV-007: OAuth rejects requested-only Credentials objects and legacy scope-only JSON files."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
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

    # 1. Requested-only credential object (scopes set, granted_scopes absent) MUST NOT be treated as verified grant
    class _RequestedOnlyCreds:
        valid = True
        expired = False
        refresh_token = "rt"
        scopes = [GOOGLE_SCOPE_DRIVE_READONLY]
        granted_scopes = None

    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth_req_only = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: _RequestedOnlyCreds(),
    )
    assert oauth_req_only._granted_scopes(_RequestedOnlyCreds()) == ()
    assert oauth_req_only.status().authenticated is False

    # 2. Legacy token JSON with ONLY "scopes" (no "granted_scopes" / "verified_granted_scopes") MUST require reauth
    cfg.google_token_path.write_text(
        json.dumps(
            {
                "token": "t",
                "refresh_token": "r",
                "client_id": "cid",
                "client_secret": "cs",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [GOOGLE_SCOPE_DRIVE_READONLY],
            }
        ),
        encoding="utf-8",
    )
    oauth_legacy = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE)
    assert oauth_legacy.status().authenticated is False

    # 3. Verified save persists verified_granted_scopes + granted_scopes at 0o600
    class _VerifiedSaveCreds:
        granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY]
        scopes = [GOOGLE_SCOPE_DRIVE_READONLY]

        def to_json(self) -> str:
            return json.dumps({"token": "tok", "refresh_token": "ref", "client_id": "cid", "client_secret": "cs", "token_uri": "https://oauth2.googleapis.com/token"})

    oauth_legacy._save_credentials(_VerifiedSaveCreds())
    saved = json.loads(cfg.google_token_path.read_text(encoding="utf-8"))
    assert saved["verified_granted_scopes"] == [GOOGLE_SCOPE_DRIVE_READONLY]
    assert saved["granted_scopes"] == [GOOGLE_SCOPE_DRIVE_READONLY]
    assert stat.S_IMODE(os.stat(cfg.google_token_path).st_mode) == 0o600


def test_r2_moodle_prefollow_redirect_guard_and_rest_only_runtime_auth(tmp_path: Path) -> None:
    """GEMINI-REV-010: Moodle blocks cross-origin 302 BEFORE requesting evil host; REST-only runtime uses REST auth without CAS."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    cfg.moodle_ws_enabled = True
    store = InMemoryCredentialStore()
    creds = SecureCredentialManager(store)
    creds.store_moodle_ws_token("ws_tok_123")

    requested_urls: list[str] = []
    allow_redirects_flags: list[bool | None] = []

    class _FakeResp:
        def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, chunks: list[bytes] | None = None, json_data: Any = None, url: str = "") -> None:
            self.status_code = status_code
            self.headers = headers or {}
            self._chunks = chunks or []
            self._json = json_data
            self.url = url

        def json(self) -> Any:
            return self._json

        def iter_content(self, chunk_size: int = 65536):
            yield from self._chunks

    def fake_get(url: str, *, params=None, stream=False, timeout=10.0, allow_redirects=None):
        requested_urls.append(url)
        allow_redirects_flags.append(allow_redirects)
        if "evil_redir" in url:
            return _FakeResp(status_code=302, headers={"Location": "https://evil.example/leak.pdf"}, url=url)
        if "safe_redir" in url:
            return _FakeResp(status_code=302, headers={"Location": "/webservice/pluginfile.php/10/CM1.pdf"}, url=url)
        if "pluginfile.php" in url:
            return _FakeResp(status_code=200, headers={"Content-Type": "application/pdf"}, chunks=[b"%PDF-1.4"], url=url)
        if (params or {}).get("wsfunction") == "core_webservice_get_site_info":
            return _FakeResp(status_code=200, json_data={"userid": 9, "username": "etud"}, url=url)
        return _FakeResp(status_code=404, url=url)

    def _forbid_browser(*args, **kwargs):
        raise AssertionError("REST-only Moodle auth must NOT launch Playwright browser")

    ensai_auth = EnsaiAuthAdapter(cfg, creds, context_factory=_forbid_browser)
    conn = MoodleConnector(cfg, auth_adapter=ensai_auth, credential_manager=creds, http_get=fake_get, rate_limiter=RateLimiter(0.0))

    # 1. Cross-origin 302 rejected BEFORE any request to evil.example
    evil_rec = conn.normalize({"course_id": "10", "module_id": "1", "title": "x", "url": "https://foad-moodle.ensai.fr/evil_redir", "download_url": "https://foad-moodle.ensai.fr/evil_redir"})
    with pytest.raises(PermissionDeniedError):
        conn.fetch_blob(evil_rec)
    assert all("evil.example" not in u for u in requested_urls)
    assert allow_redirects_flags[0] is False

    # 2. Same-origin 302 followed cleanly
    safe_rec = conn.normalize({"course_id": "10", "module_id": "2", "title": "CM1.pdf", "url": "https://foad-moodle.ensai.fr/safe_redir", "download_url": "https://foad-moodle.ensai.fr/safe_redir"})
    res = conn.fetch_blob(safe_rec)
    assert res.content == b"%PDF-1.4"

    # 3. REST-only auth bridge validates via WS token without touching Playwright/CAS
    rest_bridge = CodexMoodleRestAuthBridge(conn, creds)
    assert rest_bridge.validate().status == ConnectionStatus.CONNECTED
    rest_bridge.disconnect()
    assert rest_bridge.validate().status == ConnectionStatus.NOT_CONFIGURED

    # 4. build_components selects CodexMoodleRestAuthBridge when moodle_auth_mode="rest"
    settings = Settings(
        config_dir=tmp_path / "cfg",
        local_data_dir=tmp_path / "data",
        ensai_profile_dir=tmp_path / "cfg/sessions/ensai",
        moodle_auth_mode="rest",
    )
    comps = build_components(settings, credentials=None, sessions=None)  # type: ignore[arg-type]
    assert isinstance(comps.adapters[CodexSourceKind.MOODLE], CodexMoodleRestAuthBridge)


def test_r2_bridge_fetched_document_metadata_and_page_size_cap(tmp_path: Path) -> None:
    """GEMINI-REV-012: fetch_blob returns FetchedDocument carrying exported MIME/basename into Codex ETL; page_size bounded."""
    seen_scope: dict[str, Any] = {}

    class _ExportDrive:
        source_kind = SourceKind.ENSORA_DRIVE

        def list_changes(self, scope=None, cursor=None):
            seen_scope.update(scope or {})
            return InternalChangePage(
                items=(
                    SourceRecord(
                        source_kind=SourceKind.ENSORA_DRIVE,
                        external_id="ensora_drive:gdoc_1",
                        title="Lecture Notes",
                        mime_type="application/vnd.google-apps.document",
                        remote_version="v1",
                    ),
                ),
                next_cursor=None,
                complete=True,
                sync_token="drvmod:2026-11-15T10:00:00Z",
            )

        def fetch_blob(self, item: SourceRecord) -> FetchResult:
            content = b"# Lecture Notes\n\nDefinition 1: Linear regression."
            return FetchResult(
                external_id=item.external_id,
                content=content,
                content_hash="0" * 64,
                mime_type="text/markdown",
                size_bytes=len(content),
                filename="nested/dir/Lecture_Notes.md",
            )

    bridge = CodexConnectorBridge(_ExportDrive())
    page = bridge.list_changes(SyncScope(source=CodexSourceKind.ENSORA_DRIVE), None)
    assert seen_scope["page_size"] == MAX_PAGE_CACHE_ENTRIES

    fetched = bridge.fetch_blob(page.items[0])
    assert isinstance(fetched, FetchedDocument)
    assert fetched.mime_type == "text/markdown"
    assert fetched.filename == "Lecture_Notes.md"  # normalized to basename!

    # End-to-end persistence into Codex Pipeline updates stored version mime_type & document title
    settings = Settings(config_dir=tmp_path / "cfg", local_data_dir=tmp_path / "data", ensai_profile_dir=tmp_path / "cfg/sessions/ensai")
    with Runtime(settings) as rt:
        rt.consent.record(CodexSourceKind.ENSORA_DRIVE, "read", True)
        res = rt.pipeline.ingest(
            rt.connection_ids[CodexSourceKind.ENSORA_DRIVE],
            page.items[0],
            fetch_blob=bridge.fetch_blob,
        )
        assert res == "documents"
        with rt.engine.connect() as db:
            doc_row = db.execute(select(db_documents.c.metadata_json)).mappings().one()
            ver_row = db.execute(select(db_versions.c.mime_type, db_versions.c.status)).mappings().one()
        assert doc_row["metadata_json"]["title"] == "Lecture_Notes.md"
        assert ver_row["mime_type"] == "text/markdown"
        assert ver_row["status"] == "ready"


def test_r2_matcher_provenance_source_consent_and_persisted_exam_pairs(tmp_path: Path) -> None:
    """GEMINI-REV-013: Matcher uses Provenance.source first, scopes overrides by consent, and finds persisted exam pairs."""
    settings = Settings(config_dir=tmp_path / "cfg", local_data_dir=tmp_path / "data", ensai_profile_dir=tmp_path / "cfg/sessions/ensai")
    with Runtime(settings) as rt:
        rt.consent.record(CodexSourceKind.ACADEMIC_IMPORT, "read", True)
        rt.consent.record(CodexSourceKind.LOCAL, "read", True)

        rt.pipeline.import_academics(
            rt.connection_ids[CodexSourceKind.ACADEMIC_IMPORT],
            [Subject(provenance=Provenance(external_id="subj_ml"), title="Machine Learning", code="ML")],
        )

        # Persist an exam and its correction via Pipeline
        exam_doc = Document(
            provenance=Provenance(external_id="opaque_exam_1", source=CodexSourceKind.LOCAL, folder="2A/ML"),
            title="Partiel_2025_ML.md",
            mime_type="text/markdown",
        )
        corr_doc = Document(
            provenance=Provenance(external_id="opaque_corr_1", source=CodexSourceKind.LOCAL, folder="2A/ML"),
            title="Corrige_Partiel_2025_ML.md",
            mime_type="text/markdown",
        )
        rt.pipeline.ingest(
            rt.connection_ids[CodexSourceKind.LOCAL],
            exam_doc,
            fetch_blob=lambda _: b"# Partiel 2025 Machine Learning\n\nExercice 1 (bar\xc3\xa8me 10 pts).",
        )
        rt.pipeline.ingest(
            rt.connection_ids[CodexSourceKind.LOCAL],
            corr_doc,
            fetch_blob=lambda _: b"# Corrig\xc3\xa9 Partiel 2025 Machine Learning\n\nEl\xc3\xa9ments de correction.",
        )

        matcher = CodexEntityMatcherBridge()
        # 1. Provenance.source precedence works even with opaque external_id
        loaded = matcher.load_from_sqlite_engine(
            rt.engine, consent_manager=rt.consent, connection_ids=rt.connection_ids
        )
        assert loaded == 1
        m = matcher.match(exam_doc, CodexClassifierBridge().classify(exam_doc, None))
        assert m.subject_id == "subj_ml"

        # 2. Persisted exam-to-correction pair discovery works when consented
        pairs = matcher.find_exam_correction_pairs_from_engine(
            rt.engine, consent_manager=rt.consent, connection_ids=rt.connection_ids
        )
        assert len(pairs) == 1
        assert pairs[0].exam_external_id == "opaque_exam_1"
        assert pairs[0].correction_external_id == "opaque_corr_1"

        # 3. Withdrawing LOCAL consent excludes persisted exam pairs & overrides
        rt.consent.record(CodexSourceKind.LOCAL, "read", False)
        assert (
            matcher.find_exam_correction_pairs_from_engine(
                rt.engine, consent_manager=rt.consent, connection_ids=rt.connection_ids
            )
            == []
        )


def test_r2_cli_onboard_non_interactive_local_only_and_interactive_routes_shared(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GEMINI-REV-017: CLI onboard --non-interactive is strictly local-only; interactive routes to shared onboarding."""
    import scripts.onboarding as unified_onboarding
    import scripts.p1_sources_cli as cli

    cfg_dir = tmp_path / "cfg"
    data_dir = tmp_path / "data"

    # Forbid OnboardingOrchestrator.run_setup from being called by --non-interactive
    def _forbid_run_setup(*args, **kwargs):
        raise AssertionError("CLI onboard --non-interactive must NOT invoke run_setup()")

    monkeypatch.setattr(OnboardingOrchestrator, "run_setup", _forbid_run_setup)

    rc_non = cli.main(["--config-dir", str(cfg_dir), "onboard", "--non-interactive"])
    assert rc_non == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_statuses"]["moodle"]["metadata"]["local_only"] is True
    assert (cfg_dir / "sources.env").exists()

    # Interactive onboard routes directly through Codex's unified scripts.onboarding.run_onboarding
    called = {"count": 0}

    def _spy_run_onboarding(runtime):
        called["count"] += 1
        assert runtime.settings.config_dir == cfg_dir.absolute()

    monkeypatch.setattr(unified_onboarding, "run_onboarding", _spy_run_onboarding)
    rc_int = cli.main(["--config-dir", str(cfg_dir), "onboard", "--data-dir", str(data_dir)])
    assert rc_int == 0
    assert called["count"] == 1


def test_r2_concept_candidates_chunk_evidence_and_prerequisite_hints() -> None:
    """GEMINI-REV-020: Concept candidates carry exact ConceptEvidence(chunk_ordinal, locator) and prerequisite hints."""
    doc = Document(
        provenance=Provenance(
            external_id="ensora_drive:ml_ch2",
            remote_revision="rev_42",
            source=CodexSourceKind.ENSORA_DRIVE,
            folder="Ensora/2A/ML",
        ),
        title="CM2_Optimisation.md",
        mime_type="text/markdown",
        subject_id="subj_ml",
    )
    md = (
        "# Chapitre 2 : Optimisation Convexe\n\n"
        "Prérequis : Calcul différentiel 1A et algèbre linéaire.\n\n"
        "Théorème 2.1 : Convergence de la descente de gradient convexe.\n\n"
        "Formule : $$\\theta_{t+1} = \\theta_t - \\eta \\nabla L(\\theta_t)$$\n"
    ).encode("utf-8")

    parsed = CodexParserBridge().parse(doc, md)
    assert parsed is not None
    concepts = extract_concept_candidates(doc, parsed)
    assert len(concepts) >= 2

    thm = next(c for c in concepts if "Théorème 2.1" in c.title)
    formula = next(c for c in concepts if c.title.startswith("Formula:"))

    for concept in (thm, formula):
        assert concept.provenance.source == CodexSourceKind.ENSORA_DRIVE
        assert concept.provenance.remote_revision == "rev_42"
        assert len(concept.evidence) == 1
        ev = concept.evidence[0]
        assert 0 <= ev.chunk_ordinal < len(parsed.chunks)
        assert ev.locator == parsed.chunks[ev.chunk_ordinal].locator

    # Verify exact chunk correspondence
    assert "Théorème 2.1" in parsed.chunks[thm.evidence[0].chunk_ordinal].text

    hints = extract_prerequisite_hints(parsed)
    assert len(hints) == 1
    assert "Calcul différentiel 1A" in str(hints[0]["text"])
    assert 0 <= int(hints[0]["chunk_ordinal"]) < len(parsed.chunks)
