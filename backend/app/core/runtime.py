"""Composition root shared by CLI and HTTP entry points."""
from dataclasses import dataclass, field
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
from ..contracts.users import Preferences, Profile
from .auth import AuthenticationManager, ConsentManager
from .database import LOCAL_USER, academic_overrides, connections, migrate, new_id, open_database, records, users
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
                               self.settings.sync_concurrency, self.validate_local_import_path)
        self.sync.recover()
        self.stop = Event()
        self.scheduler = None
        return self

    def validate_local_import_path(self, path: str | Path) -> Path:
        return self.settings.validate_local_import_path(path)

    def start_schedule(self):
        if self.settings.sync_on_startup:
            self.sync.submit(SyncScope(), automatic=True)
        if self.settings.sync_interval_minutes > 0 and self.scheduler is None:
            self.scheduler = Thread(target=self._schedule, name="sync-scheduler", daemon=True)
            self.scheduler.start()

    def _schedule(self):
        while not self.stop.wait(self.settings.sync_interval_minutes * 60):
            self.sync.submit(SyncScope(), automatic=True)

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
        from ..modules.ingestion.resolution.entity_resolver import normalize_text_key

        matcher = self._matcher()
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
