"""Bridge P1 source records -> agenda (integration of feat/p1-data-engineering).

P1 stores normalized ``CalendarEvent`` records (Pamplemousse timetable, Google Calendar,
academic imports) in its own database. This module copies them into the planner agenda
through ``AgendaService.import_events``, which is idempotent (keyed by source + external_id),
keeps imported events read-only and never deletes on an incomplete listing.

Mapping rules (no information is invented):
* cancelled events are skipped;
* ``tentative`` events and events P1 flags ``confirmation_required`` need confirmation;
* P1-only event types are folded: ``project`` -> ``deadline``, ``sport`` -> ``personal``;
* sources without a dedicated agenda source label (``local``, ``academic_import``) are
  imported as ``import``;
* Pamplemousse: the ENSAI course code found in the title (``2AECO02``) becomes the
  ``subject_id`` (same code as P1's subject records), and untyped timetable slots are
  courses (``other`` -> ``class``): the timetable only lists teaching sessions.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import AppError
from app.core.identity import get_current_user_id
from app.modules.planner.models import Event
from app.modules.planner.schemas import EventImportItem, ImportResult
from app.modules.planner.service import AgendaService

router = APIRouter()

PLANNER_EVENT_TYPES = {"class", "td", "tp", "exam", "presentation", "deadline", "personal", "other"}
TYPE_FOLD = {"project": "deadline", "sport": "personal"}
# ENSAI course codes: cohort (1A-3A) + domain letters + number, e.g. 2AECO02, 1ASTA05.
COURSE_CODE = re.compile(r"(?<![A-Z0-9])([1-3]A[A-Z]{2,5}\d{2})(?![A-Z0-9])")
TIMETABLE_SOURCES = {"pamplemousse"}
DEFAULT_SOURCE_TZ = ZoneInfo("Europe/Paris")

SOURCE_LABEL = {
    "pamplemousse": "pamplemousse",
    "google_calendar": "google_calendar",
    "moodle": "moodle",
    "local": "import",
    "academic_import": "import",
    "ensora_drive": "import",
}


class SourcesUnavailable(AppError):
    status_code = 503
    code = "sources_unavailable"


class SourceImportResult(BaseModel):
    source: str
    agenda_source: str
    read: int
    skipped: int
    result: ImportResult


class SourcesImportOut(BaseModel):
    sources: list[SourceImportResult]


def _parse_dt(value: Any, tz_name: str | None = None) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        tz = DEFAULT_SOURCE_TZ
        if tz_name:
            try:
                tz = ZoneInfo(tz_name)
            except Exception:
                tz = DEFAULT_SOURCE_TZ
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(UTC)


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def course_code(*texts: Any) -> str | None:
    for text in texts:
        if isinstance(text, str):
            match = COURSE_CODE.search(text.upper())
            if match:
                return match.group(1)
    return None


def to_import_item(external_id: str, payload: dict[str, Any], source: str | None = None) -> EventImportItem | None:
    """Map one P1 ``CalendarEvent`` or Moodle ``assignment`` payload; ``None`` when it must not reach the agenda."""
    kind = payload.get("kind")
    tz_name = payload.get("original_timezone") if isinstance(payload.get("original_timezone"), str) else None
    if kind == "assignment":
        if payload.get("status") == "cancelled":
            return None
        due = _parse_dt(payload.get("deadline_at") or payload.get("due_at"), tz_name)
        if due is None:
            return None
        title = str(payload.get("title") or "").strip()[:200] or "Devoir Moodle"
        subject_id = payload.get("subject_id") or course_code(title, payload.get("course_title")) or payload.get("parent_key")
        return EventImportItem(
            external_id=external_id[:255],
            type="deadline",
            title=title,
            subject_id=subject_id,
            start_at=due,
            end_at=due + timedelta(minutes=1),
            busy=False,
            confirmation_required=bool(payload.get("confirmation_required")),
        )
    if kind != "event" or payload.get("status") == "cancelled":
        return None
    raw_type = str(payload.get("event_type") or "other")
    event_type = TYPE_FOLD.get(raw_type, raw_type)
    if event_type not in PLANNER_EVENT_TYPES:
        event_type = "other"
    subject_id = payload.get("subject_id")
    if source in TIMETABLE_SOURCES:
        if event_type == "other":
            event_type = "class"
        subject_id = subject_id or course_code(payload.get("title"), payload.get("group"))
    start, end = _parse_dt(payload.get("starts_at"), tz_name), _parse_dt(payload.get("ends_at"), tz_name)
    all_day = _parse_date(payload.get("all_day_start")) if start is None else None
    if start is None and all_day is None:
        return None
    all_day_end = _parse_date(payload.get("all_day_end")) if all_day is not None else None
    if all_day is not None and all_day_end is not None and (all_day_end - all_day).days > 1:
        # P3's all-day date carries one civil day. Preserve longer spans as local midnight intervals.
        start = _parse_dt(all_day.isoformat(), tz_name)
        end = _parse_dt(all_day_end.isoformat(), tz_name)
        all_day = None
    if start is not None:
        if end is None:
            if event_type == "deadline":
                end = start + timedelta(minutes=1)
            else:
                return None
        elif end <= start:
            if event_type == "deadline" and end == start:
                end = start + timedelta(minutes=1)
            else:
                return None
    raw_busy = payload.get("busy")
    busy = raw_busy if isinstance(raw_busy, bool) else None
    title = str(payload.get("title") or "").strip()[:200] or "Événement"
    try:
        return EventImportItem(
            external_id=external_id[:255],
            type=event_type,  # type: ignore[arg-type]
            title=title,
            subject_id=subject_id,
            start_at=start,
            end_at=end,
            all_day_date=all_day,
            room=payload.get("room"),
            teacher=payload.get("teacher"),
            group=payload.get("group"),
            busy=busy,
            confirmation_required=bool(payload.get("confirmation_required")) or payload.get("status") == "tentative",
            recurrence_key=payload.get("recurrence_key"),
        )
    except ValueError:
        return None


def read_source_events(engine) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """All P1 event and assignment records grouped by source, read from the P1 database."""
    from backend.app.core.database import LOCAL_USER, connections, latest_consent_granted, records

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    cols = [connections.c.source, records.c.external_id, records.c.kind, records.c.payload]
    if "title" in records.c:
        cols.append(records.c.title)
    query = (
        select(*cols)
        .join(connections, records.c.connection_id == connections.c.id)
        .where(
            or_(
                records.c.kind.in_(("event", "assignment")),
                and_(
                    connections.c.source == "moodle",
                    records.c.kind == "assessment",
                    records.c.payload["assessment_kind"].as_string() == "assignment",
                ),
            ),
            connections.c.user_id == LOCAL_USER,
            latest_consent_granted(connections.c.id, "read").is_(True),
        )
        .order_by(connections.c.source, records.c.external_id)
    )
    with engine.connect() as db:
        subject_rows = db.execute(
            select(connections.c.source, records.c.external_id, records.c.payload)
            .join(connections, records.c.connection_id == connections.c.id)
            .where(
                records.c.kind == "subject", connections.c.user_id == LOCAL_USER,
                connections.c.source.in_(("pamplemousse", "moodle")),
                latest_consent_granted(connections.c.id, "read").is_(True),
            )
        )
        official_codes: dict[str, str] = {}
        moodle_subjects: dict[str, dict[str, Any]] = {}
        for source, external_id, subject in subject_rows:
            if source == "moodle":
                moodle_subjects[external_id] = subject or {}
            else:
                code = course_code((subject or {}).get("code"), (subject or {}).get("source_code"))
                if code:
                    official_codes[external_id] = code
        for row in db.execute(query):
            source, external_id, kind, payload = row[0], row[1], row[2], dict(row[3] or {})
            if source == "moodle" and kind == "assessment":
                payload["kind"] = "assignment"
                subject = moodle_subjects.get(payload.get("parent_key"), {})
                candidates = {official_codes[alias] for alias in subject.get("aliases", []) or [] if alias in official_codes}
                if len(candidates) == 1:
                    payload["subject_id"] = next(iter(candidates))
            else:
                payload.setdefault("kind", kind)
            if len(row) > 4 and row[4] and not payload.get("title"):
                payload["title"] = row[4]
            grouped.setdefault(str(source), []).append((external_id, payload))
    return grouped


def apply_source_events(db: Session, clock: Clock, user_id: str, engine) -> list[SourceImportResult]:
    """Copy every P1 calendar event & Moodle assignment into the agenda (idempotent)."""
    agenda = AgendaService(db, clock)
    results: list[SourceImportResult] = []
    for source, rows in read_source_events(engine).items():
        label = SOURCE_LABEL.get(source, "import")
        items: list[EventImportItem] = []
        cancelled_ids: set[str] = set()
        task_changed = False
        for external_id, payload in rows:
            if payload.get("status") == "cancelled":
                cancelled_ids.add(external_id[:255])
            item = to_import_item(external_id, payload, source)
            if item is not None:
                items.append(item)
            if source == "moodle" and payload.get("kind") == "assignment":
                task_changed |= agenda.sync_moodle_task(
                    user_id, external_id[:255], item,
                    cancelled=payload.get("status") == "cancelled",
                )
        result = agenda.import_events(user_id, label, items) if items else ImportResult(
            created=0, updated=0, unchanged=0, agenda_revision=agenda.agenda_revision(user_id)
        )
        # Propagate upstream cancellations to previously imported events without deleting history.
        cancelled_updated = 0
        if cancelled_ids:
            existing = list(
                db.scalars(
                    select(Event).where(
                        Event.user_id == user_id,
                        Event.source == label,
                        Event.external_id.in_(cancelled_ids),
                        Event.status != "cancelled",
                    )
                )
            )
            for ev in existing:
                ev.status = "cancelled"
                ev.busy = False
                ev.confirmation_required = False
                ev.revision += 1
                db.flush()
                agenda._sync_assessment(user_id, ev)
                agenda._emit(
                    user_id,
                    "EventChanged",
                    ev.id,
                    ev.revision,
                    action="source_cancelled",
                    source=label,
                )
                cancelled_updated += 1
            if cancelled_updated:
                rev = agenda.bump_revision(user_id)
                result = ImportResult(
                    created=result.created,
                    updated=result.updated + cancelled_updated,
                    unchanged=result.unchanged,
                    agenda_revision=rev,
                )
        if task_changed:
            result.agenda_revision = agenda.bump_revision(user_id)
        results.append(
            SourceImportResult(source=source, agenda_source=label, read=len(rows), skipped=len(rows) - len(items), result=result)
        )
    return results


@router.post("/agenda/import-from-sources", response_model=SourcesImportOut, tags=["agenda"])
def import_from_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    user_id: Annotated[str, Depends(get_current_user_id)],
):
    """Copy every P1 calendar event into the agenda (idempotent; safe to call after each sync)."""
    sources_app = getattr(request.app.state, "sources_app", None)
    runtime = getattr(getattr(sources_app, "state", None), "runtime", None)
    if runtime is None:
        raise SourcesUnavailable("Source synchronization is not running")
    return SourcesImportOut(sources=apply_source_events(db, clock, user_id, runtime.engine))


def latest_finished_run(engine) -> str | None:
    """Id of the most recent sync run that brought data (succeeded or partial)."""
    from backend.app.core.database import runs

    query = (
        select(runs.c.id)
        .where(runs.c.status.in_(("succeeded", "partial")), runs.c.finished_at.is_not(None))
        .order_by(runs.c.finished_at.desc(), runs.c.id.desc())
        .limit(1)
    )
    with engine.connect() as db:
        return db.scalar(query)


class AgendaAutoImporter:
    """Applies the bridge after every finished sync run, manual or scheduled.

    P1 has no completion callback, so a daemon thread watches its ``sync_runs`` table
    (cheap indexed query) and imports once per newly finished run.
    """

    def __init__(self, engine, session_factory, clock: Clock, interval_s: float = 15.0) -> None:
        import threading

        self._engine = engine
        self._session_factory = session_factory
        self._clock = clock
        self._interval = interval_s
        self._stop = threading.Event()
        self._last: str | None = None
        self._thread = threading.Thread(target=self._loop, name="agenda-auto-import", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def run_once(self) -> list[SourceImportResult] | None:
        run_id = latest_finished_run(self._engine)
        if run_id is None or run_id == self._last:
            return None
        from app.core.identity import LOCAL_USER_EMAIL, ensure_user

        with self._session_factory() as db:
            try:
                user_id = ensure_user(db, LOCAL_USER_EMAIL, "Étudiant·e local·e").id
                results = apply_source_events(db, self._clock, user_id, self._engine)
                db.commit()
            except Exception:
                db.rollback()
                raise
        self._last = run_id
        return results

    def _loop(self) -> None:
        import logging

        log = logging.getLogger(__name__)
        while not self._stop.wait(self._interval):
            try:
                self.run_once()
            except Exception:  # keep watching; the manual endpoint still works
                log.exception("Automatic agenda import failed")
