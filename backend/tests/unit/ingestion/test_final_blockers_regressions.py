"""Targeted regression tests for final blocking defects FINAL-BLOCK-001 through FINAL-BLOCK-003.

Directly reproduces and verifies:
- FINAL-BLOCK-001 (GEMINI-REV-007): OAuth fresh provider-reported grant takes precedence over
  stale persisted `_verified_granted_scopes` marker after token refresh (narrowed/cleared grants).
- FINAL-BLOCK-002 (GEMINI-REV-010): Moodle REST token validation (`_validate_ws_token`) and
  metadata listing (`_ws_call`) enforce pre-follow same-origin redirect validation (`allow_redirects=False`).
- FINAL-BLOCK-003 (ROUND2-NEW-003): Ensora Drive preserves watermark/listing context across
  continuation pages without duplicates, reconciles moved/deleted cached files into tombstones
  removing API visibility in default Runtime, and disables reconciliation on depth truncation or errors.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus
from backend.app.contracts.sources import SourceKind as CodexSourceKind, SyncScope
from backend.app.core.database import (
    connections as db_connections,
    documents as db_documents,
    runs as db_runs,
    versions as db_versions,
)
from backend.app.core.runtime import Runtime
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.api import create_app
from backend.app.modules.ingestion.auth.google_oauth import GoogleOAuthAdapter
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
from backend.app.modules.ingestion.connectors.moodle import MoodleConnector
from backend.app.modules.ingestion.contracts import AuthState, SourceKind
from backend.app.modules.ingestion.credentials import (
    InMemoryCredentialStore,
    SecureCredentialManager,
)


class _FakeExec:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return self._result


class _VerifiedCreds:
    valid = True
    expired = False
    refresh_token = "rt"
    scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
    granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]


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


def test_final_block_001_oauth_narrowed_grant_after_refresh_rejected(tmp_path: Path) -> None:
    """FINAL-BLOCK-001: Fresh provider-reported grant overrides stale verified marker after refresh."""
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
    cfg.google_token_path.write_text(
        json.dumps(
            {
                "token": "old_tok",
                "refresh_token": "rt_1",
                "verified_granted_scopes": [
                    GOOGLE_SCOPE_DRIVE_READONLY,
                    GOOGLE_SCOPE_CALENDAR_READONLY,
                ],
            }
        ),
        encoding="utf-8",
    )

    # 1. Direct conflicting grants probe: stale _verified_granted_scopes has Drive, fresh granted_scopes has Calendar only
    class _ConflictingCreds:
        valid = True
        expired = False
        refresh_token = "rt_1"
        _verified_granted_scopes = (GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY)
        granted_scopes = [GOOGLE_SCOPE_CALENDAR_READONLY]
        scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]

    drive_oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: _ConflictingCreds(),
    )
    assert drive_oauth._granted_scopes(_ConflictingCreds()) == (GOOGLE_SCOPE_CALENDAR_READONLY,)
    assert drive_oauth.has_required_scopes(_ConflictingCreds()) is False

    # 2. Refresh narrows granted_scopes from (Drive, Calendar) -> (Calendar,) only!
    class _NarrowingRefreshCreds:
        def __init__(self) -> None:
            self.valid = False
            self.expired = True
            self.refresh_token = "rt_1"
            self._verified_granted_scopes = (
                GOOGLE_SCOPE_DRIVE_READONLY,
                GOOGLE_SCOPE_CALENDAR_READONLY,
            )
            self.granted_scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
            self.scopes = [GOOGLE_SCOPE_DRIVE_READONLY, GOOGLE_SCOPE_CALENDAR_READONLY]
            self.refreshed = False

        def refresh(self, req: Any) -> None:
            self.refreshed = True
            self.valid = True
            self.expired = False
            # Provider narrowed grant on refresh: Drive was revoked!
            self.granted_scopes = [GOOGLE_SCOPE_CALENDAR_READONLY]

        def to_json(self) -> str:
            return json.dumps({"token": "new_tok", "refresh_token": self.refresh_token})

    # 2a. status() refreshes, updates verified marker, clears Drive from disk, and returns NEEDS_LOGIN
    creds_status = _NarrowingRefreshCreds()
    drive_status_oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: creds_status,
        request_factory=lambda: object(),
    )
    st = drive_status_oauth.status()
    assert creds_status.refreshed is True
    assert st.authenticated is False
    assert st.state == AuthState.NEEDS_LOGIN
    assert st.scopes == (GOOGLE_SCOPE_CALENDAR_READONLY,)
    assert creds_status._verified_granted_scopes == (GOOGLE_SCOPE_CALENDAR_READONLY,)
    persisted = json.loads(cfg.google_token_path.read_text(encoding="utf-8"))
    assert persisted["verified_granted_scopes"] == [GOOGLE_SCOPE_CALENDAR_READONLY]
    assert persisted["granted_scopes"] == [GOOGLE_SCOPE_CALENDAR_READONLY]

    # 2b. get_credentials(allow_refresh=True, interactive=False) also rejects narrowed refresh
    creds_get = _NarrowingRefreshCreds()
    drive_get_oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.ENSORA_DRIVE,
        credentials_loader=lambda p, s: creds_get,
        request_factory=lambda: object(),
    )
    assert drive_get_oauth.get_credentials(allow_refresh=True, interactive=False) is None
    assert creds_get.refreshed is True
    assert creds_get._verified_granted_scopes == (GOOGLE_SCOPE_CALENDAR_READONLY,)

    # 2c. Calendar OAuth STILL succeeds on the narrowed Calendar-only token
    cal_oauth = GoogleOAuthAdapter(
        cfg,
        source=SourceKind.GOOGLE_CALENDAR,
        credentials_loader=lambda p, s: creds_status,
    )
    assert cal_oauth.status().authenticated is True

    # 3. Empty fresh granted_scopes clears persisted verified grants completely
    creds_empty = _NarrowingRefreshCreds()
    creds_empty.granted_scopes = []
    creds_empty.valid = True
    creds_empty.expired = False
    drive_get_oauth._save_credentials(creds_empty)
    persisted_empty = json.loads(cfg.google_token_path.read_text(encoding="utf-8"))
    assert "verified_granted_scopes" not in persisted_empty
    assert "granted_scopes" not in persisted_empty


def test_final_block_002_moodle_rest_validation_and_listing_enforce_prefollow_redirects(
    tmp_path: Path,
) -> None:
    """FINAL-BLOCK-002: Moodle REST token validation and metadata listing disable redirects and block cross-origin hops."""
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg", environ={})
    cfg.moodle_ws_enabled = True
    cfg.moodle_auth_mode = "rest"
    creds = SecureCredentialManager(InMemoryCredentialStore())
    creds.store_moodle_ws_token("ws_secret_token")

    calls: list[tuple[str, bool | None]] = []
    mode = {"scenario": "evil_validate"}

    class _FakeResp:
        def __init__(self, *, status_code: int = 200, headers: dict[str, str] | None = None, json_data: Any = None, url: str = "") -> None:
            self.status_code = status_code
            self.headers = headers or {}
            self._json = json_data
            self.url = url

        def json(self) -> Any:
            return self._json

    def fake_get(url: str, *, params=None, stream=False, timeout=10.0, allow_redirects=None):
        calls.append((url, allow_redirects))
        if mode["scenario"] == "evil_validate":
            return _FakeResp(
                status_code=302,
                headers={"Location": "https://evil.example/harvest?wstoken=leak"},
                url=url,
            )
        if mode["scenario"] == "evil_listing":
            return _FakeResp(
                status_code=301,
                headers={"Location": "https://evil.example/courses"},
                url=url,
            )
        if mode["scenario"] == "same_origin_hop":
            if url == "https://foad-moodle.ensai.fr/webservice/rest/server.php":
                return _FakeResp(
                    status_code=302,
                    headers={"Location": "/moodle/webservice/rest/server.php"},
                    url=url,
                )
            return _FakeResp(
                status_code=200,
                json_data={"userid": 42, "username": "student"},
                url=url,
            )
        return _FakeResp(status_code=404, url=url)

    conn = MoodleConnector(cfg, credential_manager=creds, http_get=fake_get, rate_limiter=RateLimiter(0.0))

    # 1. _validate_ws_token disables redirects and rejects cross-origin Location BEFORE following
    mode["scenario"] = "evil_validate"
    st = conn._validate_ws_token()
    assert st.authenticated is False
    assert st.metadata.get("errorcode") == "cross_origin_redirect"
    assert calls[-1][1] is False
    assert all("evil.example" not in u for u, _ in calls)

    # 2. _ws_call / discover_courses disables redirects and raises PermissionDeniedError BEFORE following
    calls.clear()
    mode["scenario"] = "evil_listing"
    with pytest.raises(PermissionDeniedError):
        conn.discover_courses()
    assert calls[-1][1] is False
    assert all("evil.example" not in u for u, _ in calls)

    # 3. Same-origin redirect on REST endpoint is safely followed and returns site info
    calls.clear()
    mode["scenario"] = "same_origin_hop"
    st_ok = conn._validate_ws_token()
    assert st_ok.authenticated is True
    assert len(calls) == 2
    assert all(flag is False for _, flag in calls)
    assert calls[1][0].endswith("/moodle/webservice/rest/server.php")


def test_final_block_003_drive_multipage_watermark_and_persisted_root_reconciliation(
    tmp_path: Path,
) -> None:
    """FINAL-BLOCK-003: Drive pagination preserves watermark without duplicates, reconciles moved/deleted files in Runtime/API, and skips reconciliation on depth truncation."""
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "sub_2a": {"id": "sub_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f1_stay": {
            "id": "f1_stay",
            "name": "CM1.md",
            "mimeType": "text/markdown",
            "parents": ["sub_2a"],
            "modifiedTime": "2026-11-01T10:00:00Z",
            "trashed": False,
        },
        "f2_moved": {
            "id": "f2_moved",
            "name": "CM2.md",
            "mimeType": "text/markdown",
            "parents": ["sub_2a"],
            "modifiedTime": "2026-11-05T10:00:00Z",
            "trashed": False,
        },
        "f3_deleted": {
            "id": "f3_deleted",
            "name": "CM3.md",
            "mimeType": "text/markdown",
            "parents": ["sub_2a"],
            "modifiedTime": "2026-11-06T10:00:00Z",
            "trashed": False,
        },
        "outside_personal": {
            "id": "outside_personal",
            "name": "Personal",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["other_root"],
            "trashed": False,
        },
    }

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            pid = q.split("'")[1]
            return _FakeExec(
                {"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]}
            )

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(f"# Content of {fileId}\n".encode("utf-8"))

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    # --- Part A: Incremental 1-item paging carries watermark with ZERO duplicates ---
    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg_unit", environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _VerifiedCreds())
    conn = EnsoraDriveConnector(cfg, oauth_adapter=oauth, drive_service_factory=lambda _: _Svc(), rate_limiter=RateLimiter(0.0))

    p1 = conn.list_changes(scope={"page_size": 1}, cursor="drvmod:2026-11-01T10:00:00Z")
    assert p1.complete is False and p1.next_cursor is not None and p1.next_cursor.startswith("drvpage:")
    assert [r.external_id for r in p1.items] == ["ensora_drive:f2_moved"]

    p2 = conn.list_changes(scope={"page_size": 1}, cursor=p1.next_cursor)
    assert p2.complete is True
    assert [r.external_id for r in p2.items] == ["ensora_drive:f3_deleted"]
    assert p2.sync_token == "drvmod:2026-11-06T10:00:00Z"

    # --- Part B: Depth truncation disables authoritative reconciliation AND withholds watermark ---
    p_trunc = conn.list_changes(
        scope={
            "max_depth": 0,
            "known_external_ids": {"ensora_drive:f1_stay", "ensora_drive:f2_moved", "ensora_drive:f3_deleted"},
        }
    )
    assert conn.last_scan_authoritative is False
    assert p_trunc.items == ()  # No false tombstones emitted!
    assert p_trunc.sync_token is None  # Watermark not advanced on incomplete truncated scan!
    assert any("max recursion depth" in w.lower() for w in p_trunc.warnings)

    # --- Part C: Persisted end-to-end Runtime + FastAPI visibility on move outside root & delete ---
    settings = Settings(
        config_dir=tmp_path / "app_cfg",
        local_data_dir=tmp_path / "app_data",
        ensai_profile_dir=tmp_path / "app_cfg/sessions/ensai",
    )
    app = create_app(settings)

    async def _run_e2e() -> None:
        async with app.router.lifespan_context(app):
            rt: Runtime = app.state.runtime
            # Inject fake Drive service + connected auth onto the default build_components-created connector!
            drive_bridge = rt.sync.connectors[CodexSourceKind.ENSORA_DRIVE]
            # These tests validate full authorized-tree reconciliation independently of student filtering.
            drive_bridge._student_scope_loader = None
            drive_bridge._connector._service_factory = lambda _: _Svc()
            drive_bridge._connector._oauth = oauth
            drive_bridge._connector._rate_limiter = RateLimiter(0.0)
            rt.auth.adapters = {**rt.auth.adapters, CodexSourceKind.ENSORA_DRIVE: _AlwaysConnectedAuth()}
            rt.consent.record(CodexSourceKind.ENSORA_DRIVE, "read", True)

            hdrs = {"Authorization": f"Bearer {rt.api_token}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=hdrs) as client:
                # 1. Initial sync persists all 3 Drive files
                st1, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=True)))
                assert st1 == "succeeded"

                docs1 = (await client.get("/api/v1/documents")).json()["items"]
                by_ext1 = {d["external_id"]: d for d in docs1}
                assert set(by_ext1.keys()) == {
                    "ensora_drive:f1_stay",
                    "ensora_drive:f2_moved",
                    "ensora_drive:f3_deleted",
                }
                moved_doc_id = by_ext1["ensora_drive:f2_moved"]["id"]
                deleted_doc_id = by_ext1["ensora_drive:f3_deleted"]["id"]

                # Verify persisted durable watermark cursor
                with rt.engine.connect() as db:
                    cur1 = db.scalar(
                        select(db_connections.c.cursor).where(
                            db_connections.c.id == rt.connection_ids[CodexSourceKind.ENSORA_DRIVE]
                        )
                    )
                assert cur1 == "drvmod:2026-11-06T10:00:00Z"

                # 2. Move f2_moved outside approved Ensora root AND trash f3_deleted!
                tree["f2_moved"]["parents"] = ["outside_personal"]
                tree["f3_deleted"]["trashed"] = True

                # 3. Incremental rerun (using persisted drvmod: cursor) reconciles approved-root membership!
                st2, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=False)))
                assert st2 == "succeeded"

                # 4. API hides moved & deleted cached files immediately while preserving f1_stay + DB version history!
                docs2 = (await client.get("/api/v1/documents")).json()["items"]
                assert [d["external_id"] for d in docs2] == ["ensora_drive:f1_stay"]
                assert (await client.get(f"/api/v1/documents/{moved_doc_id}")).status_code == 404
                assert (await client.get(f"/api/v1/documents/{deleted_doc_id}")).status_code == 404

                with rt.engine.connect() as db:
                    all_db_docs = {
                        r["external_id"]: r["metadata_json"]
                        for r in db.execute(select(db_documents.c.external_id, db_documents.c.metadata_json)).mappings()
                    }
                    ver_count = len(db.execute(select(db_versions.c.id)).all())
                assert all_db_docs["ensora_drive:f2_moved"]["access_status"] == "tombstoned"
                assert all_db_docs["ensora_drive:f2_moved"]["title"] == "CM2.md"  # Original title preserved!
                assert all_db_docs["ensora_drive:f3_deleted"]["access_status"] == "tombstoned"
                assert all_db_docs["ensora_drive:f3_deleted"]["title"] == "CM3.md"
                assert ver_count == 3  # Immutable version history retained!

    asyncio.run(_run_e2e())


def test_final_block_003_return_to_root_with_old_timestamp_reactivates(tmp_path: Path) -> None:
    """FINAL-BLOCK-003 (recheck): Returning file with old modifiedTime reactivates cleanly while preserving ID & versions; incomplete scans never reactivate."""
    root_id = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
    tree: dict[str, dict[str, Any]] = {
        root_id: {"id": root_id, "name": "Ensora", "mimeType": DRIVE_FOLDER_MIME, "parents": [], "trashed": False},
        "sub_2a": {"id": "sub_2a", "name": "2A", "mimeType": DRIVE_FOLDER_MIME, "parents": [root_id], "trashed": False},
        "f_keep": {
            "id": "f_keep",
            "name": "Keep.md",
            "mimeType": "text/markdown",
            "parents": ["sub_2a"],
            "modifiedTime": "2026-11-10T10:00:00Z",
            "trashed": False,
        },
        "f_return": {
            "id": "f_return",
            "name": "ReturnOld.md",
            "mimeType": "text/markdown",
            "parents": ["sub_2a"],
            "modifiedTime": "2026-11-01T10:00:00Z",  # Older than watermark!
            "trashed": False,
        },
        "f_moved_in_old": {
            "id": "f_moved_in_old",
            "name": "NewlySharedOld.md",
            "mimeType": "text/markdown",
            "parents": ["outside_personal"],
            "modifiedTime": "2026-10-15T10:00:00Z",  # Earlier than watermark!
            "trashed": False,
        },
        "outside_personal": {
            "id": "outside_personal",
            "name": "Personal",
            "mimeType": DRIVE_FOLDER_MIME,
            "parents": ["other_root"],
            "trashed": False,
        },
    }

    class _Files:
        def get(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(tree[fileId])

        def list(self, *, q: str, **kwargs: Any) -> _FakeExec:
            pid = q.split("'")[1]
            return _FakeExec(
                {"files": [v for v in tree.values() if pid in (v.get("parents") or []) and not v.get("trashed")]}
            )

        def get_media(self, *, fileId: str, **kwargs: Any) -> _FakeExec:
            return _FakeExec(f"# Stable content for {fileId}\n".encode("utf-8"))

    class _Svc:
        def files(self) -> _Files:
            return _Files()

    cfg = SourcesRuntimeConfig.load(config_dir=tmp_path / "cfg_oauth", environ={})
    cfg.ensure_private_directories()
    cfg.google_token_path.write_text("{}", encoding="utf-8")
    oauth = GoogleOAuthAdapter(cfg, source=SourceKind.ENSORA_DRIVE, credentials_loader=lambda p, s: _VerifiedCreds())

    settings = Settings(
        config_dir=tmp_path / "app_cfg",
        local_data_dir=tmp_path / "app_data",
        ensai_profile_dir=tmp_path / "app_cfg/sessions/ensai",
    )
    app = create_app(settings)

    async def _run_e2e() -> None:
        async with app.router.lifespan_context(app):
            rt: Runtime = app.state.runtime
            drive_bridge = rt.sync.connectors[CodexSourceKind.ENSORA_DRIVE]
            # These tests validate full authorized-tree reconciliation independently of student filtering.
            drive_bridge._student_scope_loader = None
            drive_bridge._connector._service_factory = lambda _: _Svc()
            drive_bridge._connector._oauth = oauth
            drive_bridge._connector._rate_limiter = RateLimiter(0.0)
            rt.auth.adapters = {**rt.auth.adapters, CodexSourceKind.ENSORA_DRIVE: _AlwaysConnectedAuth()}
            rt.consent.record(CodexSourceKind.ENSORA_DRIVE, "read", True)

            hdrs = {"Authorization": f"Bearer {rt.api_token}"}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1", headers=hdrs) as client:
                # 1. Initial sync persists f_keep (2026-11-10) & f_return (2026-11-01); watermark -> 2026-11-10
                st1, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=True)))
                assert st1 == "succeeded"
                docs1 = {d["external_id"]: d for d in (await client.get("/api/v1/documents")).json()["items"]}
                assert set(docs1.keys()) == {"ensora_drive:f_keep", "ensora_drive:f_return"}
                orig_doc_id = docs1["ensora_drive:f_return"]["id"]
                orig_ver_id = docs1["ensora_drive:f_return"]["active_version_id"]

                # 2. Move BOTH f_keep and f_return outside approved root -> empty active set, both tombstoned!
                tree["f_keep"]["parents"] = ["outside_personal"]
                tree["f_return"]["parents"] = ["outside_personal"]
                st2, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=False)))
                assert st2 == "succeeded"
                assert (await client.get("/api/v1/documents")).json()["items"] == []
                assert (await client.get(f"/api/v1/documents/{orig_doc_id}")).status_code == 404

                # 3. Move f_return BACK into approved root with its SAME OLD modifiedTime (2026-11-01 <= 2026-11-10)
                #    AND move f_moved_in_old (first-seen file with old 2026-10-15 timestamp) into approved root!
                #    Keep f_keep outside root (must remain inaccessible/tombstoned!).
                tree["f_return"]["parents"] = ["sub_2a"]
                tree["f_moved_in_old"]["parents"] = ["sub_2a"]

                # 3a. Verify incomplete (depth-truncated) scan does NOT reactivate returning files
                p_trunc = drive_bridge._connector.list_changes(
                    scope={"max_depth": 0, "known_external_ids": {}},
                    cursor="drvmod:2026-11-10T10:00:00Z",
                )
                assert drive_bridge._connector.last_scan_authoritative is False
                assert p_trunc.items == ()
                assert p_trunc.sync_token is None

                # 3b. Authoritative incremental rerun reactivates f_return AND discovers f_moved_in_old!
                st3, _ = _wait_for_job(rt, rt.sync.submit(SyncScope(source=CodexSourceKind.ENSORA_DRIVE, full=False)))
                assert st3 == "succeeded"

                docs3 = {d["external_id"]: d for d in (await client.get("/api/v1/documents")).json()["items"]}
                assert set(docs3.keys()) == {"ensora_drive:f_return", "ensora_drive:f_moved_in_old"}
                # Same logical document ID & same immutable version ID preserved on reactivation!
                assert docs3["ensora_drive:f_return"]["id"] == orig_doc_id
                assert docs3["ensora_drive:f_return"]["active_version_id"] == orig_ver_id
                assert (await client.get(f"/api/v1/documents/{orig_doc_id}")).status_code == 200
                # File still outside root remains tombstoned / inaccessible
                assert (await client.get(f"/api/v1/documents/{docs1['ensora_drive:f_keep']['id']}")).status_code == 404

                with rt.engine.connect() as db:
                    vers_for_return = db.execute(
                        select(db_versions.c.id).where(db_versions.c.document_id == orig_doc_id)
                    ).all()
                    saved_cur = db.scalar(
                        select(db_connections.c.cursor).where(
                            db_connections.c.id == rt.connection_ids[CodexSourceKind.ENSORA_DRIVE]
                        )
                    )
                assert len(vers_for_return) == 1  # No duplicate version created!
                assert saved_cur == "drvmod:2026-11-10T10:00:00Z"

    asyncio.run(_run_e2e())
