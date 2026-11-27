"""Bounded sync jobs, cooperative cancellation and durable checkpoints."""
from concurrent.futures import ThreadPoolExecutor
import logging
from threading import Event, Lock

from sqlalchemy import insert, select, update

from ..contracts.authentication import (
    AuthenticationResult, ConnectionStatus, ProfileBusy, ReauthenticationRequired, ServiceAccessDenied,
)
from ..contracts.sources import CalendarEvent, ChangePage, Document, RetryableSourceError, SourceKind, SyncScope
from ..contracts.sync import SourceSyncReport
from .auth import now
from .calendar import interval_time
from .database import LOCAL_USER, connections, new_id, runs
from .etl import count

logger = logging.getLogger(__name__)


class ResumeUnavailable(ValueError):
    """The selected job cannot be resumed yet."""


class SyncEngine:
    def __init__(self, engine, pipeline, auth, connectors, concurrency, validate_local_path=None):
        self.engine = engine
        self.pipeline = pipeline
        self.auth = auth
        self.connectors = connectors
        self.validate_local_path = validate_local_path
        self.dispatch = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync-job")
        self.workers = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="sync-source")
        self.active_lock = Lock()
        self.active = {}

    def recover(self):
        with self.engine.begin() as db:
            db.execute(update(runs).where(runs.c.status.in_(("queued", "running"))).values(
                status="interrupted", finished_at=now(),
            ))

    def submit(self, scope, key=None, automatic=False):
        if scope.source == SourceKind.LOCAL and scope.folder_id and self.validate_local_path:
            path = self.validate_local_path(scope.folder_id)
            scope = scope.model_copy(update={"folder_id": str(path)})
        payload = scope.model_dump(mode="json")
        with self.active_lock, self.engine.begin() as db:
            if key:
                existing = db.execute(select(runs.c.id, runs.c.scope).where(
                    runs.c.user_id == LOCAL_USER, runs.c.idempotency_key == key,
                )).one_or_none()
                if existing:
                    if SyncScope.model_validate(existing.scope).model_dump(mode="json") != payload:
                        raise ValueError("Idempotency key already used for another scope")
                    return existing.id
            if automatic and self.active:
                return None
            job = new_id()
            db.execute(insert(runs).values(id=job, user_id=LOCAL_USER, status="queued", scope=payload,
                                          report={}, started_at=now(), idempotency_key=key))
            cancelled = self.active[job] = Event()
        self.dispatch.submit(self._run, job, scope, automatic, cancelled)
        return job

    def resume(self, job, source=None):
        """Submit one consented source again using the interrupted job's scope."""
        with self.engine.connect() as db:
            prior = db.execute(select(runs.c.status, runs.c.scope, runs.c.report).where(
                runs.c.id == job, runs.c.user_id == LOCAL_USER,
            )).one_or_none()
        if prior is None:
            raise LookupError("Unknown sync job")
        eligible = [SourceKind(name) for name, report in prior.report.items()
                    if report.get("next_action") == "reauthorize"]
        if source is None and len(eligible) == 1:
            source = eligible[0]
        if prior.status != "partial" or source not in eligible:
            raise ResumeUnavailable("Select a source awaiting reauthentication in a completed sync job")
        with self.auth.locks[source]:
            if not self.auth.consent.allowed(source, "read"):
                raise ResumeUnavailable("Source read consent is required")
            if self.auth.status(source) != ConnectionStatus.CONNECTED:
                raise ResumeUnavailable("Reauthorize the source before resuming")
            scope = SyncScope.model_validate(prior.scope | {"source": source})
        return self.submit(scope, key=f"reauth-resume:{job}:{source.value}")

    def cancel(self, job):
        with self.active_lock:
            if job in self.active:
                self.active[job].set()

    def close(self):
        with self.active_lock:
            for cancellation in self.active.values():
                cancellation.set()
        self.dispatch.shutdown()
        self.workers.shutdown()

    def _update(self, job, **values):
        with self.engine.begin() as db:
            db.execute(update(runs).where(runs.c.id == job).values(**values))

    def _run(self, job, scope, automatic, cancelled):
        reports = {}
        lock = Lock()

        def progress(report):
            with lock:
                reports[report.source] = report.model_dump(mode="json")
                self._update(job, report=dict(reports))

        try:
            self._update(job, status="running")
            requested = [scope.source] if scope.source else [
                source for source in self.connectors if source != SourceKind.LOCAL and self.auth.consent.allowed(source, "read")
            ]
            sources = [source for source in requested
                       if not automatic or (source != SourceKind.LOCAL and
                                            self.auth.consent.allowed(source, "automatic_sync"))]
            futures = [self.workers.submit(self._source, source, scope, cancelled, progress, automatic) for source in sources]
            for future in futures:
                future.result()
            succeeded = all(report["status"] == "succeeded" for report in reports.values())
            status = "cancelled" if cancelled.is_set() else "succeeded" if succeeded else "partial"
        except Exception:
            logger.error("Sync worker failed")
            status = "failed"
        finally:
            self._update(job, status=status, finished_at=now())
            with self.active_lock:
                self.active.pop(job, None)

    def _source(self, source, scope, cancelled, progress, automatic):
        report = SourceSyncReport(source=source, status="running")
        progress(report)
        try:
            connector = self.connectors.get(source)
            if source == SourceKind.LOCAL and not scope.folder_id:
                report.status = "scope_required"
                progress(report)
                return
            with self.auth.locks[source]:
                if cancelled.is_set():
                    report.status = "cancelled"
                elif automatic and not self.auth.consent.allowed(source, "automatic_sync"):
                    report.status = "authorization_required"
                elif connector is None:
                    report.status = "unavailable"
                else:
                    validation = self.auth.validate(source)
                    if validation.status != ConnectionStatus.CONNECTED:
                        self._report_auth(report, validation)
            if report.status == "running":
                self._pages(connector, source, scope, cancelled, report, progress, automatic)
        except (ReauthenticationRequired, ServiceAccessDenied) as exc:
            self._report_auth(report, self.auth.failure_result(exc))
        except ProfileBusy:
            self._report_auth(report, AuthenticationResult(status=ConnectionStatus.UNAVAILABLE, reason="profile_busy"))
        except Exception as exc:
            logger.error("Sync source worker failed (exception_type=%s)", type(exc).__name__)
            report.status = "failed"
            report.errors += 1
            report.error_codes.append("source_failure")
        progress(report)

    @staticmethod
    def _report_auth(report, result):
        report.connection_status = result.status
        report.auth_reason = result.reason
        if result.status == ConnectionStatus.REAUTH_REQUIRED:
            report.status = "authorization_required"
            report.next_action = "reauthorize"
        elif result.status == ConnectionStatus.ACCESS_DENIED:
            report.status = "access_denied"
            report.next_action = "grant_consent" if result.reason == "consent_required" else "check_access"
        elif result.status == ConnectionStatus.NOT_CONFIGURED:
            report.status = "not_configured"
            report.next_action = "configure_source"
        else:
            report.status = "unavailable" if result.status == ConnectionStatus.UNAVAILABLE else "failed"
            report.next_action = "retry_validation"

    def _pages(self, connector, source, scope, cancelled, report, progress, automatic):
        connection_id = self.auth.connection_ids[source]
        selective = bool(scope.subject_id or scope.folder_id or scope.academic_year
                         or scope.window_start or scope.discovery_only)
        window = (
            interval_time(scope.window_start.isoformat(), None, None),
            interval_time(scope.window_end.isoformat(), None, None),
        ) if scope.window_start else None
        with self.engine.connect() as db:
            cursor = None if scope.full or selective else db.scalar(select(connections.c.cursor).where(
                connections.c.id == connection_id,
            ))
        def authorized():
            return self.auth.consent.allowed(source, "read") and (
                not automatic or self.auth.consent.allowed(source, "automatic_sync")
            )

        seen = set()
        checkpoint_blocked = False
        snapshot = None
        snapshot_ids = set()
        completed_courses = set()
        report.metrics.update(dict.fromkeys((
            "documents_discovered", "documents_downloaded", "documents_parsed", "documents_partially_parsed",
            "documents_unsupported", "documents_persisted", "documents_excluded", "parsing_failures",
            "chunks_generated", "announcements_discovered", "assignments_discovered",
            "busy_intervals_written", "intervals_updated", "intervals_cancelled",
        ), 0))
        while not cancelled.is_set() and authorized():
            if cursor in seen or len(seen) >= 10000:
                raise ValueError("Invalid connector pagination")
            seen.add(cursor)
            with self.auth.locks[source]:
                if not authorized():
                    break
                if source == SourceKind.LOCAL and self.validate_local_path:
                    self.validate_local_path(scope.folder_id)
                page = ChangePage.model_validate(self.auth.guard_acquisition(
                    source, self._retry, cancelled, connector.list_changes, scope, cursor,
                ))
            marker = page.calendar_snapshot
            window_verified = window is not None and marker is not None and (
                marker.starts_at == scope.window_start and marker.ends_at == scope.window_end
            )
            if (marker and source == SourceKind.GOOGLE_CALENDAR and not scope.discovery_only
                    and (not selective or window_verified)):
                if snapshot is not None and snapshot != page.calendar_snapshot:
                    raise ValueError("Calendar snapshot boundary changed during pagination")
                snapshot = marker
            for name, value in page.observations.items():
                count(report.metrics, name, value)
            checkpoint_blocked |= bool(page.error_codes)
            report.errors += len(page.error_codes)
            report.error_codes = sorted(set(report.error_codes + page.error_codes))
            report.warning_codes = sorted(set(report.warning_codes + page.warning_codes))
            if scope.discovery_only and page.complete and "courses_discovered" not in report.metrics:
                report.warning_codes = sorted(set(report.warning_codes + ["discovery_coverage_unknown"]))
            for record in page.items:
                if cancelled.is_set() or not authorized():
                    break
                if snapshot is not None and isinstance(record, CalendarEvent):
                    if not record.provenance.external_id.startswith(snapshot.external_id_prefix):
                        checkpoint_blocked = True
                        report.errors += 1
                        report.error_codes = sorted(set(report.error_codes + ["calendar_snapshot_scope"]))
                        continue
                    snapshot_ids.add(record.provenance.external_id)
                if scope.academic_year and record.provenance.academic_year != scope.academic_year:
                    if record.provenance.academic_year is None:
                        count(report.metrics, "academic_year_unknown")
                        if "academic_year_unknown" not in report.error_codes:
                            report.error_codes.append("academic_year_unknown")
                            report.errors += 1
                    continue
                if window and snapshot is None:
                    continue
                if window and isinstance(record, CalendarEvent) and record.status != "cancelled":
                    values = record.model_dump(mode="json")
                    tz = record.original_timezone
                    start = interval_time(values["starts_at"], values["all_day_start"], tz)
                    end = interval_time(values["ends_at"], values["all_day_end"], tz)
                    if start is None or end is None or start >= window[1] or end <= window[0]:
                        continue
                subject_key = getattr(record, "subject_id", getattr(record, "parent_key", None))
                if scope.subject_id and scope.subject_id not in (
                    subject_key, record.provenance.external_id, getattr(record, "course_id", None),
                    subject_key.removeprefix("moodle:course:") if subject_key else None,
                ):
                    continue
                if isinstance(record, Document):
                    count(report.metrics, "documents_discovered")
                    if record.resource_kind == "announcement":
                        count(report.metrics, "announcements_discovered")
                if record.kind == "assessment" and (record.format == "assignment" or record.assessment_kind == "assignment"):
                    count(report.metrics, "assignments_discovered")
                if scope.discovery_only:
                    continue
                try:
                    with self.auth.locks[source]:
                        if not authorized():
                            break
                        result = self.pipeline.ingest(connection_id, record, lambda doc: self.auth.guard_acquisition(
                            source, self._retry, cancelled, connector.fetch_blob, doc,
                        ), metrics=report.metrics, source=source)
                    setattr(report, result, getattr(report, result) + 1)
                    if isinstance(record, Document) and record.course_id:
                        completed_courses.add(record.course_id)
                        report.metrics["courses_with_processed_resources"] = len(completed_courses)
                except (ReauthenticationRequired, ServiceAccessDenied, ProfileBusy):
                    raise
                except Exception:
                    checkpoint_blocked = True
                    report.errors += 1
                    if "record_failure" not in report.error_codes:
                        report.error_codes.append("record_failure")
                progress(report)
            if cancelled.is_set() or not authorized():
                break
            if not page.complete and not page.next_cursor:
                raise ValueError("Incomplete page requires a cursor")
            if page.complete and window and snapshot is None:
                report.error_codes.append("calendar_window_unverified")
                report.errors += 1
            if page.complete and (not selective or snapshot is not None) and not checkpoint_blocked:
                changes = {} if selective else {"last_success_at": now()}
                resume = page.resume_cursor or page.next_cursor
                if not selective and (resume is not None or scope.full or snapshot is not None):
                    changes["cursor"] = resume
                with self.auth.locks[source]:
                    if cancelled.is_set() or not authorized():
                        break
                    with self.engine.begin() as db:
                        if snapshot is not None:
                            retired = self.pipeline.retire_missing_calendar_events(db, connection_id, snapshot, snapshot_ids)
                            report.records += retired
                            count(report.metrics, "intervals_cancelled", retired)
                        if changes:
                            db.execute(update(connections).where(connections.c.id == connection_id).values(**changes))
            if page.complete:
                report.status = "partial" if report.errors else "succeeded"
                return
            cursor = page.next_cursor
        report.status = "cancelled" if cancelled.is_set() else "authorization_required"

    @staticmethod
    def _retry(cancelled, operation, *args):
        for attempt in range(3):
            if cancelled.is_set():
                raise InterruptedError()
            try:
                return operation(*args)
            except RetryableSourceError:
                if attempt == 2 or cancelled.wait(0.25 * (2 ** attempt)):
                    raise
