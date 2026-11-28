"""Composition root shared by CLI and HTTP entry points."""
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import fcntl
from importlib import import_module
import logging
import os
from pathlib import Path
import secrets
from threading import Event, Thread
from typing import Mapping

from sqlalchemy import insert, select

from ..contracts.authentication import ConnectionStatus, SourceAuthenticationAdapter
from ..contracts.sources import DocumentClassifier, DocumentParser, EntityMatcher, SourceConnector, SourceKind, SyncScope
from ..contracts.sync import DEFAULT_SYNC_INTERVALS_MINUTES, SourceScheduleStatus, SyncScheduleSettings, SyncScheduleStatus
from ..contracts.users import Preferences, Profile
from .auth import AuthenticationManager, ConsentManager
from .database import LOCAL_USER, academic_overrides, connections, documents, migrate, new_id, open_database, records, runs, users
from .etl import Pipeline
from .security import KeyringCredentialStore, LockedBrowserSession
from .settings import Settings
from .sync import SyncEngine

logger = logging.getLogger(__name__)


@dataclass
class Components:
    adapters: Mapping[SourceKind, SourceAuthenticationAdapter] = field(default_factory=dict)
    connectors: Mapping[SourceKind, SourceConnector] = field(default_factory=dict)
    parser: DocumentParser | None = None
    classifier: DocumentClassifier | None = None
    matcher: EntityMatcher | None = None


class RuntimeBusy(RuntimeError):
    """Another local backend process owns the data directory."""


class Runtime:
    def __init__(self, settings: Settings, components: Components | None = None):
        self.settings = settings
        self.components = components

    def __enter__(self):
        self.settings.prepare()
        root = self.settings.local_data_dir
        self.fd = os.open(root / ".runtime.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeBusy("The Ensora+ backend is running. Stop it before using this CLI command.") from None
            return self._initialize(root)
        except BaseException:
            if hasattr(self, "engine"):
                self.engine.dispose()
            os.close(self.fd)
            raise

    def _initialize(self, root: Path):
        self.engine = open_database(root / "ensora.sqlite3")
        migrate(self.engine)
        self.credentials = KeyringCredentialStore(self.settings.ensai_keyring_service)
        self.sessions = LockedBrowserSession(self.settings.ensai_profile_dir)
        self.api_token = secrets.token_urlsafe(32)
        token_path = root / "api-token"
        fd = os.open(token_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(self.api_token)
        with self.engine.begin() as db:
            if db.scalar(select(users.c.id).where(users.c.id == LOCAL_USER)) is None:
                profile = Profile(academic_year=self.settings.academic_year, study_year=self.settings.study_year,
                                  specialization=self.settings.specialization)
                db.execute(insert(users).values(id=LOCAL_USER, profile=profile.model_dump(),
                                               preferences=Preferences().model_dump(), onboarded=False))
            self.connection_ids = {}
            for source in SourceKind:
                existing = db.scalar(select(connections.c.id).where(connections.c.user_id == LOCAL_USER,
                                                                   connections.c.source == source))
                self.connection_ids[source] = existing or new_id()
                if not existing:
                    db.execute(insert(connections).values(id=self.connection_ids[source], user_id=LOCAL_USER,
                                                          source=source, status=ConnectionStatus.NOT_CONFIGURED))
        self.consent = ConsentManager(self.engine, self.connection_ids)
        selected = self.components or self._load_components()
        self.auth = AuthenticationManager(
            self.engine, self.connection_ids, self.consent, selected.adapters,
            shared_ensai_session=self.settings.ensai_shared_session,
            require_institutional_evidence=self.components is None and self.settings.adapter_factory ==
            "backend.app.modules.ingestion.codex_bridge:build_components",
        )
        self.pipeline = Pipeline(self.engine, root / "blobs", self.settings.max_document_bytes,
                                 selected.parser, selected.classifier, selected.matcher)
        self.sync = SyncEngine(self.engine, self.pipeline, self.auth, selected.connectors,
                               self.settings.sync_concurrency, self.validate_local_import_path,
                               self.reconcile_academic_documents)
        self.sync.recover()
        self.stop = Event()
        self.scheduler = None
        return self

    def validate_local_import_path(self, path: str | Path) -> Path:
        return self.settings.validate_local_import_path(path)

    def reconcile_academic_documents(self, catalog_source):
        """Resolve existing documents against the currently read-consented catalog."""
        source_for_connection = {identifier: source for source, identifier in self.connection_ids.items()}
        catalog_sources = frozenset(source for source in self.connection_ids
                                    if self.consent.allowed(source, "read"))
        if catalog_source not in catalog_sources:
            return {"examined": 0, "matched": 0, "uncertain": 0, "skipped": 0, "errors": 0}
        allowed = [self.connection_ids[source] for source in catalog_sources]

        def still_authorized(identifier):
            source = source_for_connection.get(identifier)
            return (source in catalog_sources
                    and all(self.consent.allowed(item, "read") for item in catalog_sources))

        return self.pipeline.reconcile_documents(allowed, still_authorized)

    def start_schedule(self):
        # Startup only starts a daemon checker. It never waits for authentication or I/O;
        # due work is submitted after the application has entered its serving state.
        if self.scheduler is None:
            self.scheduler = Thread(target=self._schedule, name="sync-scheduler", daemon=True)
            self.scheduler.start()

    def _schedule(self):
        while not self.stop.wait(30):
            try:
                self.schedule_due_sync()
            except Exception as exc:
                logger.warning("Scheduled synchronization check failed (%s)", type(exc).__name__)

    @staticmethod
    def _parse_schedule_time(value):
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except (TypeError, ValueError):
            return None

    def _schedule_snapshot(self, current=None):
        current = current or datetime.now(UTC)
        with self.engine.connect() as db:
            raw = db.scalar(select(users.c.preferences).where(users.c.id == LOCAL_USER)) or {}
            preferences = Preferences.model_validate(raw)
            configured = preferences.sync_schedule
            rows = db.execute(select(connections.c.source, connections.c.last_success_at, connections.c.status).where(
                connections.c.user_id == LOCAL_USER)).all()
            last_success = {SourceKind(source): self._parse_schedule_time(value) for source, value, _status in rows}
            connection_status = {SourceKind(source): ConnectionStatus(status) for source, _value, status in rows}
            source_rows = db.execute(select(connections.c.source, connections.c.id).where(
                connections.c.user_id == LOCAL_USER)).all()
            has_dataset = {}
            for source_name, connection_id in source_rows:
                source = SourceKind(source_name)
                has_dataset[source] = bool(
                    db.scalar(select(documents.c.id).where(documents.c.connection_id == connection_id).limit(1))
                    or db.scalar(select(records.c.id).where(records.c.connection_id == connection_id).limit(1))
                )
            recent_runs = db.execute(select(runs.c.status, runs.c.report, runs.c.scope,
                                           runs.c.finished_at, runs.c.started_at)
                                     .where(runs.c.user_id == LOCAL_USER)
                                     .order_by(runs.c.started_at.desc()).limit(500)).all()

        schedule_tasks = [
            (SourceKind.ENSORA_DRIVE, None, SourceKind.ENSORA_DRIVE.value),
            (SourceKind.MOODLE, None, SourceKind.MOODLE.value),
            (SourceKind.PAMPLEMOUSSE, "timetable", "pamplemousse.timetable"),
            (SourceKind.PAMPLEMOUSSE, "grades", "pamplemousse.grades"),
            (SourceKind.PAMPLEMOUSSE, "catalog", "pamplemousse.catalog"),
            (SourceKind.GOOGLE_CALENDAR, None, SourceKind.GOOGLE_CALENDAR.value),
        ]
        schedule_tasks.extend((source, None, "") for source in (SourceKind.LOCAL, SourceKind.ACADEMIC_IMPORT))

        rows_out = []
        for source, scope_key, interval_key in schedule_tasks:
            default_minutes = DEFAULT_SYNC_INTERVALS_MINUTES.get(interval_key) if interval_key else None
            interval = None
            if interval_key:
                interval = configured.intervals_minutes.get(
                    interval_key, configured.intervals_minutes.get(source.value, default_minutes))
            manual_only = bool(interval_key and interval is None)
            source_toggle = source in configured.enabled_sources
            enabled = bool(interval is not None and configured.enabled and source_toggle and source in self.sync.connectors
                           and self.consent.allowed(source, "read")
                           and self.consent.allowed(source, "automatic_sync"))
            task_history = []
            scoped_success = None
            for job_status, report, saved_scope, finished_at, started_at in recent_runs:
                if not isinstance(report, dict) or not isinstance(saved_scope, dict):
                    continue
                detail = report.get(source.value)
                if not isinstance(detail, dict):
                    continue
                run_scope_key = saved_scope.get("pamplemousse_scope")
                if source == SourceKind.PAMPLEMOUSSE and run_scope_key not in (None, scope_key):
                    continue
                attempted = self._parse_schedule_time(finished_at or started_at)
                task_history.append((job_status, detail, attempted))
                if (scope_key and run_scope_key == scope_key and detail.get("status") == "succeeded"
                        and attempted and (scoped_success is None or attempted > scoped_success)):
                    scoped_success = attempted
            success = max(filter(None, (last_success.get(source), scoped_success)), default=None)
            next_due = success + timedelta(minutes=interval) if success and interval is not None else current
            if success is None and has_dataset.get(source, False):
                # Legacy local data with no recorded success time must never trigger
                # an unexpected full sync. A manual successful refresh establishes freshness.
                next_due = None
            failures = 0
            backoff_until = None
            recent_failure_code = None
            progress = None
            latest_failure_at = None
            for overall, detail, attempted in task_history:
                state = detail.get("status")
                if state in {"queued", "running"}:
                    if progress is None:
                        progress = {key: detail.get(key) for key in (
                            "status", "stage", "page_number", "items_remaining_in_page", "metrics",
                        ) if detail.get(key) is not None}
                    continue
                if state == "succeeded" or overall == "succeeded":
                    break
                if state in {"partial", "failed", "cancelled", "interrupted", "authorization_required",
                             "unavailable", "access_denied", "not_configured", "scope_required"}:
                    failures += 1
                    if recent_failure_code is None:
                        codes = detail.get("error_codes") or []
                        recent_failure_code = codes[0] if codes else detail.get("auth_reason")
                    if latest_failure_at is None:
                        latest_failure_at = attempted
            if latest_failure_at is not None and failures:
                delay = min(360, 5 * (2 ** min(failures - 1, 6)))
                backoff_until = latest_failure_at + timedelta(minutes=delay)
            if success is None and has_dataset.get(source, False):
                recent_failure_code = recent_failure_code or "freshness_unknown_existing_data"
            next_scheduled = max(next_due, backoff_until) if next_due and backoff_until else next_due or backoff_until
            rows_out.append(SourceScheduleStatus(
                source=source, scope_key=scope_key, enabled=enabled, manual_only=manual_only,
                interval_minutes=interval,
                connection_status=connection_status.get(source),
                last_success_at=success, next_scheduled_at=next_scheduled if enabled else None,
                backoff_until=backoff_until, progress=progress, recent_failure_code=recent_failure_code,
            ))
        with self.sync.active_lock:
            active = len(self.sync.active)
        return SyncScheduleStatus(enabled=configured.enabled, sources=rows_out, active_jobs=active)

    def schedule_status(self, current=None):
        return self._schedule_snapshot(current)

    def update_schedule(self, schedule: SyncScheduleSettings):
        with self.engine.begin() as db:
            raw = db.scalar(select(users.c.preferences).where(users.c.id == LOCAL_USER)) or {}
            preferences = Preferences.model_validate(raw).model_copy(update={"sync_schedule": schedule})
            db.execute(users.update().where(users.c.id == LOCAL_USER).values(
                preferences=preferences.model_dump(mode="json")))
        return self._schedule_snapshot()

    def schedule_due_sync(self, current=None):
        """Submit at most one overdue, consented source; the sync lock serializes writers."""
        current = current or datetime.now(UTC)
        snapshot = self._schedule_snapshot(current)
        if not snapshot.enabled or snapshot.active_jobs:
            return None
        ordered = sorted(snapshot.sources, key=lambda row: (
            row.interval_minutes if row.interval_minutes is not None else 10**9,
            row.source.value,
        ))
        for row in ordered:
            if not row.enabled or row.next_scheduled_at is None or row.next_scheduled_at > current:
                continue
            scope = SyncScope(source=row.source, pamplemousse_scope=row.scope_key if row.source == SourceKind.PAMPLEMOUSSE else None)
            if row.source in {SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE} and self.settings.academic_year:
                scope = scope.model_copy(update={"academic_year": self.settings.academic_year})
            return self.sync.submit(scope, automatic=True)
        return None

    def _load_components(self):
        if self.settings.adapter_factory:
            try:
                module, name = self.settings.adapter_factory.split(":", 1)
                factory = getattr(import_module(module), name)
                components = factory(self.settings, self.credentials, self.sessions)
                if isinstance(components, Components):
                    if module == "backend.app.modules.ingestion.codex_bridge" and name == "build_components":
                        components.matcher = _PersistedCatalogMatcher(self)
                    return components
            except Exception as exc:
                logger.warning("Source adapters could not be loaded (%s)", type(exc).__name__)
        return Components()

    def __exit__(self, *exc):
        try:
            self.stop.set()
            if self.scheduler:
                self.scheduler.join()
            self.sync.close()
            self.engine.dispose()
        finally:
            (self.settings.local_data_dir / "api-token").unlink(missing_ok=True)
            os.close(self.fd)


class _PersistedCatalogMatcher:
    """Resolve against current read-consented subjects without retaining revoked catalog rows."""

    def __init__(self, runtime):
        self.runtime = runtime

    def _matcher(self):
        from ..modules.ingestion.codex_bridge import CodexEntityMatcherBridge
        from ..modules.ingestion.resolution.entity_resolver import AcademicEntityResolver, normalize_text_key

        runtime = self.runtime
        settings = runtime.settings
        resolver = AcademicEntityResolver(current_academic_year=settings.academic_year,
                                          student_cohort=settings.study_year,
                                          student_specialization=settings.specialization)
        matcher = CodexEntityMatcherBridge(resolver)
        matcher.load_from_sqlite_engine(runtime.engine, consent_manager=runtime.consent,
                                        connection_ids=runtime.connection_ids)
        manual = {}
        allowed = self._authorized_connections()
        with runtime.engine.connect() as db:
            overrides = db.execute(select(records.c.external_id, academic_overrides.c.fields).join(
                academic_overrides, academic_overrides.c.record_id == records.c.id,
            ).where(records.c.kind == "subject", records.c.connection_id.in_(allowed)))
            for external_id, fields in overrides:
                for alias in fields.get("aliases", []):
                    manual.setdefault(normalize_text_key(alias), set()).add(external_id)
        for alias, matches in manual.items():
            if len(matches) == 1:
                resolver.manual_store.confirm_alias(alias, next(iter(matches)))
        return matcher

    def _authorized_connections(self):
        runtime = self.runtime
        return [identifier for source, identifier in runtime.connection_ids.items()
                if runtime.consent.allowed(source, "read")]

    def match(self, document, classification):
        return self._match_with(self._matcher(), document, classification)

    def match_many(self, documents):
        """Resolve a batch against one consent-scoped catalog snapshot."""
        matcher = self._matcher()
        return [self._match_with(matcher, document, classification)
                for document, classification in documents]

    def _match_with(self, matcher, document, classification):
        from ..modules.ingestion.resolution.entity_resolver import normalize_text_key

        store = matcher.resolver.manual_store
        candidates = {store.confirmed_aliases.get(normalize_text_key(value))
                      for value in (document.course_id, document.course_title) if value}
        candidates.discard(None)
        if len(candidates) == 1 and document.provenance.external_id not in store.subject_overrides:
            store.set_subject_override(document.provenance.external_id, next(iter(candidates)))
        match = matcher.match(document, classification)
        if match.confirmed and match.subject_id:
            parent = records.alias("teaching_unit_parent")
            with self.runtime.engine.connect() as db:
                units = set(db.scalars(select(parent.c.external_id).select_from(
                    records.join(parent, parent.c.id == records.c.parent_id)
                ).where(records.c.external_id == match.subject_id, records.c.kind == "subject",
                        parent.c.kind == "teaching_unit", records.c.connection_id.in_(self._authorized_connections()))))
            if len(units) == 1:
                match = match.model_copy(update={"teaching_unit_id": units.pop()})
        return match

    def exam_correction_candidates(self):
        runtime = self.runtime
        return self._matcher().find_exam_correction_pairs_from_engine(
            runtime.engine, consent_manager=runtime.consent, connection_ids=runtime.connection_ids,
        )
