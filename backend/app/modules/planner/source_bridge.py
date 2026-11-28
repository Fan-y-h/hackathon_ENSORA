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
  imported as ``import``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import AppError
from app.core.identity import get_current_user_id
from app.modules.planner.schemas import EventImportItem, ImportResult
from app.modules.planner.service import AgendaService

router = APIRouter()

PLANNER_EVENT_TYPES = {"class", "td", "tp", "exam", "presentation", "deadline", "personal", "other"}
TYPE_FOLD = {"project": "deadline", "sport": "personal"}
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


def _parse_dt(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) and value else None


def _parse_date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def to_import_item(external_id: str, payload: dict[str, Any]) -> EventImportItem | None:
    """Map one P1 ``CalendarEvent`` payload; ``None`` when it must not reach the agenda."""
    if payload.get("kind") != "event" or payload.get("status") == "cancelled":
        return None
    raw_type = str(payload.get("event_type") or "other")
    event_type = TYPE_FOLD.get(raw_type, raw_type)
    if event_type not in PLANNER_EVENT_TYPES:
        event_type = "other"
    start, end = _parse_dt(payload.get("starts_at")), _parse_dt(payload.get("ends_at"))
    all_day = _parse_date(payload.get("all_day_start")) if start is None else None
    if start is None and all_day is None:
        return None
    if start is not None and end is None:
        return None
    title = str(payload.get("title") or "").strip()[:200] or "Événement"
    return EventImportItem(
        external_id=external_id[:255],
        type=event_type,  # type: ignore[arg-type]
        title=title,
        subject_id=payload.get("subject_id"),
        start_at=start,
        end_at=end,
        all_day_date=all_day,
        room=payload.get("room"),
        teacher=payload.get("teacher"),
        group=payload.get("group"),
        confirmation_required=bool(payload.get("confirmation_required")) or payload.get("status") == "tentative",
        recurrence_key=payload.get("recurrence_key"),
    )


def read_source_events(engine) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """All P1 event records grouped by source, read from the P1 database."""
    from backend.app.core.database import connections, records

    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    query = (
        select(connections.c.source, records.c.external_id, records.c.payload)
        .join(connections, records.c.connection_id == connections.c.id)
        .where(records.c.kind == "event")
        .order_by(connections.c.source, records.c.external_id)
    )
    with engine.connect() as db:
        for source, external_id, payload in db.execute(query):
            grouped.setdefault(str(source), []).append((external_id, payload or {}))
    return grouped


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
    agenda = AgendaService(db, clock)
    results: list[SourceImportResult] = []
    for source, rows in read_source_events(runtime.engine).items():
        label = SOURCE_LABEL.get(source, "import")
        items: list[EventImportItem] = []
        for external_id, payload in rows:
            item = to_import_item(external_id, payload)
            if item is not None:
                items.append(item)
        result = agenda.import_events(user_id, label, items) if items else ImportResult(
            created=0, updated=0, unchanged=0, agenda_revision=0
        )
        results.append(
            SourceImportResult(source=source, agenda_source=label, read=len(rows), skipped=len(rows) - len(items), result=result)
        )
    return SourcesImportOut(sources=results)
