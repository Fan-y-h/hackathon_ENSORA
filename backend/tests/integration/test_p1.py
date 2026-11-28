import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic, sleep

from httpx import ASGITransport, AsyncClient
import asyncio
from pydantic import SecretStr, ValidationError
import pytest
from sqlalchemy import func, insert, select, text, update

from backend.app.contracts.authentication import AuthenticationResult, ConnectionStatus, ProfileBusy, ReauthenticationRequired
from backend.app.contracts.sources import ChangePage, Document, Provenance, SourceKind, SyncScope
from backend.app.contracts.sync import SyncScheduleSettings
from backend.app.contracts.users import Preferences
from backend.app.core.database import LOCAL_USER, chunks, connections, documents, new_id, runs, users, versions
from backend.app.core.runtime import Components, Runtime
from backend.app.core.security import KeyringCredentialStore, LockedBrowserSession
from backend.app.core.settings import Settings
from backend.app.modules.ingestion.api import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(config_dir=tmp_path / "config", local_data_dir=tmp_path / "data",
                    ensai_profile_dir=tmp_path / "config/sessions/ensai")


def test_config_preserves_file_and_applies_overrides(tmp_path, monkeypatch):
    path = tmp_path / "sources.env"
    data = "SYNC_INTERVAL_MINUTES=5\nENSAI_USERNAME=synthetic-identifier\n"
    path.write_text(data)
    monkeypatch.setenv("SYNC_INTERVAL_MINUTES", "9")
    monkeypatch.setenv("LOCAL_DATA_DIR", str(tmp_path / "data"))
    settings = Settings.load(path)
    settings.prepare()
    assert path.read_text() == data
    assert settings.sync_interval_minutes == 9
    assert "ensai_username" not in settings.model_dump()
    assert settings.local_data_dir.stat().st_mode & 0o777 == 0o700
    with pytest.raises(ValidationError):
        Settings(google_project_id="unexpected-project")


def test_source_sync_schedules_cooldowns_backoff_and_persistence(settings, monkeypatch):
    now = datetime(2026, 11, 28, 12, tzinfo=UTC)
    sources = (SourceKind.ENSORA_DRIVE, SourceKind.MOODLE)
    components = Components(connectors={source: Connector() for source in sources})
    with Runtime(settings, components) as runtime:
        defaults = runtime.schedule_status(now)
        by_source = {item.source: item for item in defaults.sources if item.scope_key is None}
        assert by_source[SourceKind.ENSORA_DRIVE].interval_minutes == 10080
        assert by_source[SourceKind.MOODLE].interval_minutes == 10080
        pample_by_scope = {item.scope_key: item.interval_minutes for item in defaults.sources
                           if item.source == SourceKind.PAMPLEMOUSSE}
        assert pample_by_scope == {"timetable": 360, "grades": 1440, "catalog": 10080}
        assert by_source[SourceKind.GOOGLE_CALENDAR].interval_minutes == 30
        assert by_source[SourceKind.LOCAL].interval_minutes is None
        assert defaults.supported_intervals_minutes == [30, 60, 360, 720, 1440, 4320, 10080, 20160, 43200]

        schedule = SyncScheduleSettings(enabled=True, intervals_minutes={
            SourceKind.ENSORA_DRIVE.value: 10080, SourceKind.MOODLE.value: 10080,
        })
        runtime.update_schedule(schedule)
        for source in sources:
            runtime.consent.record(source, "read", True)
            runtime.consent.record(source, "automatic_sync", True)
        with runtime.engine.begin() as db:
            db.execute(update(connections).where(connections.c.source == SourceKind.ENSORA_DRIVE.value).values(
                last_success_at=(now - timedelta(days=8)).isoformat()))
            db.execute(update(connections).where(connections.c.source == SourceKind.MOODLE.value).values(
                last_success_at=(now - timedelta(days=1)).isoformat()))

        submissions = []
        monkeypatch.setattr(runtime.sync, "submit", lambda scope, **kwargs: submissions.append((scope, kwargs)) or "job")
        assert runtime.schedule_due_sync(now) == "job"
        assert submissions[0][0].source == SourceKind.ENSORA_DRIVE
        assert submissions[0][1] == {"automatic": True}
        with runtime.sync.active_lock:
            runtime.sync.active["running"] = Event()
        assert runtime.schedule_due_sync(now) is None
        with runtime.sync.active_lock:
            runtime.sync.active.pop("running")

        # A failed attempt persists a bounded exponential delay, rather than
        # immediately launching the same expensive source again.
        failed_at = now - timedelta(minutes=1)
        with runtime.engine.begin() as db:
            db.execute(insert(runs).values(
                id=new_id(), user_id=LOCAL_USER, status="partial",
                scope={"source": SourceKind.ENSORA_DRIVE.value},
                report={SourceKind.ENSORA_DRIVE.value: {"status": "failed", "error_codes": ["source_failure"]}},
                started_at=failed_at.isoformat(), finished_at=failed_at.isoformat(),
            ))
            # A retry attempt after the previous success is the latest relevant history.
            db.execute(update(connections).where(connections.c.source == SourceKind.ENSORA_DRIVE.value).values(
                last_success_at=None))
        status = runtime.schedule_status(now)
        drive_status = next(item for item in status.sources if item.source == SourceKind.ENSORA_DRIVE)
        assert drive_status.recent_failure_code == "source_failure"
        assert drive_status.next_scheduled_at == now + timedelta(minutes=4)
        persisted = drive_status.interval_minutes

    with Runtime(settings, components) as restarted:
        assert next(item for item in restarted.schedule_status(now).sources
                    if item.source == SourceKind.ENSORA_DRIVE).interval_minutes == persisted
        manual = SyncScheduleSettings(intervals_minutes={SourceKind.ENSORA_DRIVE.value: None})
        manual_status = restarted.update_schedule(manual)
        drive = next(item for item in manual_status.sources if item.source == SourceKind.ENSORA_DRIVE)
        assert drive.manual_only and drive.next_scheduled_at is None
        disabled = SyncScheduleSettings(enabled=False, intervals_minutes={})
        result = restarted.update_schedule(disabled)
        assert result.enabled is False
        assert restarted.schedule_due_sync(now) is None


def test_overdue_source_sync_runs_once_and_restart_observes_success(settings):
    source = SourceKind.ENSORA_DRIVE

    class EmptyConnector:
        def list_changes(self, scope, cursor):
            return ChangePage(items=[], complete=True)

        def fetch_blob(self, record):
            raise AssertionError("No document was discovered")

    components = Components(adapters={source: Auth()}, connectors={source: EmptyConnector()})
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.consent.record(source, "automatic_sync", True)
        job = runtime.schedule_due_sync()
        assert job
        assert await_job(runtime, job)["status"] == "succeeded"
        with runtime.engine.connect() as db:
            saved = db.scalar(select(connections.c.last_success_at).where(connections.c.source == source.value))
        assert saved
        status = next(row for row in runtime.schedule_status().sources if row.source == source)
        assert status.last_success_at is not None
        assert status.next_scheduled_at > datetime.now(UTC)
        assert runtime.schedule_due_sync() is None

    with Runtime(settings, components) as restarted:
        assert restarted.schedule_due_sync() is None


def test_pamplemousse_schedule_runs_three_independent_scopes(settings):
    source = SourceKind.PAMPLEMOUSSE

    class SliceConnector:
        observed_scopes = []

        def list_changes(self, scope, cursor):
            self.observed_scopes.append(scope.pamplemousse_scope)
            return ChangePage(items=[], complete=True)

        def fetch_blob(self, record):
            raise AssertionError("No documents are expected")

    connector = SliceConnector()
    components = Components(adapters={source: Auth()}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.consent.record(source, "automatic_sync", True)
        scopes = []
        for expected in ("timetable", "grades", "catalog"):
            job = runtime.schedule_due_sync()
            assert job and await_job(runtime, job)["status"] == "succeeded"
            # The just-succeeded scope receives its own cadence; the next overdue
            # Pamplemousse slice is selected without waiting for the longest interval.
            scope = await_job(runtime, job)["scope"]
            scopes.append(scope["pamplemousse_scope"])
        assert scopes == ["timetable", "grades", "catalog"]
        assert connector.observed_scopes == scopes
        status = {row.scope_key: row for row in runtime.schedule_status().sources
                  if row.source == source}
        assert {key: value.interval_minutes for key, value in status.items()} == {
            "timetable": 360, "grades": 1440, "catalog": 10080,
        }
        assert all(value.last_success_at is not None for value in status.values())


def test_keyring_and_profile_lock(tmp_path, monkeypatch):
    entries = {}

    class FakeBackend:
        __module__ = "keyring.backends.SecretService"
        set_password = staticmethod(lambda service, key, value: entries.update({(service, key): value}))
        get_password = staticmethod(lambda service, key: entries.get((service, key)))
        delete_password = staticmethod(lambda service, key: entries.pop((service, key), None))

    monkeypatch.setattr("keyring.get_keyring", lambda: FakeBackend())
    store = KeyringCredentialStore("testing")
    store.set("moodle:password", SecretStr("synthetic-password"))
    assert store.get("moodle:password") == SecretStr("synthetic-password")
    store.delete("moodle:password")
    assert store.get("moodle:password") is None

    profile = tmp_path / "profile"
    with LockedBrowserSession(profile).lease():
        with pytest.raises(ProfileBusy), LockedBrowserSession(profile).lease():
            pytest.fail("Lease must be exclusive")
    with LockedBrowserSession(profile).lease() as leased:
        assert leased == profile


class Auth:
    expired = False
    calls = 0

    def validate(self):
        self.calls += 1
        if self.expired:
            raise ReauthenticationRequired()
        return AuthenticationResult(status=ConnectionStatus.CONNECTED)

    def authorize(self, inputs):
        self.expired = False
        return self.validate()

    def disconnect(self):
        self.expired = True


class Connector:
    payload = b"synthetic linear algebra"
    revision = "one"
    fetches = 0
    requested = None
    requested_scope = None
    fail = False

    def list_changes(self, scope, cursor):
        self.requested = cursor
        self.requested_scope = scope
        items = [Document(provenance=Provenance(external_id="doc", remote_revision=self.revision,
                                                 academic_year=getattr(self, "academic_year", None)),
                          title="Demo", mime_type="text/plain")]
        return ChangePage(items=items, next_cursor=self.revision)

    def fetch_blob(self, record):
        self.fetches += 1
        if self.fail:
            raise ValueError("private upstream details")
        return self.payload


def await_job(runtime, job):
    end = monotonic() + 5
    while monotonic() < end:
        with runtime.engine.connect() as db:
            row = db.execute(select(runs).where(runs.c.id == job)).mappings().one()
        if row["status"] not in {"queued", "running"}:
            return row
        sleep(0.01)
    pytest.fail("job did not complete")


def test_sync_incremental_dedup_version_failure_and_reauth(settings):
    source = SourceKind.MOODLE
    connector, adapter = Connector(), Auth()
    components = Components(adapters={source: adapter}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        scope = SyncScope(source=source)
        def run(automatic=False):
            return await_job(runtime, runtime.sync.submit(scope, automatic=automatic))
        assert run()["status"] == "partial"
        assert adapter.calls == 0
        runtime.consent.record(source, "read", True)
        assert run(automatic=True)["report"] == {}
        assert run()["report"][source]["documents"] == 1
        assert run()["report"][source]["unchanged"] == 1
        assert connector.requested == "one"
        assert connector.fetches == 1
        connector.revision = "two"
        assert run()["report"][source]["unchanged"] == 1
        connector.revision = "three"
        connector.payload = b"updated"
        connector.fail = True
        failed = run()
        assert failed["status"] == "partial"
        assert "private upstream" not in str(failed["report"])
        connector.fail = False
        assert run()["report"][source]["documents"] == 1
        assert connector.requested == "two"
        adapter.expired = True
        assert runtime.auth.validate(source).status == ConnectionStatus.REAUTH_REQUIRED
        with runtime.engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(documents)) == 1
            assert db.scalar(select(func.count()).select_from(versions)) == 2
            assert db.scalar(select(func.count()).select_from(chunks)) == 2
            assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0005_p1_read_indexes"
            assert db.scalar(text("PRAGMA foreign_keys")) == 1
        assert len(list((settings.local_data_dir / "blobs").iterdir())) == 2
        assert runtime.auth.authorize(source).status == ConnectionStatus.CONNECTED
    with Runtime(settings, components) as runtime:
        assert runtime.consent.allowed(source, "read")
        assert (settings.local_data_dir / "blobs" / hashlib.sha256(b"updated").hexdigest()).is_file()


def test_api_onboarding_local_documents_and_academic(settings):
    app = create_app(settings, Components())

    async def run():
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                base = "/api/v1"
                assert (await client.get(base + "/health")).json()["status"] == "ready"
                assert (await client.get(base + "/documents")).status_code == 401
                token = (settings.local_data_dir / "api-token").read_text()
                client.headers["Authorization"] = f"Bearer {token}"
                assert (await client.get(base + "/onboarding")).json()["completed"] is False
                assert (await client.post(base + "/onboarding/complete")).json()["completed"] is True
                ids = {item["source"]: item["id"] for item in (await client.get(base + "/connections")).json()["items"]}
                for source in ("local", "academic_import"):
                    path = f"{base}/connections/{ids[source]}"
                    assert (await client.put(path + "/consent", json={"purpose": "read", "granted": True})).status_code == 200
                    assert (await client.post(path + "/authorize")).json()["status"] == "CONNECTED"
                upload = {"file": ("course.txt", b"synthetic content", "text/plain")}
                assert (await client.post(base + "/documents/import", files=upload)).status_code == 201
                assert (await client.post(base + "/documents/import", files=upload)).json()["result"] == "unchanged"
                document = (await client.get(base + "/documents")).json()["items"][0]
                prefix = f"{base}/documents/{document['id']}/versions/{document['active_version_id']}"
                assert (await client.get(prefix + "/content")).content == b"synthetic content"
                assert (await client.get(prefix + "/chunks")).json()["items"][0]["payload"]["text"] == "synthetic content"

                payload = [
                    {"kind": "subject", "title": "Demo subject", "provenance": {"external_id": "subject"}},
                    {"kind": "assessment", "title": "Demo assessment", "parent_key": "subject",
                     "provenance": {"external_id": "exam"}},
                    {"kind": "grade", "title": "Published result", "parent_key": "exam", "value": "0", "maximum": "20",
                     "provenance": {"external_id": "grade"}},
                ]
                assert (await client.post(base + "/academic/import", json=payload)).json()["results"] == [{"status": "records"}] * 3
                assert (await client.post(base + "/academic/import", json=payload)).json()["results"] == [{"status": "unchanged"}] * 3
                assert (await client.get(base + "/grades")).json()["items"][0]["payload"]["value"] == "0"
                payload[-1]["value"] = "21"
                assert (await client.post(base + "/academic/import", json=payload)).status_code == 422
                assert (await client.delete(f"{base}/connections/{ids['local']}")).status_code == 204
                assert (await client.get(prefix + "/content")).status_code == 404

    asyncio.run(run())


def test_sync_schedule_api_persists_preferences_and_manual_refresh(settings):
    source = SourceKind.ENSORA_DRIVE

    class EmptyConnector:
        def list_changes(self, scope, cursor):
            return ChangePage(items=[], complete=True)

        def fetch_blob(self, record):
            raise AssertionError("No document was discovered")

    components = Components(adapters={source: Auth()}, connectors={source: EmptyConnector()})
    app = create_app(settings, components)
    request = type("RequestStub", (), {})()
    request.app = type("AppStub", (), {})()
    request.app.state = type("StateStub", (), {})()
    with Runtime(settings, components) as runtime:
        request.app.state.runtime = runtime
        flat_routes = [route for route in app.routes if hasattr(route, "endpoint")]
        flat_routes.extend(child for route in app.routes if hasattr(route, "original_router")
                           for child in route.original_router.routes)
        routes = {(route.path, method): route.endpoint for route in flat_routes
                  for method in (route.methods or set())}
        get_schedule = routes[("/api/v1/sync/schedule", "GET")]
        update_schedule = routes[("/api/v1/sync/schedule", "PATCH")]
        reset_schedule = routes[("/api/v1/sync/schedule/reset", "POST")]
        initial = get_schedule(request)
        drive = next(row for row in initial["sources"] if row["source"] == source.value)
        assert drive["interval_minutes"] == 10080
        custom = SyncScheduleSettings(enabled=True, enabled_sources=[source],
                                      intervals_minutes={source.value: 1440})
        update_schedule(request, custom)
        routes[("/api/v1/settings", "PATCH")](request, Preferences(formats=["pdf"], daily_minutes=90))
        assert next(row for row in get_schedule(request)["sources"] if row["source"] == source.value)[
            "interval_minutes"] == 1440
        reset_schedule(request)
        assert next(row for row in get_schedule(request)["sources"] if row["source"] == source.value)[
            "interval_minutes"] == 10080

        runtime.consent.record(source, "read", True)
        runtime.auth.authorize(source)
        # Manual refresh is immediate and independent of automatic-sync consent/cadence.
        response = routes[("/api/v1/sync", "POST")](request, SyncScope(source=source), None)
        assert response["id"]
        assert await_job(runtime, response["id"])["status"] == "succeeded"


def test_drive_scope_policy_bridge_etl_api_cache_retry_and_safe_cleanup(settings, monkeypatch, capsys):
    import json
    import sys

    from backend.app.core.drive_scope import folder_scope
    from backend.app.core.database import documents as db_documents
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge, _load_active_drive_documents
    from backend.app.modules.ingestion.contracts import ChangePage as InternalPage
    from backend.app.modules.ingestion.contracts import SourceKind as InternalSourceKind
    from backend.app.modules.ingestion.connectors.base import PermissionDeniedError

    assert folder_scope("Ensora/1A/Statistics/Slides") == "allowed"
    assert folder_scope("Ensora/Global/Admin") == "global"
    assert folder_scope("Ensora/Other/1A/Slides") == "unverified"
    assert folder_scope("1A/Statistics/Global") == "allowed"  # only the top-level Global tree is excluded

    class BridgeDrive:
        source_kind = InternalSourceKind.ENSORA_DRIVE
        root_folder_id = settings.ensora_drive_folder_id
        last_stats = {}

        def __init__(self):
            self.scopes = []

        def list_changes(self, scope, cursor):
            self.scopes.append(scope)
            return InternalPage(items=[], next_cursor=None, complete=True)

        def fetch_blob(self, record):
            raise AssertionError("No connector item expected")

    bridge_connector = BridgeDrive()
    bridge = CodexConnectorBridge(bridge_connector)
    for scope in (SyncScope(source=SourceKind.ENSORA_DRIVE),
                  SyncScope(source=SourceKind.ENSORA_DRIVE, full=True),
                  SyncScope(source=SourceKind.ENSORA_DRIVE, retry_failed=True)):
        bridge.list_changes(scope, None)
        assert bridge_connector.scopes[-1]["cohorts"] == ["1A", "2A", "3A"]
    with pytest.raises(PermissionDeniedError):
        bridge.list_changes(SyncScope(source=SourceKind.ENSORA_DRIVE, folder_id="unapproved-subfolder"), None)

    source = SourceKind.ENSORA_DRIVE
    allowed = Document(provenance=Provenance(external_id="allowed", source=source,
                                               folder="Ensora/1A/Statistics"),
                       title="Allowed fixture", mime_type="text/plain")
    global_doc = Document(provenance=Provenance(external_id="global", source=source,
                                                 folder="Ensora/Global/Shared"),
                          title="Excluded fixture", mime_type="text/plain")
    unverified = Document(provenance=Provenance(external_id="unverified", source=source,
                                                 folder="Ensora/Other/Shared"),
                          title="Unverified fixture", mime_type="text/plain")

    class MixedDrive:
        fetched = []

        def list_changes(self, scope, cursor):
            return ChangePage(items=[allowed, global_doc, unverified], complete=True)

        def fetch_blob(self, record):
            self.fetched.append(record.provenance.external_id)
            return b"synthetic allowed content"

    connector = MixedDrive()
    components = Components(adapters={source: Auth()}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        report = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        detail = report["report"][source.value]
        assert detail["metrics"]["documents_excluded"] == 2
        assert detail["documents"] == 1
        assert connector.fetched == ["allowed"]
        # Exercise the persisted-document guard with a pre-existing Global row.
        assert runtime.pipeline.ingest(runtime.connection_ids[source], global_doc,
                                       lambda _: b"synthetic old global bytes") == "documents"
        with runtime.engine.connect() as db:
            global_id = db.scalar(select(db_documents.c.id).where(db_documents.c.external_id == "global"))
        assert runtime.pipeline.reprocess_documents([runtime.connection_ids[source]],
                                                    document_ids=[global_id], limit=1)["selected"] == 0
        active_inventory = _load_active_drive_documents(settings.local_data_dir / "ensora.sqlite3")
        assert set(active_inventory) == {"allowed"}

        app = create_app(settings, components)
        request = type("RequestStub", (), {})()
        request.app = type("AppStub", (), {})()
        request.app.state = type("StateStub", (), {"runtime": runtime})()
        routes = [child for route in app.routes if hasattr(route, "original_router")
                  for child in route.original_router.routes]
        list_documents = next(route.endpoint for route in routes
                              if route.path == "/api/v1/documents" and "GET" in route.methods)
        visible_ids = {item["id"] for item in list_documents(request, limit=50, cursor=None,
                                                                subject_id=None)["items"]}
        assert global_id not in visible_ids
        assert len(visible_ids) == 1

    # Cleanup is dry-run by default, and explicit apply tombstones metadata only.
    monkeypatch.setattr(sys, "argv", ["drive_scope_cleanup.py", "--data-dir", str(settings.local_data_dir)])
    from scripts import drive_scope_cleanup
    drive_scope_cleanup.main()
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["mode"] == "dry_run" and dry_run["global_documents_to_tombstone"] == 1
    monkeypatch.setattr(sys, "argv", ["drive_scope_cleanup.py", "--data-dir", str(settings.local_data_dir), "--apply"])
    drive_scope_cleanup.main()
    applied = json.loads(capsys.readouterr().out)
    assert applied["global_documents_tombstoned"] == 1
    assert applied["content_blobs_deleted"] == 0
    with Runtime(settings, components) as runtime:
        with runtime.engine.connect() as db:
            metadata = db.scalar(select(db_documents.c.metadata_json).where(db_documents.c.external_id == "global"))
            assert metadata["access_status"] == "tombstoned"
            assert db.scalar(select(func.count()).select_from(versions)) == 2
        assert (settings.local_data_dir / "blobs" / hashlib.sha256(b"synthetic old global bytes").hexdigest()).is_file()


def test_cli_onboarding_network_free(settings, monkeypatch):
    import sys
    scripts = Path(__file__).resolve().parents[3] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    import onboarding

    answers = iter(["n", "n", "n", "n", "y", "y", "n"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    with Runtime(settings, Components()) as runtime:
        onboarding.run_onboarding(runtime)
        assert runtime.consent.allowed(SourceKind.LOCAL, "read")
        assert not runtime.consent.allowed(SourceKind.MOODLE, "read")
        with runtime.engine.connect() as db:
            assert db.scalar(text("SELECT onboarded FROM users")) == 1


def test_rev001_rev006_rev010_runtime_lock_cleanup_and_consent(settings, monkeypatch):
    from backend.app.core.runtime import RuntimeBusy

    class BusyCleanup(Auth):
        def disconnect(self):
            raise ProfileBusy()

    source = SourceKind.MOODLE
    monkeypatch.setattr("backend.app.core.auth.now", lambda: "2026-01-01T00:00:00+00:00")
    with Runtime(settings, Components(adapters={source: BusyCleanup()})) as runtime:
        with pytest.raises(RuntimeBusy, match="backend is running"), Runtime(settings, Components()):
            pytest.fail("A second writer started")
        runtime.consent.record(source, "read", True)
        assert runtime.consent.allowed(source, "read")
        assert not runtime.auth.disconnect(source)
        assert not runtime.consent.allowed(source, "read")
        assert runtime.auth.validate(source).status == ConnectionStatus.ACCESS_DENIED


def test_rev002_rev011_recover_failed_version_and_enforce_ownership(settings):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError
    from backend.app.core.etl import TextParser

    class RecoverableParser(TextParser):
        def parse(self, document, content):
            if content == b"bad":
                raise ValueError("synthetic parse failure")
            if content == b"unsupported":
                return None
            return super().parse(document, content)

    with Runtime(settings, Components(parser=RecoverableParser())) as runtime:
        identifier = runtime.connection_ids[SourceKind.LOCAL]
        doc = Document(title="fixture.txt", mime_type="text/plain", subject_id="remote",
                       provenance=Provenance(external_id="fixture"))
        runtime.pipeline.ingest(identifier, doc, lambda _: b"ready")
        with runtime.engine.begin() as db:
            initial = db.execute(select(documents)).mappings().one()
            meta = {**initial["metadata_json"], "user_override": {"subject_id": "confirmed"}}
            db.execute(update(documents).values(metadata_json=meta))
        with pytest.raises(ValueError, match="processing failed"):
            runtime.pipeline.ingest(identifier, doc, lambda _: b"bad")
        runtime.pipeline.ingest(identifier, doc, lambda _: b"unsupported")
        with runtime.engine.connect() as db:
            assert db.scalar(select(documents.c.active_version_id)) == initial["active_version_id"]
            assert db.scalar(select(documents.c.metadata_json))["subject_id"] == "confirmed"
            assert "failed" in db.scalars(select(versions.c.status)).all()
        with pytest.raises(IntegrityError), runtime.engine.begin() as db:
            db.execute(update(documents).values(active_version_id="bogus"))
        runtime.pipeline.parser = TextParser()
        runtime.pipeline.ingest(identifier, doc, lambda _: b"bad")
        with runtime.engine.connect() as db:
            assert "failed" not in db.scalars(select(versions.c.status)).all()


def test_rev003_rev004_rev005_rev008_rev009_rev012_rev013_rev014_api(settings):
    from backend.app.contracts.sources import CalendarEvent
    from backend.app.core.database import LOCAL_USER

    app = create_app(settings, Components())
    async def run():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                client.headers["Authorization"] = f"Bearer {runtime.api_token}"
                preflight = await client.options("/api/v1/documents", headers={
                    "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                })
                assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
                for source in (SourceKind.LOCAL, SourceKind.ACADEMIC_IMPORT, SourceKind.GOOGLE_CALENDAR):
                    runtime.consent.record(source, "read", True)
                for content in (b"first", b"second"):
                    assert (await client.post("/api/v1/documents/import", files={
                        "file": ("fixture.txt", content, "text/plain"),
                    })).status_code == 201
                files = (await client.get("/api/v1/documents")).json()["items"]
                assert len(files) == 1
                doc = files[0]
                path = f"/api/v1/documents/{doc['id']}/versions/{doc['active_version_id']}/content"
                response = await client.get(path)
                assert response.content == b"second"
                assert "fixture.txt" in response.headers["content-disposition"]
                (settings.local_data_dir / "blobs" / hashlib.sha256(b"second").hexdigest()).unlink()
                assert (await client.get(path)).status_code == 503
                stored = Document.model_validate({key: value for key, value in doc["metadata_json"].items()
                                                  if key in Document.model_fields})
                runtime.pipeline.ingest(runtime.connection_ids[SourceKind.LOCAL],
                                        stored.model_copy(update={"access_status": "tombstoned"}))
                assert (await client.get(f"/api/v1/documents/{doc['id']}")).status_code == 404
                assert (await client.post("/api/v1/sync", json={"source": "local"})).status_code == 422
                orphan = {"kind": "subject", "title": "Orphan", "parent_key": "missing",
                          "provenance": {"external_id": "orphan"}}
                result = await client.post("/api/v1/academic/import", json=[orphan])
                assert result.status_code == 422 and result.json()["results"][0]["code"] == "missing_parent"
                valid = {**orphan, "parent_key": None}
                preview = await client.post("/api/v1/academic/import?dry_run=true", json=[valid])
                assert preview.status_code == 200
                assert (await client.get("/api/v1/subjects")).json()["items"] == []
                calendar = runtime.connection_ids[SourceKind.GOOGLE_CALENDAR]
                for day in (8, 4):
                    event = CalendarEvent.model_validate(dict(
                        provenance={"external_id": str(day)}, title="Private synthetic title",
                        starts_at=f"2026-01-0{day}T10:00:00+01:00", ends_at=f"2026-01-0{day}T11:00:00+01:00",
                        event_type="class", teacher="Synthetic teacher", confirmation_required=True,
                    ))
                    runtime.pipeline.ingest(calendar, event)
                first = (await client.get("/api/v1/events?limit=1")).json()
                assert first["items"][0]["external_id"] == "4"
                second = (await client.get("/api/v1/events", params={"cursor": first["next_cursor"]})).json()
                assert second["items"][0]["external_id"] == "8"
                items = (await client.get("/api/v1/availability")).json()["items"]
                assert len(items) == 2 and all("title" not in item["payload"] for item in items)
                runtime.pipeline.ingest(calendar, CalendarEvent(
                    provenance=Provenance(external_id="4"), title="Cancelled", status="cancelled",
                ))
                assert len((await client.get("/api/v1/availability")).json()["items"]) == 1
                with runtime.engine.begin() as db:
                    db.execute(runs.insert(), [
                        dict(id="a", user_id=LOCAL_USER, started_at="2026-01-03", status="succeeded", scope={}, report={}),
                        dict(id="z", user_id=LOCAL_USER, started_at="2026-01-01", status="succeeded", scope={}, report={}),
                    ])
                assert (await client.get("/api/v1/sync/runs")).json()["items"][0]["id"] == "z"

    asyncio.run(run())


def test_bridge_durable_cursor_and_partial_diagnostics(settings):
    from backend.app.core.database import connections

    source = SourceKind.MOODLE
    class PagedConnector(Connector):
        error = False
        def list_changes(self, scope, cursor):
            self.requested = cursor
            if cursor != "page2":
                return ChangePage(complete=False, next_cursor="page2", warning_codes=["source_warning"])
            return ChangePage(resume_cursor="durable", error_codes=["source_listing_error"] if self.error else [])

    connector = PagedConnector()
    with Runtime(settings, Components(adapters={source: Auth()}, connectors={source: connector})) as runtime:
        runtime.consent.record(source, "read", True)
        scope = SyncScope(source=source)
        job = await_job(runtime, runtime.sync.submit(scope))
        assert job["report"][source]["warning_codes"] == ["source_warning"]
        connector.error = True
        job = await_job(runtime, runtime.sync.submit(scope))
        assert job["status"] == "partial"
        assert job["report"][source]["error_codes"] == ["source_listing_error"]
        with runtime.engine.connect() as db:
            assert db.scalar(select(connections.c.cursor).where(connections.c.source == source)) == "durable"


def test_rev007_rev015_rev016_configuration_and_setup(settings, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    from backend.app.modules.ingestion.auth import google_cloud
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    import onboarding

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT_ID", "ensora-516015")
    monkeypatch.setenv("ENSAI_CAS_LOGIN_URL", "https://ssocas.ensai.fr/cas/login")
    config = tmp_path / "sources.env"
    loaded = Settings.load(config)
    assert loaded.google_credentials_path.name == "google_credentials.json"
    assert str(loaded.ensai_cas_url).endswith("/cas/login")
    monkeypatch.setattr("keyring.get_keyring", lambda: object())
    assert not KeyringCredentialStore("synthetic").is_available()

    observed = []
    class Setup:
        def __init__(self, _): pass
        def inspect(self):
            return SimpleNamespace(project_accessible=True, project_id="ensora-516015",
                                   missing_apis=() if observed else ("drive.googleapis.com",),
                                   credentials_validation=SimpleNamespace(valid=True))
        def enable_required_apis(self, *, approved, apis):
            observed.append((approved, apis))
            return SimpleNamespace(returncode=0)

    monkeypatch.setattr(google_cloud, "GoogleCloudSetupHelper", Setup)
    monkeypatch.setattr(onboarding, "yes", lambda _: True)
    assert onboarding.google_ready(settings)
    assert observed == [(True, ("drive.googleapis.com",))]

    class ForcedAuth(Auth):
        def reauthorize(self, inputs):
            observed.append("forced")
            return self.validate()

    source = SourceKind.MOODLE
    with Runtime(settings, Components(adapters={source: ForcedAuth()})) as runtime:
        runtime.consent.record(source, "read", True)
        assert runtime.auth.reauthorize(source).status == ConnectionStatus.CONNECTED
        monkeypatch.setattr(onboarding, "yes", lambda prompt: source.value in prompt and pytest.fail(prompt))
        onboarding.run_onboarding(runtime)
        assert observed[-1] == "forced"


def test_rev011_upgrade_existing_database_and_standalone_config(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    ini = Path(__file__).resolve().parents[2] / "alembic.ini"
    config = Config(str(ini))
    url = "sqlite:///" + str(tmp_path / "previous.sqlite3")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0001_p1")
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text("INSERT INTO users VALUES ('synthetic', '{}', '{}', 0)"))
    command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.scalar(text("SELECT id FROM users")) == "synthetic"
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "0005_p1_read_indexes"
    assert "ix_records_parent" in {index["name"] for index in inspect(engine).get_indexes("source_records")}
    engine.dispose()


def test_rev016_bootstrap_accepts_pep508_requirements(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts"))
    import bootstrap

    manifest = tmp_path / "pyproject.toml"
    manifest.write_text("""[project]
dependencies = ["sample>=1,<2", "other~=3.2", "ignored; python_version < '0'"]
""")
    monkeypatch.setattr(bootstrap, "MANIFEST", manifest)
    monkeypatch.setattr(bootstrap, "version", lambda name: {"sample": "1.2", "other": "1.0"}[name])
    invoked = []
    monkeypatch.setattr(bootstrap.subprocess, "run", lambda command, check: invoked.append(command))
    bootstrap.install_missing()
    assert len(invoked) == 1 and invoked[0][-1] == "other~=3.2"


def test_default_bridge_uses_only_consented_catalog(settings):
    from backend.app.contracts.sources import ClassificationResult, Subject

    with Runtime(settings) as runtime:
        source = SourceKind.ACADEMIC_IMPORT
        runtime.consent.record(source, "read", True)
        runtime.pipeline.ingest(runtime.connection_ids[source], Subject(
            provenance=Provenance(external_id="canonical-linear"), title="Linear Algebra", code="ALG201",
        ))
        document = Document(title="Linear Algebra", provenance=Provenance(external_id="local:test"))
        assert runtime.pipeline.matcher.match(document, ClassificationResult()).subject_id == "canonical-linear"
        runtime.consent.record(source, "read", False)
        assert runtime.pipeline.matcher.match(document, ClassificationResult()).subject_id != "canonical-linear"


def test_catalog_sync_reconciles_prior_documents_without_reprocessing_and_preserves_overrides(settings):
    from backend.app.contracts.sources import ChangePage, Provenance, Subject
    from backend.app.core.database import documents as document_table, records

    source = SourceKind.PAMPLEMOUSSE
    connector = type("CatalogConnector", (), {
        "list_changes": lambda self, scope, cursor: ChangePage(items=[Subject(
            provenance=Provenance(external_id="pamp:subject:linear", source=source),
            title="Linear Algebra", code="ALG201",
        )]),
    })()
    fetches = {"count": 0}

    def fetch(_record):
        fetches["count"] += 1
        return b"Synthetic Linear Algebra lesson with reusable content."

    components = Components(adapters={source: Auth()}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        from backend.app.core.runtime import _PersistedCatalogMatcher

        drive = SourceKind.ENSORA_DRIVE
        runtime.consent.record(drive, "read", True)
        moodle = SourceKind.MOODLE
        runtime.consent.record(moodle, "read", True)
        runtime.consent.record(source, "read", True)
        runtime.pipeline.matcher = _PersistedCatalogMatcher(runtime)
        document_ids = []
        for external_id in ("drive:linear:lesson", "drive:linear:manual"):
            document = Document(
                title="Linear Algebra",
                mime_type="text/plain",
                provenance=Provenance(external_id=external_id, source=drive,
                                      folder="Ensora/2A/Linear Algebra"),
            )
            runtime.pipeline.ingest(runtime.connection_ids[drive], document, fetch)
            document_ids.append(external_id)
        moodle_external_id = "moodle:course:demo:resource:linear"
        runtime.pipeline.ingest(runtime.connection_ids[moodle], Document(
            title="Linear Algebra", mime_type="text/plain",
            provenance=Provenance(external_id=moodle_external_id, source=moodle),
        ), fetch)

        with runtime.engine.begin() as db:
            manual_id = db.scalar(select(document_table.c.id).where(
                document_table.c.connection_id == runtime.connection_ids[drive],
                document_table.c.external_id == document_ids[1],
            ))
            current = db.scalar(select(document_table.c.metadata_json).where(document_table.c.id == manual_id))
            db.execute(document_table.update().where(document_table.c.id == manual_id).values(
                metadata_json={**current, "user_override": {"subject_id": "user-confirmed-subject"},
                               "subject_id": "user-confirmed-subject"},
            ))

        # Catalog synchronization follows the prior document acquisition and triggers
        # a metadata-only pass over currently authorized document sources.
        run = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        report = run["report"][source]
        assert report["status"] == "succeeded"
        assert report["metrics"]["catalog_documents_matched"] == 2
        assert report["metrics"]["catalog_documents_skipped"] == 1

        with runtime.engine.connect() as db:
            rows = db.execute(select(document_table.c.external_id, document_table.c.metadata_json,
                                     document_table.c.active_version_id).where(
                document_table.c.connection_id.in_((runtime.connection_ids[drive], runtime.connection_ids[moodle])),
            )).mappings().all()
            assert db.scalar(select(func.count()).select_from(versions)) == 3
            assert db.scalar(select(func.count()).select_from(chunks)) == 3
            subject = db.scalar(select(records.c.external_id).where(
                records.c.connection_id == runtime.connection_ids[source], records.c.kind == "subject",
            ))
        by_external_id = {row["external_id"]: row for row in rows}
        auto = by_external_id[document_ids[0]]["metadata_json"]
        manual = by_external_id[document_ids[1]]["metadata_json"]
        assert auto["subject_id"] == subject
        assert auto["match"]["confirmed"] is True
        assert by_external_id[moodle_external_id]["metadata_json"]["subject_id"] == subject
        assert manual["subject_id"] == "user-confirmed-subject"
        assert manual["user_override"]["subject_id"] == "user-confirmed-subject"
        assert fetches["count"] == 3

        # Consent withdrawal prevents later reconciliation reads of those documents.
        runtime.consent.record(drive, "read", False)
        counts = runtime.reconcile_academic_documents(source)
        assert counts["examined"] == 0 and counts["matched"] == 0


def test_document_match_confirmation_is_consent_scoped_and_survives_source_refresh(settings):
    from backend.app.contracts.sources import Subject

    app = create_app(settings, Components())

    async def verify():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            source = SourceKind.LOCAL
            catalog = SourceKind.ACADEMIC_IMPORT
            runtime.consent.record(source, "read", True)
            runtime.consent.record(catalog, "read", True)
            subject = Subject(provenance=Provenance(external_id="import:subject:stats", source=catalog),
                             title="Statistics", code="STAT201")
            assert runtime.pipeline.import_academics(runtime.connection_ids[catalog], [subject])[0]["status"] == "records"
            doc = Document(title="Statistics", mime_type="text/plain",
                           provenance=Provenance(external_id="local:statistics", source=source,
                                                 remote_revision="revision-1"))
            runtime.pipeline.ingest(runtime.connection_ids[source], doc,
                                    lambda _: b"Synthetic statistics lecture notes.")
            with runtime.engine.connect() as db:
                doc_id = db.scalar(select(documents.c.id).where(
                    documents.c.connection_id == runtime.connection_ids[source],
                    documents.c.external_id == "local:statistics",
                ))

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                invalid = await client.put(f"/api/v1/documents/{doc_id}/match",
                                           json={"subject_id": "missing-subject"})
                assert invalid.status_code == 422
                runtime.consent.record(catalog, "read", False)
                unconsented = await client.put(f"/api/v1/documents/{doc_id}/match",
                                               json={"subject_id": "import:subject:stats"})
                assert unconsented.status_code == 422
                runtime.consent.record(catalog, "read", True)
                confirmed = await client.put(f"/api/v1/documents/{doc_id}/match",
                                             json={"subject_id": "import:subject:stats", "chapter_id": "Chapter 2"})
                assert confirmed.status_code == 200
                assert confirmed.json()["confirmed"] is True
                assert confirmed.json()["chapter_id"] == "Chapter 2"

            updated_doc = doc.model_copy(update={
                "provenance": doc.provenance.model_copy(update={"remote_revision": "revision-2"}),
            })
            runtime.pipeline.ingest(runtime.connection_ids[source], updated_doc,
                                    lambda _: b"Synthetic updated statistics lecture notes.")
            with runtime.engine.connect() as db:
                row = db.execute(select(documents.c.metadata_json, documents.c.active_version_id).where(
                    documents.c.id == doc_id,
                )).one()
                assert db.scalar(select(func.count()).select_from(versions)) == 2
            assert row.metadata_json["subject_id"] == "import:subject:stats"
            assert row.metadata_json["chapter_id"] == "Chapter 2"
            assert row.metadata_json["user_override"] == {
                "subject_id": "import:subject:stats", "chapter_id": "Chapter 2",
            }
            assert row.metadata_json["match"]["confirmed"] is True

            runtime.consent.record(source, "read", False)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                hidden = await client.put(f"/api/v1/documents/{doc_id}/match",
                                          json={"subject_id": "import:subject:stats"})
                assert hidden.status_code == 404

    asyncio.run(verify())


def test_round2_configured_origin_and_private_local_imports(settings, monkeypatch):
    from backend.app.core.settings import ProtectedImportPath

    settings.prepare()
    monkeypatch.setenv("ENSORA_SOURCES_ENV", str(settings.config_dir / "sources.env"))
    monkeypatch.setenv("LOCAL_DATA_DIR", str(settings.local_data_dir))
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://127.0.0.1:5188")
    private = settings.config_dir / "secrets" / "example.json"
    private.write_text("{}")
    alias = settings.config_dir.parent / "alias"
    alias.symlink_to(private)
    app = create_app(components=Components())

    async def run():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            assert str(runtime.settings.frontend_origin).endswith(":5188/")
            with pytest.raises(ProtectedImportPath):
                runtime.validate_local_import_path(alias)
            with pytest.raises(ProtectedImportPath):
                runtime.validate_local_import_path(settings.config_dir.parent)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                good = await client.options("/api/v1/health", headers={
                    "Origin": "http://127.0.0.1:5188", "Access-Control-Request-Method": "GET",
                })
                assert good.headers["access-control-allow-origin"] == "http://127.0.0.1:5188"
                wrong = await client.options("/api/v1/health", headers={
                    "Origin": "http://127.0.0.1:5173", "Access-Control-Request-Method": "GET",
                })
                assert wrong.status_code == 400
                client.headers["Authorization"] = f"Bearer {runtime.api_token}"
                for path in (private, alias, settings.local_data_dir):
                    blocked = await client.post("/api/v1/sync", json={"source": "local", "folder_id": str(path)})
                    assert blocked.status_code == 403

    asyncio.run(run())


def test_round2_long_cursor_snapshot_recovery_and_no_change(settings):
    from backend.app.contracts.sources import CalendarEvent, CalendarSnapshot
    from backend.app.core.database import connections, record_history, records

    source = SourceKind.GOOGLE_CALENDAR
    long_cursor = "page:" + "opaque" * 180
    snapshot = CalendarSnapshot.model_validate({
        "external_id_prefix": "google_calendar:primary:",
        "starts_at": "2026-01-01T00:00:00Z", "ends_at": "2026-02-01T00:00:00Z",
    })

    def event(identifier, month="01", **extra):
        return CalendarEvent.model_validate({
            "provenance": {"external_id": identifier}, "title": "Busy",
            "starts_at": f"2026-{month}-15T10:00:00Z", "ends_at": f"2026-{month}-15T11:00:00Z", **extra,
        })

    kept = event("google_calendar:primary:kept")
    missing = event("google_calendar:primary:missing")
    other = event("google_calendar:other:present")
    outside = event("google_calendar:primary:outside", "02")

    class Calendar:
        fail = True
        snapshots = True

        def list_changes(self, scope, cursor):
            if not self.snapshots:
                assert cursor == "recovered"
                return ChangePage()
            if cursor != long_cursor:
                assert cursor == "prior"
                return ChangePage(items=[kept], complete=False, next_cursor=long_cursor,
                                  calendar_snapshot=snapshot)
            errors = ["source_listing_error"] if self.fail else []
            return ChangePage(resume_cursor="recovered", error_codes=errors)

    connector = Calendar()
    with Runtime(settings, Components(adapters={source: Auth()}, connectors={source: connector})) as runtime:
        identifier = runtime.connection_ids[source]
        runtime.consent.record(source, "read", True)
        for item in (kept, missing, other, outside):
            runtime.pipeline.ingest(identifier, item)
        with runtime.engine.begin() as db:
            db.execute(connections.update().where(connections.c.id == identifier).values(cursor="prior"))

        def snapshot_state():
            with runtime.engine.connect() as db:
                cursor = db.scalar(select(connections.c.cursor).where(connections.c.id == identifier))
                states = {row.external_id: row.payload["status"]
                          for row in db.execute(select(records.c.external_id, records.c.payload))}
                history_count = db.scalar(select(func.count()).select_from(record_history))
                return cursor, states, history_count

        scope = SyncScope(source=source)
        assert await_job(runtime, runtime.sync.submit(scope))["status"] == "partial"
        assert snapshot_state() == ("prior", {x.provenance.external_id: "confirmed" for x in
                                              (kept, missing, other, outside)}, 4)
        connector.fail = False
        assert await_job(runtime, runtime.sync.submit(scope))["status"] == "succeeded"
        checkpoint, states, history = snapshot_state()
        assert checkpoint == "recovered" and history == 5
        assert [key for key, status in states.items() if status == "cancelled"] == [missing.provenance.external_id]
        connector.snapshots = False
        assert await_job(runtime, runtime.sync.submit(scope))["status"] == "succeeded"
        assert snapshot_state()[0] == "recovered"


def test_round2_availability_has_uniform_items_and_utc_order(settings):
    from backend.app.contracts.sources import CalendarEvent

    app = create_app(settings, Components())
    async def run():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            source = SourceKind.GOOGLE_CALENDAR
            runtime.consent.record(source, "read", True)
            runtime.pipeline.ingest(runtime.connection_ids[source], CalendarEvent.model_validate({
                "title": "Synthetic private title",
                "provenance": {"external_id": "google_calendar:primary:all-day"},
                "all_day_start": "2026-01-15", "all_day_end": "2026-01-16", "original_timezone": "Europe/Paris",
            }))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                manual = await client.post("/api/v1/availability", json={
                    "starts_at": "2026-01-15T00:15:00+02:00", "ends_at": "2026-01-15T02:15:00+02:00",
                })
                assert manual.status_code == 201
                first = (await client.get("/api/v1/availability?limit=1")).json()
                second = (await client.get("/api/v1/availability",
                                          params={"cursor": first["next_cursor"]})).json()
                assert [row["source"] for row in first["items"] + second["items"]] == ["manual", "google_calendar"]
                assert all(set(row) == {"id", "source", "payload"} for row in first["items"] + second["items"])
                assert second["items"][0]["payload"]["available"] is False
                assert "Synthetic private title" not in str(second)
                runtime.consent.record(source, "read", False)
                assert len((await client.get("/api/v1/availability")).json()["items"]) == 1

    asyncio.run(run())


def test_round2_fetched_metadata_reaches_version_and_parser(settings):
    from backend.app.contracts.sources import FetchedDocument

    with Runtime(settings, Components()) as runtime:
        document = Document(title="Remote export", provenance=Provenance(external_id="synthetic"))
        runtime.pipeline.ingest(runtime.connection_ids[SourceKind.LOCAL], document, lambda _: FetchedDocument(
            content=b"Synthetic contents", mime_type="text/plain", filename="converted.txt",
        ))
        with runtime.engine.connect() as db:
            version = db.execute(select(versions)).mappings().one()
            assert (version["filename"], version["mime_type"], version["status"]) == ("converted.txt", "text/plain", "ready")
            assert db.scalar(select(chunks.c.payload))["text"] == "Synthetic contents"


def test_round2_p2_version_evidence_and_p3_nested_academics(settings):
    app = create_app(settings)

    async def run():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            for source in (SourceKind.LOCAL, SourceKind.ACADEMIC_IMPORT):
                runtime.consent.record(source, "read", True)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                lecture = b"# Lecture\n\nPrerequisites: Synthetic linear algebra.\n\nTheorem 1: The sample is nonempty.\n"
                uploaded = await client.post("/api/v1/documents/import", files={
                    "file": ("lecture.md", lecture, "text/markdown"),
                })
                assert uploaded.status_code == 201
                doc = (await client.get("/api/v1/documents")).json()["items"][0]
                version_path = f"/api/v1/documents/{doc['id']}/versions/{doc['active_version_id']}"
                chunks_payload = (await client.get(version_path + "/chunks")).json()
                chunk_ids = {item["id"] for item in chunks_payload["items"]}
                evidence = (await client.get(version_path + "/concept-candidates")).json()
                assert evidence["items"][0]["evidence"][0]["chunk_id"] in chunk_ids
                assert evidence["prerequisite_hints"][0]["chunk_id"] in chunk_ids
                records = [
                    {"kind": "subject", "title": "Synthetic course", "provenance": {"external_id": "subject"}},
                    {"kind": "coefficient", "title": "Course coefficient", "parent_key": "subject",
                     "weight": "3", "provenance": {"external_id": "coefficient"}},
                    {"kind": "assessment", "title": "Assessment", "parent_key": "subject",
                     "provenance": {"external_id": "assessment"}},
                    {"kind": "assessment_weight", "title": "Parent weight", "parent_key": "assessment",
                     "weight": "1", "provenance": {"external_id": "parent-weight"}},
                    {"kind": "assessment_weight", "title": "Child weight", "parent_key": "parent-weight",
                     "weight": "0.5", "mode": "fraction", "provenance": {"external_id": "child-weight"}},
                    {"kind": "grade", "title": "Published grade", "parent_key": "assessment",
                     "value": "0", "maximum": "20", "provenance": {"external_id": "grade"}},
                ]
                assert (await client.post("/api/v1/academic/import", json=records)).status_code == 201
                weights = (await client.get("/api/v1/assessment-weights")).json()["items"]
                by_key = {item["external_id"]: item for item in weights}
                assert by_key["child-weight"]["parent_id"] == by_key["parent-weight"]["id"]
                assert (await client.get("/api/v1/grades")).json()["items"][0]["payload"]["value"] == "0"
    asyncio.run(run())


def test_round2_calendar_410_bridge_retires_only_missing_scoped_events(settings, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from backend.app.contracts.sources import CalendarEvent
    from backend.app.core.database import connections, records
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge, runtime_config_from_codex_settings
    from backend.app.modules.ingestion.connectors.base import RateLimiter
    from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector

    calls = []
    source = SourceKind.GOOGLE_CALENDAR
    start = datetime.now(UTC) + timedelta(days=2)
    end = start + timedelta(hours=1)

    class Gone(Exception):
        resp = SimpleNamespace(status=410)

    class RemoteEvents:
        def list(self, **params):
            calls.append(params)
            if params.get("syncToken") == "stale":
                raise Gone()
            return SimpleNamespace(execute=lambda: {
                "nextSyncToken": "fresh", "items": [{
                    "id": "known", "summary": "Synthetic private summary",
                    "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()},
                }],
            })

    provider = GoogleCalendarConnector(runtime_config_from_codex_settings(settings), rate_limiter=RateLimiter(0))
    monkeypatch.setattr(provider, "_build_service", lambda: SimpleNamespace(events=RemoteEvents))
    bridge = CodexConnectorBridge(provider)
    with Runtime(settings, Components(adapters={source: Auth()}, connectors={source: bridge})) as runtime:
        identifier = runtime.connection_ids[source]
        runtime.consent.record(source, "read", True)
        runtime.pipeline.ingest(identifier, CalendarEvent(
            provenance=Provenance(external_id="google_calendar:primary:gone"),
            title="Busy", starts_at=start, ends_at=end,
        ))
        with runtime.engine.begin() as db:
            db.execute(connections.update().where(connections.c.id == identifier).values(cursor="sync:stale"))
        result = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        assert result["status"] == "succeeded"
        assert calls[0]["syncToken"] == "stale" and "syncToken" not in calls[1]
        assert all(request["singleEvents"] is True for request in calls)
        with runtime.engine.connect() as db:
            items = {row.external_id: row.payload for row in db.execute(select(records.c.external_id, records.c.payload))}
            assert items["google_calendar:primary:gone"]["status"] == "cancelled"
            assert items["google_calendar:primary:known"]["title"] == "Busy"
            assert "fresh" in db.scalar(select(connections.c.cursor).where(connections.c.id == identifier))


def test_round2_catalog_overrides_and_exam_candidates_remain_consent_scoped(settings):
    from backend.app.contracts.sources import ClassificationResult, Subject
    from sqlalchemy import update

    app = create_app(settings)
    async def run():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            local = SourceKind.LOCAL
            academic = SourceKind.ACADEMIC_IMPORT
            runtime.consent.record(local, "read", True)
            runtime.consent.record(academic, "read", True)
            runtime.pipeline.ingest(runtime.connection_ids[academic], Subject(
                provenance=Provenance(external_id="subject-ml"), title="Machine Learning", code="ML",
            ))
            exam = Document(provenance=Provenance(external_id="exam", source=local, folder="ML"),
                            title="Partiel 2025 ML.md", mime_type="text/markdown")
            correction = Document(provenance=Provenance(external_id="correction", source=local, folder="ML"),
                                  title="Corrige Partiel 2025 ML.md", mime_type="text/markdown")
            for doc in (exam, correction):
                runtime.pipeline.ingest(runtime.connection_ids[local], doc, lambda _: b"Synthetic assessment.")

            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                candidates = (await client.get("/api/v1/documents/exam-correction-candidates")).json()["items"]
                assert [(item["exam_external_id"], item["correction_external_id"])
                        for item in candidates] == [("exam", "correction")]
                with runtime.engine.begin() as db:
                    query = documents.c.external_id == "exam"
                    metadata = db.scalar(select(documents.c.metadata_json).where(query))
                    metadata["user_override"] = {"subject_id": "manual-subject", "chapter_id": "manual-chapter"}
                    db.execute(update(documents).where(query).values(metadata_json=metadata))
                assert runtime.pipeline.matcher.match(exam, ClassificationResult()).subject_id == "manual-subject"
                runtime.pipeline.ingest(runtime.connection_ids[local], exam, lambda _: b"Revised source bytes.")
                with runtime.engine.connect() as db:
                    updated = db.scalar(select(documents.c.metadata_json).where(documents.c.external_id == "exam"))
                assert updated["chapter_id"] == "manual-chapter"
                runtime.consent.record(local, "read", False)
                assert (await client.get("/api/v1/documents/exam-correction-candidates")).json()["items"] == []
                assert runtime.pipeline.matcher.match(exam, ClassificationResult()).subject_id != "manual-subject"
    asyncio.run(run())


@pytest.mark.parametrize("challenge_at", ["listing", "download"])
def test_ensai_cas_expiration_resumes_same_job_scope(settings, challenge_at):
    from backend.app.contracts.sources import CalendarEvent, Subject
    from backend.app.core.database import connections, records
    from backend.app.core.sync import ResumeUnavailable

    source = SourceKind.MOODLE if challenge_at == "listing" else SourceKind.PAMPLEMOUSSE

    class InstitutionalAuth:
        interactive_calls = 0
        verified = False

        def validate(self):
            # Simulates a legacy landing-page probe which used to claim a working service.
            return AuthenticationResult(status=ConnectionStatus.CONNECTED,
                                        session_state="active" if self.verified else "unknown",
                                        service_access="verified" if self.verified else "unknown")

        def reauthorize(self, inputs):
            self.interactive_calls += 1
            self.verified = True
            return self.validate()

    adapter = InstitutionalAuth()
    subject = Subject(title="Fixture subject", provenance=Provenance(external_id="fixture-course"))
    document = Document(title="Fixture", mime_type="text/plain",
                        provenance=Provenance(external_id="lesson", remote_revision="1"))
    event = CalendarEvent.model_validate({
        "title": "Fixture lesson", "provenance": {"external_id": "class"},
        "starts_at": "2026-11-27T09:00:00Z", "ends_at": "2026-11-27T10:00:00Z",
    })

    class InstitutionalConnector:
        cursors = []

        def list_changes(self, scope, cursor):
            self.cursors.append(cursor)
            if cursor == "durable-before":
                return ChangePage(items=[subject], complete=False, next_cursor="page-two")
            assert cursor in ("page-two", "durable-after")
            if cursor == "durable-after":
                return ChangePage()
            if challenge_at == "listing" and not adapter.verified:
                raise ReauthenticationRequired(reason="cas_login_required")
            return ChangePage(items=[document, event], resume_cursor="durable-after")

        def fetch_blob(self, item):
            if challenge_at == "download" and not adapter.verified:
                raise ReauthenticationRequired(reason="cas_login_required")
            return b"Synthetic lecture with persisted text."

    connector = InstitutionalConnector()
    components = Components(adapters={source: adapter}, connectors={source: connector})
    scope = SyncScope(source=source)
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.consent.record(source, "automatic_sync", True)
        identifier = runtime.connection_ids[source]
        with runtime.engine.begin() as db:
            db.execute(connections.update().where(connections.c.id == identifier).values(cursor="durable-before"))
        blocked = await_job(runtime, runtime.sync.submit(scope, automatic=True))
        result = blocked["report"][source]
        assert blocked["status"] == "partial"
        assert (result["connection_status"], result["auth_reason"], result["next_action"]) == (
            "REAUTH_REQUIRED", "cas_login_required", "reauthorize",
        )
        assert adapter.interactive_calls == 0
        assert runtime.auth.status(source) == ConnectionStatus.REAUTH_REQUIRED
        assert runtime.auth.validate(source).reason == "service_verification_required"
        with pytest.raises(ResumeUnavailable, match="Reauthorize"):
            runtime.sync.resume(blocked["id"], source)
        with runtime.engine.connect() as db:
            assert db.scalar(select(connections.c.cursor).where(connections.c.id == identifier)) == "durable-before"
            assert db.scalar(select(func.count()).select_from(records)) == 1

    app = create_app(settings, components)
    async def verify():
        async with app.router.lifespan_context(app):
            runtime = app.state.runtime
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {runtime.api_token}"}) as client:
                connection = runtime.connection_ids[source]
                assert (await client.post(f"/api/v1/connections/{connection}/reauthorize")).json()["service_access"] == "verified"
                first = await client.post(f"/api/v1/jobs/{blocked['id']}/resume", json={"source": source})
                repeated = await client.post(f"/api/v1/jobs/{blocked['id']}/resume")
                assert first.status_code == 202 and repeated.json() == first.json()
                finished = await asyncio.to_thread(await_job, runtime, first.json()["id"])
                assert finished["status"] == "succeeded" and finished["scope"] == scope.model_dump(mode="json")
                persisted = (await client.get("/api/v1/documents")).json()["items"][0]
                path = f"/api/v1/documents/{persisted['id']}/versions/{persisted['active_version_id']}/chunks"
                assert "persisted text" in (await client.get(path)).json()["items"][0]["payload"]["text"]
                assert len((await client.get("/api/v1/subjects")).json()["items"]) == 1
                assert len((await client.get("/api/v1/events")).json()["items"]) == 1
                with runtime.engine.connect() as db:
                    assert db.scalar(select(connections.c.cursor).where(connections.c.id == connection)) == "durable-after"
                    assert db.scalar(select(func.count()).select_from(versions)) == 1
    asyncio.run(verify())
    # The first page was committed before CAS expired, so the successor resumes
    # from its durable per-job checkpoint instead of replaying the source cursor.
    assert connector.cursors == ["durable-before", "page-two", "page-two"]
    assert adapter.interactive_calls == 1


def test_ensai_cas_session_does_not_imply_service_authorization(settings):
    from backend.app.contracts.authentication import ServiceAccessDenied

    source = SourceKind.PAMPLEMOUSSE
    class Adapter:
        result = AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active")

        def validate(self):
            if self.result is None:
                raise ServiceAccessDenied()
            return self.result

    adapter = Adapter()
    with Runtime(settings, Components(adapters={source: adapter})) as runtime:
        runtime.consent.record(source, "read", True)
        first = runtime.auth.validate(source)
        assert first.status == ConnectionStatus.REAUTH_REQUIRED and first.reason == "service_verification_required"
        adapter.result = None
        denied = runtime.auth.validate(source)
        assert denied.status == ConnectionStatus.ACCESS_DENIED and denied.reason == "service_access_denied"
        runtime.consent.record(source, "read", False)
        assert runtime.auth.reauthorize(source).reason == "consent_required"


def test_ensai_bundled_runtime_requires_protected_service_evidence(settings):
    source = SourceKind.MOODLE
    adapter = Auth()
    with Runtime(settings) as runtime:
        assert runtime.auth.locks[SourceKind.MOODLE] is runtime.auth.locks[SourceKind.PAMPLEMOUSSE]
        runtime.auth.adapters[source] = adapter
        runtime.consent.record(source, "read", True)
        assert runtime.auth.validate(source).reason == "service_verification_required"
        adapter.validate = lambda: AuthenticationResult(status=ConnectionStatus.CONNECTED, service_access="verified")
        assert runtime.auth.validate(source).status == ConnectionStatus.CONNECTED


@pytest.mark.parametrize("source", [SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE])
@pytest.mark.parametrize("operation", ["authorize", "reauthorize"])
def test_verified_interactive_cas_and_fresh_profile_probe_are_separate(settings, monkeypatch, caplog, source, operation):
    from backend.app.modules.ingestion.auth.ensai import EnsaiAuthAdapter
    from backend.app.modules.ingestion.contracts import AuthState, AuthStatus

    calls = []

    def interactive(self, kind, **options):
        assert options["interactive"] is True
        calls.append(("interactive", self.profile_dir_for(kind)))
        return AuthStatus(source=kind, state=AuthState.AUTHENTICATED, authenticated=True,
                          detail="synthetic protected content verified",
                          metadata={"origin_verified": True, "cas_redirect_detected": False,
                                    "diagnostics": {"phase": "completed",
                                                    "navigation_state": "on_protected_resource",
                                                    "redirect_completed": True,
                                                    "session_persisted": True,
                                                    "service_cookies_present": True}})

    def fresh_context(self, kind, **options):
        assert options["headless"] is True
        calls.append(("headless", self.profile_dir_for(kind)))
        return AuthStatus(source=kind, state=AuthState.NEEDS_LOGIN, authenticated=False,
                          detail="synthetic private upstream detail")

    monkeypatch.setattr(EnsaiAuthAdapter, "authenticate", interactive)
    monkeypatch.setattr(EnsaiAuthAdapter, "check_source_connection", fresh_context)
    caplog.set_level("DEBUG", logger="backend.app.core.auth")

    with Runtime(settings) as runtime:
        runtime.consent.record(source, "read", True)
        connected = getattr(runtime.auth, operation)(source)
        assert connected.status == ConnectionStatus.CONNECTED and connected.service_access == "verified"
        assert calls == [("interactive", settings.ensai_profile_dir)]
        assert runtime.auth.status(source) == ConnectionStatus.CONNECTED
        expired = runtime.auth.validate(source)
        assert expired.status == ConnectionStatus.REAUTH_REQUIRED
        assert expired.reason == "cas_login_required"
        assert calls[-1] == ("headless", settings.ensai_profile_dir)
        assert runtime.auth.status(source) == ConnectionStatus.REAUTH_REQUIRED
        assert "synthetic private upstream detail" not in caplog.text
        assert "fallback_probe" not in caplog.text
        assert "operation=validate stage=adapter_result status=REAUTH_REQUIRED" in caplog.text


def test_interactive_cas_without_protected_evidence_requires_real_probe(settings):
    source = SourceKind.MOODLE

    class Adapter:
        def authorize(self, inputs):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active")

        def validate(self):
            raise ReauthenticationRequired("cas_login_required")

    with Runtime(settings, Components(adapters={source: Adapter()})) as runtime:
        runtime.consent.record(source, "read", True)
        result = runtime.auth.authorize(source)
        assert result.status == ConnectionStatus.REAUTH_REQUIRED
        assert result.reason == "cas_login_required"
        assert runtime.auth.status(source) == ConnectionStatus.REAUTH_REQUIRED


def test_existing_ensai_consent_onboarding_uses_one_pre_auth_probe(settings, monkeypatch):
    from scripts import onboarding

    source = SourceKind.MOODLE
    calls = []

    class Adapter:
        def validate(self):
            calls.append("headless")
            return AuthenticationResult(status=ConnectionStatus.REAUTH_REQUIRED,
                                        session_state="reauth_required", reason="cas_login_required")

        def reauthorize(self, inputs):
            calls.append("interactive")
            return AuthenticationResult(status=ConnectionStatus.CONNECTED, session_state="active",
                                        service_access="verified")

    answers = iter([True, True, False])
    monkeypatch.setattr(onboarding, "yes", lambda _: next(answers))
    with Runtime(settings, Components(adapters={source: Adapter()})) as runtime:
        runtime.consent.record(source, "read", True)
        onboarding.run_onboarding(runtime, source)
        assert calls == ["headless", "interactive"]
        assert runtime.auth.status(source) == ConnectionStatus.CONNECTED


def test_live_etl_acceptance_moodle_stage_counts_and_version_provenance(settings):
    from backend.app.core.audit import audit_database
    from backend.app.modules.ingestion.codex_bridge import CodexParserBridge

    source = SourceKind.MOODLE
    year = "2026-2027"
    first = Document(title="Synthetic private course heading", resource_kind="educational_html",
                     course_id="fixture-course", section_id="section-1", section_path=["Week 1"],
                     provenance=Provenance(external_id="section-1:copy", academic_year=year,
                                           source_url="https://example.invalid/course?section=1"))
    second = first.model_copy(update={
        "section_id": "section-2", "section_path": ["Week 2"],
        "provenance": Provenance(external_id="section-2:copy", academic_year=year),
    })
    unverified = first.model_copy(update={"provenance": Provenance(external_id="unknown-year")})

    class Resources:
        payload = b"<p>Synthetic extraction</p><script>never execute</script>"
        fetches = 0

        def list_changes(self, scope, cursor):
            assert cursor is None
            return ChangePage(items=[first, second, unverified],
                              observations={"courses_discovered": 1, "sections_discovered": 2, "sections_traversed": 2})

        def fetch_blob(self, doc):
            from backend.app.contracts.sources import FetchedDocument
            self.fetches += 1
            return FetchedDocument(content=self.payload, mime_type="text/html", filename="lesson.html")

    resources = Resources()
    components = Components(adapters={source: Auth()}, connectors={source: resources}, parser=CodexParserBridge())
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        discovery = await_job(runtime, runtime.sync.submit(SyncScope(source=source, discovery_only=True)))
        assert discovery["report"][source]["metrics"]["documents_discovered"] == 3
        assert resources.fetches == 0
        scope = SyncScope(source=source, academic_year=year)
        synced = await_job(runtime, runtime.sync.submit(scope))
        assert synced["status"] == "partial"
        counts = synced["report"][source]["metrics"]
        assert counts["documents_downloaded"] == counts["documents_parsed"] == counts["documents_persisted"] == 2
        assert counts["academic_year_unknown"] == 1
        with runtime.engine.connect() as db:
            assert db.scalar(select(func.count()).select_from(documents)) == 2
            assert all("never execute" not in item["text"] for item in db.scalars(select(chunks.c.payload)))
        report = audit_database(settings.local_data_dir / "ensora.sqlite3", synced["id"])
        assert report["cumulative"]["moodle"]["duplicate_blob_references"] == 1
        assert report["run"]["sources"]["moodle"]["sections_traversed"] == 2
        assert "Synthetic private" not in str(report) and "example.invalid" not in str(report)
        resources.payload = b"<p>Synthetic update</p>"
        await_job(runtime, runtime.sync.submit(scope))
        with runtime.engine.connect() as db:
            version_rows = db.scalars(select(versions.c.source_metadata)).all()
            assert len(version_rows) == 4
            assert {row["section_id"] for row in version_rows} == {"section-1", "section-2"}


def test_live_etl_academic_override_and_calendar_privacy(settings):
    from backend.app.contracts.sources import CalendarEvent, CalendarSnapshot

    source = SourceKind.GOOGLE_CALENDAR
    busy = CalendarEvent.model_validate({
        "title": "Synthetic calendar private example",
        "provenance": {"external_id": "google_calendar:primary:busy", "source_url": "https://example.invalid/secret"},
        "starts_at": "2026-11-27T10:00:00+01:00", "ends_at": "2026-11-27T11:00:00+01:00",
        "room": "private-room",
    })
    free = busy.model_copy(update={
        "busy": False,
        "provenance": Provenance(external_id="google_calendar:primary:free"),
    })

    class Calendar:
        cancelled = False

        def list_changes(self, scope, cursor):
            assert cursor is None
            event = busy if not self.cancelled else CalendarEvent(
                provenance=busy.provenance, title="Private cancellation", status="cancelled",
            )
            return ChangePage(items=[event, free], calendar_snapshot=CalendarSnapshot(
                external_id_prefix="google_calendar:primary:",
                starts_at=scope.window_start, ends_at=scope.window_end,
            ))

    calendar = Calendar()
    app = create_app(settings, Components(adapters={source: Auth()}, connectors={source: calendar}))
    async def verify():
        async with app.router.lifespan_context(app):
            rt = app.state.runtime
            for consent in (source, SourceKind.ACADEMIC_IMPORT):
                rt.consent.record(consent, "read", True)
            scope = SyncScope.model_validate({"source": source, "window_start": "2026-11-27T00:00:00Z",
                                               "window_end": "2026-11-28T00:00:00Z"})
            await asyncio.to_thread(await_job, rt, rt.sync.submit(scope))
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                                   headers={"Authorization": f"Bearer {rt.api_token}"}) as client:
                events = (await client.get("/api/v1/events")).json()["items"]
                assert len(events) == 2 and "private" not in str(events) and "example.invalid" not in str(events)
                assert all(item["payload"]["duration_minutes"] == 60 for item in events)
                intervals = (await client.get("/api/v1/availability")).json()["items"]
                assert len(intervals) == 1 and intervals[0]["payload"]["starts_at"].startswith("2026-11-27T09:")
                calendar.cancelled = True
                await asyncio.to_thread(await_job, rt, rt.sync.submit(scope))
                assert (await client.get("/api/v1/availability")).json()["items"] == []
                records = [
                    {"kind": "academic_year", "title": "Fixture year", "provenance": {"external_id": "year"}},
                    {"kind": "semester", "title": "S1", "parent_key": "year", "provenance": {"external_id": "semester"}},
                    {"kind": "teaching_unit", "title": "UE", "parent_key": "semester", "provenance": {"external_id": "ue"}},
                    {"kind": "subject", "title": "Subject", "parent_key": "ue", "ects": "4.5",
                     "provenance": {"external_id": "subject"}},
                    {"kind": "coefficient", "title": "Coefficient", "parent_key": "subject", "weight": "0",
                     "level": "subject", "provenance": {"external_id": "coef"}},
                    {"kind": "grade", "title": "Pending", "record_status": "missing", "parent_key": "subject",
                     "value": None, "grade_kind": "official_aggregate", "provenance": {"external_id": "grade"}},
                ]
                assert (await client.post("/api/v1/academic/import", json=records)).status_code == 201
                coefficients = (await client.get("/api/v1/coefficients")).json()["items"]
                coefficient = coefficients[0]
                assert (await client.put(f"/api/v1/academic/records/{coefficient['id']}/override",
                                         json={"fields": {"weight": "4"}})).status_code == 200
                records[4]["weight"] = "3"
                assert (await client.post("/api/v1/academic/import", json=records)).status_code == 201
                coefficient = (await client.get("/api/v1/coefficients")).json()["items"][0]
                assert coefficient["payload"]["weight"] == "3"
                assert coefficient["effective_payload"]["weight"] == "4"
                assert (await client.get("/api/v1/subjects")).json()["items"][0]["payload"]["ects"] == "4.5"
                assert (await client.get("/api/v1/grades")).json()["items"][0]["payload"]["value"] is None
    asyncio.run(verify())


def test_pamplemousse_bridge_persists_academic_hierarchy_and_missing_values(settings):
    from backend.app.core.audit import audit_database
    from backend.app.core.database import records
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
    from backend.app.modules.ingestion.contracts import ChangePage as InternalPage, SourceKind as InternalSourceKind
    from backend.app.modules.ingestion.contracts import SourceRecord as InternalRecord

    source = SourceKind.PAMPLEMOUSSE
    year_id = "pamplemousse:year:2026-2027"
    semester_id = "pamplemousse:semester:s5"
    ue_id = "pamplemousse:ue:data"
    subject_id = "pamplemousse:subject:ml"
    records_in = [
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id=year_id,
                       title="2026-2027", record_kind="academic",
                       payload={"record_type": "academic_year", "academic_year": "2026-2027", "title": "2026-2027"}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id=semester_id,
                       title="S5", record_kind="academic",
                       payload={"record_type": "semester", "academic_year": "2026-2027",
                                "parent_external_id": year_id, "title": "S5"}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id="pamplemousse:aggregate:year",
                       title="Annual aggregate", record_kind="academic",
                       payload={"record_type": "aggregate_grade", "parent_external_id": year_id,
                                "title": "Annual aggregate", "weight": None, "weight_status": "missing",
                                "grade_value": "14", "provenance": {"aggregate_scope": "overall"}}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id="pamplemousse:aggregate:semester",
                       title="Semester aggregate", record_kind="academic",
                       payload={"record_type": "aggregate_grade", "parent_external_id": semester_id,
                                "title": "Semester aggregate", "weight": "4", "weight_mode": "raw",
                                "grade_value": "13", "provenance": {"aggregate_scope": "semester"}}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id=ue_id,
                       title="UE Data", record_kind="academic",
                       payload={"record_type": "teaching_unit", "academic_year": "2026-2027",
                                "parent_external_id": semester_id, "title": "UE Data", "weight": None,
                                "weight_status": "missing", "provenance": {"ects": "24"}}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id=subject_id,
                       title="Machine Learning", record_kind="academic",
                       payload={"record_type": "subject", "academic_year": "2026-2027",
                                "parent_external_id": ue_id, "title": "Machine Learning",
                                "subject_code": "ML2A", "weight": None, "weight_status": "missing",
                                "grade_value": None, "grade_status": "missing"}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id="pamplemousse:assessment:final",
                       title="Final", record_kind="academic",
                       payload={"record_type": "assessment", "parent_external_id": subject_id,
                                "title": "Final", "weight": "2", "weight_mode": "raw",
                                "grade_value": None, "grade_status": "missing"}),
        InternalRecord(source_kind=InternalSourceKind.PAMPLEMOUSSE, external_id="pamplemousse:assessment:quiz",
                       title="Quiz aggregate", record_kind="academic",
                       payload={"record_type": "assessment", "parent_external_id": subject_id,
                                "title": "Quiz aggregate", "weight": "0.5", "weight_mode": "fraction"}),
    ]

    class InternalConnector:
        source_kind = InternalSourceKind.PAMPLEMOUSSE
        def __init__(self):
            self.last_stats = {"pages_visited": 2, "enrolled_subjects": 1, "available_grades": 2,
                               "subject_coefficients": 0, "missing_or_ambiguous_count": 1}
        def list_changes(self, scope, cursor):
            if cursor is None:
                return InternalPage(items=tuple(records_in[:2]), next_cursor="2", complete=False)
            return InternalPage(items=tuple(records_in[2:]), next_cursor=None, complete=True)
        def fetch_blob(self, record):
            raise AssertionError("Academic records must not trigger blob downloads")

    bridge = CodexConnectorBridge(InternalConnector(), page_size=10)
    with Runtime(settings, Components(adapters={source: Auth()}, connectors={source: bridge})) as runtime:
        runtime.consent.record(source, "read", True)
        result = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        assert result["status"] == "succeeded"
        metrics = result["report"][source.value]["metrics"]
        assert metrics["enrolled_subjects"] == 1  # Connector-wide totals are not doubled on page 2.
        assert metrics["available_grades"] == 2
        with runtime.engine.connect() as db:
            by_key = {row["external_id"]: row for row in db.execute(select(records)).mappings()}
            assert by_key[semester_id]["parent_id"] == by_key[year_id]["id"]
            assert by_key[ue_id]["parent_id"] == by_key[semester_id]["id"]
            assert by_key[subject_id]["parent_id"] == by_key[ue_id]["id"]
            ue_coefficient = by_key[ue_id + ":coef"]["payload"]
            assert ue_coefficient["weight"] is None and ue_coefficient["record_status"] == "missing"
            subject_facts = [row["payload"] for key, row in by_key.items() if key.startswith(subject_id + ":")]
            assert any(item["kind"] == "coefficient" and item["weight"] is None and
                       item["record_status"] == "missing" for item in subject_facts)
            assert any(item["kind"] == "grade" and item["value"] is None and
                       item["record_status"] == "missing" for item in subject_facts)
            final_coef = by_key["pamplemousse:assessment:final:coef"]["payload"]
            assert final_coef["kind"] == "coefficient" and final_coef["weight"] == "2"
            assert final_coef["level"] == "assessment" and final_coef["mode"] == "raw"
            assert by_key["pamplemousse:assessment:final:grade"]["payload"]["record_status"] == "missing"
            year_coefficient = by_key["pamplemousse:aggregate:year:coef"]["payload"]
            assert year_coefficient["level"] == "year" and year_coefficient["weight"] is None
            assert year_coefficient["record_status"] == "missing"
            semester_coefficient = by_key["pamplemousse:aggregate:semester:coef"]["payload"]
            assert semester_coefficient["level"] == "semester" and semester_coefficient["weight"] == "4"
            fraction = by_key["pamplemousse:assessment:quiz:weight"]["payload"]
            assert fraction["kind"] == "assessment_weight" and fraction["weight"] == "0.5"
            assert fraction["mode"] == "fraction"
        report = audit_database(settings.local_data_dir / "ensora.sqlite3", result["id"])
        assert report["run"]["sources"][source.value]["enrolled_subjects"] == 1
        assert report["cumulative"][source.value]["academic_entities"]["academic_year"] == 1
        assert report["cumulative"][source.value]["academic_entities"]["semester"] == 1
        assert report["cumulative"][source.value]["academic_entities"]["teaching_unit"] == 1
        assert report["cumulative"][source.value]["academic_entities"]["subject"] == 1
        assert report["cumulative"][source.value]["available_grades"] == 2


def test_audit_leaves_adapter_unreported_metrics_unknown():
    import json
    from backend.app.core.audit import METRICS, run_summary

    row = {"report": json.dumps({"ensora_drive": {"metrics": {"documents_discovered": 25}}}),
           "scope": json.dumps({"source": "ensora_drive"}), "id": "fixture", "status": "succeeded",
           "started_at": "2026-11-27T00:00:00Z", "finished_at": "2026-11-27T00:01:00Z"}
    summary = run_summary(row)["sources"]["ensora_drive"]
    assert summary["documents_discovered"] == 25
    assert summary["resources_discovered"] is None
    assert summary["folders_traversed"] is None
    assert set(METRICS).issuperset(("courses_traversed", "sections_traversed", "resources_discovered"))


def test_pamplemousse_raw_sibling_coefficients_preserved_and_normalized_for_grade_tracker(settings):
    from decimal import Decimal

    from backend.app.contracts.sources import AssessmentWeight, SubjectCoefficient
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
    from backend.app.modules.ingestion.contracts import ChangePage as InternalPage
    from backend.app.modules.ingestion.contracts import SourceKind as InternalSourceKind
    from backend.app.modules.ingestion.contracts import SourceRecord as InternalRecord

    subject_id = "pamplemousse:subject:synthetic-weighted"
    raw = [
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id=subject_id,
            title="Synthetic weighted subject",
            record_kind="academic",
            payload={"record_type": "subject", "title": "Synthetic weighted subject"},
        ),
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id="pamplemousse:assessment:midterm",
            title="Midterm",
            record_kind="academic",
            payload={"record_type": "assessment", "parent_external_id": subject_id,
                     "title": "Midterm", "weight": "1", "weight_mode": "raw",
                     "weight_status": "confirmed"},
            source_url="https://example.invalid/notes",
        ),
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id="pamplemousse:assessment:final",
            title="Final",
            record_kind="academic",
            payload={"record_type": "assessment", "parent_external_id": subject_id,
                     "title": "Final", "weight": "2", "weight_mode": "raw",
                     "weight_status": "confirmed"},
            source_url="https://example.invalid/notes",
        ),
    ]

    class CompletePamplemousse:
        source_kind = InternalSourceKind.PAMPLEMOUSSE
        last_scope = None

        def list_changes(self, scope, cursor):
            self.last_scope = scope
            return InternalPage(items=tuple(raw), next_cursor=None, complete=True)

        def fetch_blob(self, record):
            raise AssertionError("Academic records must not trigger blob downloads")

    connector = CompletePamplemousse()
    bridge = CodexConnectorBridge(connector)
    result = bridge.list_changes(
        SyncScope(source=SourceKind.PAMPLEMOUSSE, academic_year="2026-2027"), None
    )
    assert connector.last_scope["academic_year"] == "2026-2027"
    coefficients = {item.provenance.external_id: item for item in result.items
                    if isinstance(item, SubjectCoefficient) and item.level == "assessment"}
    relative = {item.parent_key: item for item in result.items if isinstance(item, AssessmentWeight)}
    assert coefficients["pamplemousse:assessment:midterm:coef"].weight == Decimal("1")
    assert coefficients["pamplemousse:assessment:final:coef"].weight == Decimal("2")
    assert all(item.mode == "raw" for item in coefficients.values())
    assert relative["pamplemousse:assessment:midterm"].weight == Decimal(1) / Decimal(3)
    assert relative["pamplemousse:assessment:final"].weight == Decimal(2) / Decimal(3)
    assert all(item.mode == "fraction" and item.weight_basis == "normalized_sibling_coefficients"
               for item in relative.values())

    # Incomplete source pages may not imply a denominator from a partial sibling set.
    class PartialPamplemousse(CompletePamplemousse):
        def list_changes(self, scope, cursor):
            return InternalPage(items=tuple(raw[:2]), next_cursor="next", complete=False)

    partial = CodexConnectorBridge(PartialPamplemousse()).list_changes(
        SyncScope(source=SourceKind.PAMPLEMOUSSE), None
    )
    assert not any(isinstance(item, AssessmentWeight) for item in partial.items)

    # A final continuation page is complete only for its slice; it cannot establish
    # the denominator for siblings that may have appeared on earlier pages.
    continued = CodexConnectorBridge(CompletePamplemousse()).list_changes(
        SyncScope(source=SourceKind.PAMPLEMOUSSE), "2"
    )
    assert not any(isinstance(item, AssessmentWeight) for item in continued.items)

    class WarningPamplemousse(CompletePamplemousse):
        def list_changes(self, scope, cursor):
            return InternalPage(items=tuple(raw), next_cursor=None, complete=True,
                                warnings=("synthetic_partial_acquisition",))

    warned = CodexConnectorBridge(WarningPamplemousse()).list_changes(
        SyncScope(source=SourceKind.PAMPLEMOUSSE), None
    )
    assert not any(isinstance(item, AssessmentWeight) for item in warned.items)


def test_pamplemousse_scoped_year_keeps_five_acquired_grades_and_zero_coefficients(settings):
    """Every academic child fact retains its verified year through bridge, filter and SQLite."""
    from backend.app.modules.ingestion.contracts import (
        ChangePage as InternalPage,
        SourceKind as InternalSourceKind,
        SourceRecord as InternalRecord,
    )
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge
    from backend.app.core.database import connections, records as academic_rows
    from sqlalchemy import update

    year = "2026-2027"
    year_id = "pamplemousse:year:2026-2027"
    semester_id = "pamplemousse:semester:s1"
    ue_id = "pamplemousse:ue:ue1"
    source_items = [
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id=year_id, title=year, record_kind="academic",
            payload={"record_type": "academic_year", "title": year, "academic_year": year},
            provenance={"academic_year": year},
        ),
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id=semester_id, title="S1", record_kind="academic",
            payload={"record_type": "semester", "title": "S1", "academic_year": year,
                     "parent_external_id": year_id},
            provenance={"academic_year": year},
        ),
        InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id=ue_id, title="UE 1", record_kind="academic",
            payload={"record_type": "teaching_unit", "title": "UE 1", "academic_year": year,
                     "parent_external_id": semester_id, "weight": "4", "weight_mode": "raw",
                     "weight_status": "confirmed", "provenance": {"ects": "12"}},
            provenance={"academic_year": year},
        ),
    ]
    for i in range(5):
        source_items.append(InternalRecord(
            source_kind=InternalSourceKind.PAMPLEMOUSSE,
            external_id=f"pamplemousse:subject:s{i}", title=f"Subject {i}", record_kind="academic",
            payload={"record_type": "subject", "title": f"Subject {i}", "academic_year": year,
                     "parent_external_id": ue_id, "subject_code": f"S{i}",
                     "weight": str(i), "weight_mode": "raw", "weight_status": "confirmed",
                     "grade_value": str(10 + i), "grade_status": "confirmed",
                     "provenance": {"ects": "3"}},
            provenance={"academic_year": year},
        ))

    class Connector:
        source_kind = InternalSourceKind.PAMPLEMOUSSE
        last_stats = {"academic_year": year}

        def list_changes(self, scope, cursor):
            assert scope["academic_year"] == year
            assert cursor is None
            return InternalPage(items=tuple(source_items), next_cursor=None, complete=True)

        def fetch_blob(self, item):
            raise AssertionError("Academic records do not download blobs")

    class Connected:
        def validate(self):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED)
        def authorize(self, inputs):
            return self.validate()
        def disconnect(self):
            return None

    with Runtime(settings, Components(
        adapters={SourceKind.PAMPLEMOUSSE: Connected()},
        connectors={SourceKind.PAMPLEMOUSSE: CodexConnectorBridge(Connector())},
    )) as runtime:
        runtime.consent.record(SourceKind.PAMPLEMOUSSE, "read", True)
        with runtime.engine.begin() as db:
            db.execute(update(connections).where(
                connections.c.id == runtime.connection_ids[SourceKind.PAMPLEMOUSSE],
            ).values(cursor="sync:existing-pamplemousse-checkpoint"))
        job = await_job(runtime, runtime.sync.submit(SyncScope(
            source=SourceKind.PAMPLEMOUSSE, academic_year=year, full=True,
        )))
        assert job["status"] == "succeeded", job["report"]
        report = job["report"]["pamplemousse"]
        assert report["errors"] == 0
        assert report["metrics"].get("academic_year_unknown", 0) == 0
        with runtime.engine.connect() as db:
            rows = db.execute(select(academic_rows.c.kind, academic_rows.c.payload)).all()
            checkpoint = db.scalar(select(connections.c.cursor).where(
                connections.c.id == runtime.connection_ids[SourceKind.PAMPLEMOUSSE],
            ))
        assert checkpoint == "sync:existing-pamplemousse-checkpoint"
        by_kind = {}
        for kind, payload in rows:
            by_kind.setdefault(kind, []).append(payload)
        assert len(by_kind["subject"]) == 5
        assert len(by_kind["coefficient"]) == 6  # one UE plus five subjects
        assert len(by_kind["grade"]) == 5
        assert {item["provenance"]["academic_year"] for kind_rows in by_kind.values()
                for item in kind_rows} == {year}
        assert {item["value"] for item in by_kind["grade"]} == {"10", "11", "12", "13", "14"}
        assert "0" in {item["weight"] for item in by_kind["coefficient"]}


def test_scoped_calendar_bridge_preserves_utc_window_and_global_checkpoint(settings, tmp_path):
    """A Paris academic-year window persists events with an exact bounded snapshot, not a global cursor."""
    from backend.app.contracts.sources import SourceKind as CodexSourceKind
    from backend.app.core.database import connections, records as calendar_rows
    from backend.app.modules.ingestion.config import SourcesRuntimeConfig
    from backend.app.modules.ingestion.connectors.google_calendar import GoogleCalendarConnector
    from backend.app.modules.ingestion.connectors.base import RateLimiter
    from sqlalchemy import update
    from backend.app.modules.ingestion.codex_bridge import CodexConnectorBridge

    source = CodexSourceKind.GOOGLE_CALENDAR
    config = SourcesRuntimeConfig.load(config_dir=tmp_path / "calendar-config", environ={})
    params_seen = {}

    class Credentials:
        valid = True
        expired = False

    class OAuth:
        def get_credentials(self, allow_refresh=True, interactive=False):
            return Credentials()

    class Response:
        def execute(self):
            return {
                "timeZone": "Europe/Paris",
                "items": [
                    {
                        "id": f"controlled-busy-{hour}",
                        "status": "confirmed",
                        "transparency": "opaque",
                        "summary": f"Private event title {hour}",
                        "start": {"dateTime": f"2026-12-01T{hour:02d}:00:00+01:00", "timeZone": "Europe/Paris"},
                        "end": {"dateTime": f"2026-12-01T{hour + 1:02d}:00:00+01:00", "timeZone": "Europe/Paris"},
                    }
                    for hour in (10, 12, 14)
                ] + [{
                    "id": "controlled-free",
                    "status": "confirmed",
                    "transparency": "transparent",
                    "summary": "Private free reminder",
                    "start": {"dateTime": "2026-12-02T10:00:00+01:00", "timeZone": "Europe/Paris"},
                    "end": {"dateTime": "2026-12-02T11:00:00+01:00", "timeZone": "Europe/Paris"},
                }],
                "nextSyncToken": "window-token-must-not-be-global",
            }

    class Events:
        def list(self, **kwargs):
            params_seen.update(kwargs)
            return Response()

    class Service:
        def events(self):
            return Events()

    connector = GoogleCalendarConnector(
        config, oauth_adapter=OAuth(), calendar_service_factory=lambda _: Service(),
        rate_limiter=RateLimiter(0.0),
    )
    runtime_settings = Settings.model_validate({
        "config_dir": tmp_path / "calendar-runtime-config",
        "local_data_dir": tmp_path / "calendar-data",
        "adapter_factory": None,
    })
    class Connected:
        def validate(self):
            return AuthenticationResult(status=ConnectionStatus.CONNECTED)
        def authorize(self, inputs):
            return self.validate()
        def disconnect(self):
            return None

    with Runtime(runtime_settings, Components(
        adapters={source: Connected()},
        connectors={source: CodexConnectorBridge(connector)},
    )) as runtime:
        runtime.consent.record(source, "read", True)
        with runtime.engine.begin() as db:
            db.execute(update(connections).where(
                connections.c.id == runtime.connection_ids[source],
            ).values(cursor="sync:existing-global-checkpoint"))
        scope = SyncScope.model_validate({
            "source": source,
            "window_start": "2026-09-01T00:00:00+02:00",
            "window_end": "2027-09-01T00:00:00+02:00",
        })
        job = await_job(runtime, runtime.sync.submit(scope))
        assert job["status"] == "succeeded", job["report"]
        report = job["report"]["google_calendar"]
        assert "calendar_window_unverified" not in report["error_codes"]
        assert report["metrics"]["busy_intervals_written"] == 3
        assert params_seen["timeMin"] == "2026-08-31T22:00:00Z"
        assert params_seen["timeMax"] == "2027-08-31T22:00:00Z"
        with runtime.engine.connect() as db:
            events = db.execute(select(calendar_rows.c.payload)).scalars().all()
            checkpoint = db.scalar(select(connections.c.cursor).where(
                connections.c.id == runtime.connection_ids[source],
            ))
        assert len(events) == 4
        assert sum(event["busy"] for event in events) == 3
        assert all(event["title"] in ("Busy", "Free") for event in events)
        assert any(event["starts_at"] == "2026-12-01T09:00:00Z" for event in events)
        assert sum(not event["busy"] for event in events) == 1  # Free is retained but never blocks.
        assert checkpoint == "sync:existing-global-checkpoint"


def test_sync_cli_progress_snapshot_exposes_only_aggregate_allowlisted_metrics():
    from scripts.sync import progress_snapshot

    result = progress_snapshot({
        "pamplemousse": {
            "status": "running",
            "documents": 0,
            "records": 18,
            "errors": 2,
            "warning_codes": ["record_failure"],
            "metrics": {
                "timetable_events": 214,
                "subject_coefficients": 71,
                "documents_persisted": 5,
                "private_grade_value": 19,
                "private_title": "Do not print",
            },
        },
    })
    assert result == {
        "pamplemousse": {
            "status": "running",
            "stage": None,
            "documents": 0,
            "records": 18,
            "errors": 2,
            "metrics": {
                "timetable_events": 214,
                "subject_coefficients": 71,
                "documents_persisted": 5,
            },
        },
    }


def test_failed_download_is_private_retryable_and_replay_is_idempotent(settings):
    from backend.app.core.etl import Pipeline

    class DriveResourceNotFoundError(Exception):
        pass

    class DriveUnexportableError(Exception):
        pass

    class DriveSizeLimitError(Exception):
        pass

    assert Pipeline._download_error_code(DriveResourceNotFoundError("private-id")) == "source_resource_missing"
    assert Pipeline._download_error_code(DriveUnexportableError("private-title")) == "unsupported_export"
    assert Pipeline._download_error_code(DriveSizeLimitError("private-size")) == "download_size_limit"
    source = SourceKind.MOODLE
    connector, adapter = Connector(), Auth()
    connector.fail = True
    components = Components(adapters={source: adapter}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.auth.set_status(source, ConnectionStatus.CONNECTED)
        failed = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        report = failed["report"][source.value]
        assert failed["status"] == "partial"
        assert report["error_codes"] == ["download_failure"]
        assert report["metrics"]["download_error_download_failure"] == 1
        with runtime.engine.connect() as db:
            doc = db.execute(select(documents)).mappings().one()
        assert doc["active_version_id"] is None
        assert doc["metadata_json"]["ingestion_state"] == "download_failed"
        assert doc["metadata_json"]["ingestion_error_code"] == "download_failure"
        assert "private upstream details" not in str(doc["metadata_json"])
        from backend.app.core.audit import audit_database
        failure_audit = audit_database(settings.local_data_dir / "ensora.sqlite3")
        assert failure_audit["cumulative"][source.value]["document_inventory"] == 1
        assert failure_audit["cumulative"][source.value]["documents_persisted"] == 0
        assert failure_audit["cumulative"][source.value]["pending_download_failures"] == 1
        assert failure_audit["cumulative"][source.value]["download_failure_categories"] == {"download_failure": 1}

        connector.fail = False
        retry_scope = SyncScope(source=source, retry_failed=True)
        recovered = await_job(runtime, runtime.sync.submit(retry_scope))
        assert recovered["status"] == "succeeded"
        assert recovered["report"][source.value]["metrics"]["failed_resources_retried"] == 1
        assert recovered["report"][source.value]["documents"] == 1

        again = await_job(runtime, runtime.sync.submit(retry_scope))
        assert again["status"] == "succeeded"
        assert again["report"][source.value]["metrics"]["failed_resources_retried"] == 0
        assert again["report"][source.value]["metrics"]["retry_failed_skipped"] == 1
        assert connector.fetches == 2
        with runtime.engine.connect() as db:
            doc = db.execute(select(documents)).mappings().one()
            version_count = db.scalar(select(func.count()).select_from(versions))
        assert doc["active_version_id"] is not None
        assert doc["metadata_json"]["ingestion_state"] == "persisted"
        assert doc["metadata_json"]["ingestion_error_code"] is None
        assert version_count == 1

        confirmed_active_version = doc["active_version_id"]
        with runtime.engine.begin() as db:
            db.execute(text("""UPDATE documents SET metadata_json = json_set(
                metadata_json, '$.user_override.subject_id', 'user-confirmed-subject',
                '$.subject_id', 'user-confirmed-subject') WHERE id = :id"""), {"id": doc["id"]})
        connector.revision = "two"
        connector.fail = True
        updated_failure = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        assert updated_failure["status"] == "partial"
        with runtime.engine.connect() as db:
            doc = db.execute(select(documents)).mappings().one()
        assert doc["active_version_id"] == confirmed_active_version
        assert doc["metadata_json"]["user_override"]["subject_id"] == "user-confirmed-subject"
        assert doc["metadata_json"]["subject_id"] == "user-confirmed-subject"

        connector.fail = False
        updated_success = await_job(runtime, runtime.sync.submit(retry_scope))
        assert updated_success["status"] == "succeeded"
        with runtime.engine.connect() as db:
            doc = db.execute(select(documents)).mappings().one()
            version_count = db.scalar(select(func.count()).select_from(versions))
        assert doc["metadata_json"]["subject_id"] == "user-confirmed-subject"
        assert doc["metadata_json"]["user_override"]["subject_id"] == "user-confirmed-subject"
        assert version_count == 1


def test_interrupted_job_replay_preserves_original_academic_year_scope(settings):
    from sqlalchemy import insert
    from backend.app.core.auth import now
    from backend.app.core.database import LOCAL_USER

    source = SourceKind.MOODLE
    connector, adapter = Connector(), Auth()
    connector.academic_year = "2026-2027"
    components = Components(adapters={source: adapter}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        with runtime.engine.begin() as db:
            db.execute(insert(runs).values(
                id="synthetic-crashed-job", user_id=LOCAL_USER, status="running",
                scope={"source": source.value, "academic_year": "2026-2027", "full": True},
                report={}, started_at=now(),
            ))
    # A fresh Runtime acquires the flock and marks an abandoned running row interrupted.
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.auth.set_status(source, ConnectionStatus.CONNECTED)
        with runtime.engine.connect() as db:
            status = db.scalar(select(runs.c.status).where(runs.c.id == "synthetic-crashed-job"))
        assert status == "interrupted"
        replay = runtime.sync.retry("synthetic-crashed-job", source)
        completed = await_job(runtime, replay)
        assert completed["status"] == "succeeded"
        assert connector.requested_scope.academic_year == "2026-2027"
        assert connector.requested_scope.full is True


def test_auth_adapter_failure_categories_are_actionable_and_redacted():
    from backend.app.core.auth import AuthenticationManager
    from backend.app.contracts.sync import SourceSyncReport
    from backend.app.core.sync import SyncEngine

    class TransientSourceError(Exception):
        pass

    class PermissionDeniedError(Exception):
        pass

    transient = AuthenticationManager._adapter_exception_result(TransientSourceError("secret URL and token"))
    denied = AuthenticationManager._adapter_exception_result(PermissionDeniedError("private course title"))
    unknown = AuthenticationManager._adapter_exception_result(RuntimeError("private exception content"))
    assert transient.status == ConnectionStatus.UNAVAILABLE
    assert transient.reason == "transient_failure"
    assert denied.status == ConnectionStatus.ACCESS_DENIED
    assert denied.reason == "service_access_denied"
    assert unknown.status == ConnectionStatus.ERROR
    assert unknown.reason == "adapter_error"
    assert "secret" not in str(transient.model_dump())
    assert "private" not in str(unknown.model_dump())
    report = SourceSyncReport(source=SourceKind.MOODLE)
    SyncEngine._report_auth(report, unknown)
    assert report.errors == 1
    assert report.error_codes == ["source_validation_error"]
    assert report.next_action == "retry_validation"


def test_interrupted_page_checkpoint_resumes_after_restart_and_is_not_public(settings):
    source = SourceKind.MOODLE

    class CheckpointConnector:
        def __init__(self):
            self.fail_on_page_two_once = True
            self.received = []

        def list_changes(self, scope, cursor):
            self.received.append(cursor)
            if cursor is None:
                return ChangePage(
                    items=[Document(provenance=Provenance(external_id="checkpoint-doc-1", remote_revision="1"),
                                    title="One", mime_type="text/plain")],
                    complete=False, next_cursor="page-two-token",
                )
            if cursor == "page-two-token" and self.fail_on_page_two_once:
                self.fail_on_page_two_once = False
                raise RuntimeError("private provider response")
            assert cursor == "page-two-token"
            return ChangePage(
                items=[Document(provenance=Provenance(external_id="checkpoint-doc-2", remote_revision="1"),
                                title="Two", mime_type="text/plain")],
                complete=True,
            )

        def fetch_blob(self, record):
            return record.title.encode("utf-8")

    connector, adapter = CheckpointConnector(), Auth()
    components = Components(adapters={source: adapter}, connectors={source: connector})
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.auth.set_status(source, ConnectionStatus.CONNECTED)
        failed = await_job(runtime, runtime.sync.submit(SyncScope(source=source)))
        assert failed["status"] == "partial"
        assert failed["report"][source.value]["status"] == "failed"
        assert connector.received == [None, "page-two-token"]
        with runtime.engine.connect() as db:
            stored = db.scalar(select(runs.c.report).where(runs.c.id == failed["id"]))
        assert stored["_checkpoint_cursors"][source.value] == "page-two-token"

    from scripts.sync import progress_snapshot, public_report
    assert "_checkpoint_cursors" not in public_report(stored)
    assert "_checkpoint_cursors" not in progress_snapshot(stored)

    # A new Runtime models process restart: retry uses the last fully processed page cursor.
    with Runtime(settings, components) as runtime:
        runtime.consent.record(source, "read", True)
        runtime.auth.set_status(source, ConnectionStatus.CONNECTED)
        retried = runtime.sync.retry(failed["id"], source)
        completed = await_job(runtime, retried)
        assert completed["status"] == "succeeded"
        assert connector.received[-1] == "page-two-token"
        with runtime.engine.connect() as db:
            count_docs = db.scalar(select(func.count()).select_from(documents))
            stored = db.scalar(select(runs.c.report).where(runs.c.id == retried))
        assert count_docs == 2
        assert "_checkpoint_cursors" not in stored
