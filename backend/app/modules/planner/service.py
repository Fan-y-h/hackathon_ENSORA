"""Agenda use cases: classes, exams, deadlines, personal events, tasks and availability.

Every mutation bumps the per-user agenda revision (planner input revision) and writes an
outbox event in the same transaction, so a changed agenda marks the plan dirty.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import TypeVar
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.common import Clock, check_revision, paginate
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.events import record_event
from app.modules.account.domain import PlanningPreferences
from app.modules.account.service import get_planning_preferences
from app.modules.learning.catalog_port import get_catalog
from app.modules.planner import domain
from app.modules.planner.models import (
    AgendaState,
    Assessment,
    AssessmentConcept,
    Event,
    Task,
)
from app.modules.planner.schemas import (
    AssessmentOut,
    AssessmentPatch,
    AvailabilityOut,
    EventConfirm,
    EventCreate,
    EventImportItem,
    EventOut,
    EventPatch,
    ImportResult,
    TaskCreate,
    TaskOut,
    TaskPatch,
    check_timing,
)

M = TypeVar("M", Event, Assessment, Task)
_CLOSED_TASK = ("done", "cancelled")


class AgendaService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    # ================================================================== helpers ====
    def _get_owned(self, model: type[M], entity_id: str, user_id: str) -> M:
        obj = self.db.get(model, entity_id)
        if obj is None or obj.user_id != user_id:  # never reveal other users' rows
            raise NotFound(f"{model.__name__} not found")
        return obj

    def bump_revision(self, user_id: str) -> int:
        state = self.db.get(AgendaState, user_id)
        if state is None:
            state = AgendaState(user_id=user_id, revision=0)
            self.db.add(state)
        state.revision += 1
        return state.revision

    def agenda_revision(self, user_id: str) -> int:
        state = self.db.get(AgendaState, user_id)
        return state.revision if state else 0

    def _tz(self, user_id: str) -> ZoneInfo:
        return ZoneInfo(get_planning_preferences(self.db, user_id).timezone)

    def _emit(self, user_id: str, type_: str, entity_id: str, revision: int, **payload) -> None:
        record_event(
            self.db,
            user_id=user_id,
            type=type_,
            entity_id=entity_id,
            revision=revision,
            payload=payload,
        )

    def _assessment_for(self, event_id: str) -> Assessment | None:
        return self.db.scalar(select(Assessment).where(Assessment.event_id == event_id))

    def _event_out(self, ev: Event, assessment_id: str | None) -> EventOut:
        return EventOut(
            id=ev.id,
            type=ev.type,  # type: ignore[arg-type]
            title=ev.title,
            subject_id=ev.subject_id,
            start_at=ev.start_at,
            end_at=ev.end_at,
            all_day_date=ev.all_day_date,
            room=ev.room,
            teacher=ev.teacher,
            group=ev.group_name,
            status=ev.status,  # type: ignore[arg-type]
            busy=ev.busy,
            source=ev.source,  # type: ignore[arg-type]
            external_id=ev.external_id,
            read_only=ev.source != "manual",
            confirmation_required=ev.confirmation_required,
            recurrence_key=ev.recurrence_key,
            notes=ev.notes,
            assessment_id=assessment_id,
            revision=ev.revision,
            created_at=ev.created_at,
            updated_at=ev.updated_at,
        )

    # =================================================================== events ====
    def list_events(
        self,
        user_id: str,
        start: datetime,
        end: datetime,
        *,
        types: list[str] | None,
        include_cancelled: bool,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[EventOut], str | None]:
        if end <= start:
            raise ValidationFailed("end must be after start")
        if end - start > timedelta(days=366):
            raise ValidationFailed("range is limited to 366 days")
        tz = self._tz(user_id)
        first_day = start.astimezone(tz).date()
        last_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
        q = select(Event).where(
            Event.user_id == user_id,
            or_(
                and_(Event.start_at < end, Event.end_at > start),
                and_(Event.all_day_date >= first_day, Event.all_day_date <= last_day),
            ),
        )
        if types:
            q = q.where(Event.type.in_(types))
        if not include_cancelled:
            q = q.where(Event.status != "cancelled")
        events = list(self.db.scalars(q))
        events.sort(
            key=lambda e: (
                domain.event_interval(e.start_at, e.end_at, e.all_day_date, tz).start,
                e.id,
            )
        )
        page, nxt = paginate(events, limit, cursor)
        amap = self._assessment_map([e.id for e in page])
        return [self._event_out(e, amap.get(e.id)) for e in page], nxt

    def _assessment_map(self, event_ids: list[str]) -> dict[str, str]:
        if not event_ids:
            return {}
        rows = self.db.execute(
            select(Assessment.event_id, Assessment.id).where(Assessment.event_id.in_(event_ids))
        )
        return {eid: aid for eid, aid in rows}

    def get_event(self, user_id: str, event_id: str) -> EventOut:
        ev = self._get_owned(Event, event_id, user_id)
        a = self._assessment_for(ev.id)
        return self._event_out(ev, a.id if a else None)

    def create_event(self, user_id: str, body: EventCreate) -> EventOut:
        get_catalog(self.db).check_subject(user_id, body.subject_id)
        ev = Event(
            user_id=user_id,
            type=body.type,
            title=body.title,
            subject_id=body.subject_id,
            start_at=body.start_at,
            end_at=body.end_at,
            all_day_date=body.all_day_date,
            room=body.room,
            teacher=body.teacher,
            group_name=body.group,
            status=body.status,
            busy=domain.default_busy(body.type) if body.busy is None else body.busy,
            source="manual",
            recurrence_key=body.recurrence_key,
            notes=body.notes,
        )
        self.db.add(ev)
        self.db.flush()
        assessment = self._sync_assessment(user_id, ev)
        rev = self.bump_revision(user_id)
        self._emit(user_id, "EventChanged", ev.id, ev.revision, action="created", agenda_revision=rev)
        return self._event_out(ev, assessment.id if assessment else None)

    def patch_event(self, user_id: str, event_id: str, body: EventPatch) -> EventOut:
        ev = self._get_owned(Event, event_id, user_id)
        if ev.source != "manual":
            raise Forbidden(
                "Imported events are read-only; use the confirmation action instead",
                code="imported_read_only",
            )
        check_revision(ev.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        for name in fields & {"type", "title", "status", "busy"}:
            if getattr(body, name) is None:
                raise ValidationFailed(f"{name} cannot be null", details=[{"field": name}])

        # Switching between timed and all-day clears the other representation.
        start_at, end_at, all_day = ev.start_at, ev.end_at, ev.all_day_date
        if fields & {"start_at", "end_at"}:
            start_at = body.start_at if "start_at" in fields else start_at
            end_at = body.end_at if "end_at" in fields else end_at
            if "all_day_date" not in fields:
                all_day = None
        if "all_day_date" in fields:
            all_day = body.all_day_date
            if body.all_day_date is not None and not fields & {"start_at", "end_at"}:
                start_at = end_at = None
        try:
            check_timing(start_at, end_at, all_day)
        except ValueError as exc:
            raise ValidationFailed(str(exc)) from exc

        mapping = {"group": "group_name"}
        changed = (start_at, end_at, all_day) != (ev.start_at, ev.end_at, ev.all_day_date)
        ev.start_at, ev.end_at, ev.all_day_date = start_at, end_at, all_day
        for name in fields - {"start_at", "end_at", "all_day_date"}:
            attr = mapping.get(name, name)
            value = getattr(body, name)
            if getattr(ev, attr) != value:
                setattr(ev, attr, value)
                changed = True
        if not changed:
            return self.get_event(user_id, event_id)

        ev.revision += 1
        self.db.flush()
        assessment = self._sync_assessment(user_id, ev)
        rev = self.bump_revision(user_id)
        self._emit(user_id, "EventChanged", ev.id, ev.revision, action="updated", agenda_revision=rev)
        return self._event_out(ev, assessment.id if assessment else None)

    def delete_event(self, user_id: str, event_id: str, expected_revision: int | None) -> None:
        ev = self._get_owned(Event, event_id, user_id)
        if ev.source != "manual":
            raise Forbidden("Imported events cannot be deleted", code="imported_read_only")
        check_revision(ev.revision, expected_revision)
        assessment = self._assessment_for(ev.id)
        if assessment is not None:
            self.db.execute(
                delete(AssessmentConcept).where(AssessmentConcept.assessment_id == assessment.id)
            )
            self._emit(
                user_id, "AssessmentChanged", assessment.id, assessment.revision + 1, action="deleted"
            )
            self.db.delete(assessment)
        self.db.delete(ev)
        rev = self.bump_revision(user_id)
        self._emit(user_id, "EventChanged", ev.id, ev.revision + 1, action="deleted", agenda_revision=rev)

    def confirm_event(self, user_id: str, event_id: str, body: EventConfirm) -> EventOut:
        """Resolve an ambiguous imported slot (never inferred from colour or heuristics)."""
        ev = self._get_owned(Event, event_id, user_id)
        check_revision(ev.revision, body.expected_revision)
        if not ev.confirmation_required:
            raise Conflict("No confirmation pending for this event", code="nothing_to_confirm")
        ev.confirmation_required = False
        ev.status = "confirmed" if body.decision == "confirm" else "cancelled"
        ev.revision += 1
        self.db.flush()
        assessment = self._sync_assessment(user_id, ev)
        rev = self.bump_revision(user_id)
        self._emit(
            user_id, "EventChanged", ev.id, ev.revision, action=body.decision, agenda_revision=rev
        )
        return self._event_out(ev, assessment.id if assessment else None)

    def import_events(
        self, user_id: str, source: str, items: list[EventImportItem]
    ) -> ImportResult:
        """Idempotent upsert keyed by (source, external_id). Absent items are not deleted
        (an incomplete listing is not a deletion)."""
        if source == "manual":
            raise ValidationFailed("manual is not an import source")
        created = updated = unchanged = 0
        seen: set[str] = set()
        for item in items:
            if item.external_id in seen:
                raise ValidationFailed("Duplicate external_id in import", details=[item.external_id])
            seen.add(item.external_id)
            digest = hashlib.sha256(
                json.dumps(item.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
            ev = self.db.scalar(
                select(Event).where(
                    Event.user_id == user_id,
                    Event.source == source,
                    Event.external_id == item.external_id,
                )
            )
            if ev is not None and ev.source_hash == digest:
                unchanged += 1
                continue
            if item.status == "cancelled":
                if ev is None:
                    continue
                if ev.status == "cancelled" and not ev.busy:
                    ev.source_hash = digest
                    unchanged += 1
                    continue
                ev.status = "cancelled"
                ev.busy = False
                ev.confirmation_required = False
                ev.source_hash = digest
                ev.revision += 1
                self.db.flush()
                self._sync_assessment(user_id, ev)
                self._emit(
                    user_id,
                    "EventChanged",
                    ev.id,
                    ev.revision,
                    action="source_cancelled",
                    source=source,
                )
                updated += 1
                continue
            is_new = ev is None
            if ev is None:
                ev = Event(user_id=user_id, source=source, external_id=item.external_id)
                self.db.add(ev)
            else:
                ev.revision += 1
            ev.type, ev.title, ev.subject_id = item.type, item.title, item.subject_id
            ev.start_at, ev.end_at, ev.all_day_date = item.start_at, item.end_at, item.all_day_date
            ev.room, ev.teacher, ev.group_name = item.room, item.teacher, item.group
            ev.recurrence_key = item.recurrence_key
            ev.busy = domain.default_busy(item.type) if item.busy is None else item.busy
            ev.confirmation_required = item.confirmation_required
            ev.status = "tentative" if item.confirmation_required else (item.status or "confirmed")
            ev.source_hash = digest
            self.db.flush()
            self._sync_assessment(user_id, ev)
            self._emit(
                user_id,
                "EventChanged",
                ev.id,
                ev.revision,
                action="imported" if is_new else "source_changed",
                source=source,
            )
            if is_new:
                created += 1
            else:
                updated += 1
        rev = self.bump_revision(user_id) if created or updated else self.agenda_revision(user_id)
        return ImportResult(created=created, updated=updated, unchanged=unchanged, agenda_revision=rev)

    def _sync_assessment(self, user_id: str, ev: Event) -> Assessment | None:
        """Keep the assessment linked to an exam/presentation event consistent with it."""
        assessment = self._assessment_for(ev.id)
        wants = domain.creates_assessment(ev.type)
        if assessment is None and not wants:
            return None
        tz = self._tz(user_id)
        interval = domain.event_interval(ev.start_at, ev.end_at, ev.all_day_date, tz)
        deadline = interval.start
        duration = interval.minutes if ev.all_day_date is None else None
        status = "active" if wants and ev.status != "cancelled" else "cancelled"
        if assessment is None:
            assessment = Assessment(
                user_id=user_id,
                event_id=ev.id,
                subject_id=ev.subject_id,
                deadline_at=deadline,
                duration_min=duration,
                status=status,
            )
            self.db.add(assessment)
            self.db.flush()
            self._emit(user_id, "AssessmentChanged", assessment.id, 1, action="created")
            return assessment
        before = (assessment.deadline_at, assessment.duration_min, assessment.status)
        assessment.deadline_at = deadline
        if ev.all_day_date is None:
            assessment.duration_min = duration
        assessment.status = status
        if assessment.subject_id is None:
            assessment.subject_id = ev.subject_id
        if before != (assessment.deadline_at, assessment.duration_min, assessment.status):
            assessment.revision += 1
            self._emit(
                user_id, "AssessmentChanged", assessment.id, assessment.revision, action="updated"
            )
        return assessment

    # ============================================================== assessments ====
    def _assessment_out(self, a: Assessment) -> AssessmentOut:
        ev = self.db.get(Event, a.event_id) if a.event_id else None
        concepts = list(
            self.db.scalars(
                select(AssessmentConcept.concept_id)
                .where(AssessmentConcept.assessment_id == a.id)
                .order_by(AssessmentConcept.concept_id)
            )
        )
        return AssessmentOut(
            id=a.id,
            event_id=a.event_id,
            title=ev.title if ev else None,
            type=ev.type if ev else None,  # type: ignore[arg-type]
            subject_id=a.subject_id,
            deadline_at=a.deadline_at,
            format=a.format,  # type: ignore[arg-type]
            duration_min=a.duration_min,
            difficulty=a.difficulty,  # type: ignore[arg-type]
            scope_status=a.scope_status,  # type: ignore[arg-type]
            scope_revision=a.scope_revision,
            concept_ids=concepts,
            status=a.status,  # type: ignore[arg-type]
            confirmation_required=bool(ev and ev.confirmation_required),
            revision=a.revision,
        )

    def list_assessments(
        self,
        user_id: str,
        *,
        status: str | None,
        scope_status: str | None,
        after: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[AssessmentOut], str | None]:
        q = select(Assessment).where(Assessment.user_id == user_id)
        if status:
            q = q.where(Assessment.status == status)
        if scope_status:
            q = q.where(Assessment.scope_status == scope_status)
        if after:
            q = q.where(Assessment.deadline_at >= after)
        q = q.order_by(Assessment.deadline_at.is_(None), Assessment.deadline_at, Assessment.id)
        page, nxt = paginate(list(self.db.scalars(q)), limit, cursor)
        return [self._assessment_out(a) for a in page], nxt

    def patch_assessment(self, user_id: str, assessment_id: str, body: AssessmentPatch) -> AssessmentOut:
        a = self._get_owned(Assessment, assessment_id, user_id)
        catalog = get_catalog(self.db)
        if "subject_id" in body.model_fields_set:
            catalog.check_subject(user_id, body.subject_id)
        if body.concept_ids:
            catalog.check_concepts(user_id, body.concept_ids)
        check_revision(a.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        if "scope_status" in fields and body.scope_status is None:
            raise ValidationFailed("scope_status cannot be null")
        changed = False
        for name in fields & {"subject_id", "format", "duration_min", "difficulty", "scope_status"}:
            value = getattr(body, name)
            if getattr(a, name) != value:
                setattr(a, name, value)
                changed = True
        if "concept_ids" in fields:
            wanted = sorted(set(body.concept_ids or []))
            current = sorted(
                self.db.scalars(
                    select(AssessmentConcept.concept_id).where(
                        AssessmentConcept.assessment_id == a.id
                    )
                )
            )
            if wanted != current:
                self.db.execute(
                    delete(AssessmentConcept).where(AssessmentConcept.assessment_id == a.id)
                )
                for cid in wanted:
                    self.db.add(AssessmentConcept(assessment_id=a.id, concept_id=cid))
                a.scope_revision += 1
                changed = True
        if changed:
            a.revision += 1
            self.db.flush()
            rev = self.bump_revision(user_id)
            self._emit(
                user_id,
                "AssessmentChanged",
                a.id,
                a.revision,
                action="updated",
                scope_revision=a.scope_revision,
                agenda_revision=rev,
            )
        return self._assessment_out(a)

    # ==================================================================== tasks ====
    def _task_out(self, t: Task) -> TaskOut:
        now = self.clock.now()
        return TaskOut(
            id=t.id,
            title=t.title,
            description=t.description,
            subject_id=t.subject_id,
            parent_task_id=t.parent_task_id,
            deadline_at=t.deadline_at,
            estimated_min=t.estimated_min,
            remaining_min=t.remaining_min,
            priority=t.priority,
            status=t.status,  # type: ignore[arg-type]
            locked=t.locked,
            splittable=t.splittable,
            origin=t.origin,  # type: ignore[arg-type]
            overdue=bool(t.deadline_at and t.deadline_at < now and t.status not in _CLOSED_TASK),
            revision=t.revision,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )

    def _check_parent(self, user_id: str, task_id: str | None, parent_id: str | None) -> None:
        if parent_id is None:
            return
        parent = self._get_owned(Task, parent_id, user_id)
        seen = {task_id} if task_id else set()
        node: Task | None = parent
        while node is not None:
            if node.id in seen:
                raise ValidationFailed("Task hierarchy cannot contain a cycle", code="task_cycle")
            seen.add(node.id)
            node = self.db.get(Task, node.parent_task_id) if node.parent_task_id else None

    def list_tasks(
        self,
        user_id: str,
        *,
        status: list[str] | None,
        subject_id: str | None,
        due_before: datetime | None,
        limit: int,
        cursor: str | None,
    ) -> tuple[list[TaskOut], str | None]:
        q = select(Task).where(Task.user_id == user_id)
        if status:
            q = q.where(Task.status.in_(status))
        if subject_id:
            q = q.where(Task.subject_id == subject_id)
        if due_before:
            q = q.where(Task.deadline_at < due_before)
        q = q.order_by(
            Task.deadline_at.is_(None), Task.deadline_at, Task.priority.desc(), Task.created_at, Task.id
        )
        page, nxt = paginate(list(self.db.scalars(q)), limit, cursor)
        return [self._task_out(t) for t in page], nxt

    def create_task(self, user_id: str, body: TaskCreate) -> TaskOut:
        get_catalog(self.db).check_subject(user_id, body.subject_id)
        self._check_parent(user_id, None, body.parent_task_id)
        remaining = body.remaining_min if body.remaining_min is not None else body.estimated_min
        t = Task(
            user_id=user_id,
            title=body.title,
            description=body.description,
            subject_id=body.subject_id,
            parent_task_id=body.parent_task_id,
            deadline_at=body.deadline_at,
            estimated_min=body.estimated_min,
            remaining_min=remaining,
            priority=body.priority,
            locked=body.locked,
            splittable=body.splittable,
            origin="manual",
        )
        self.db.add(t)
        self.db.flush()
        rev = self.bump_revision(user_id)
        self._emit(user_id, "TaskChanged", t.id, t.revision, action="created", agenda_revision=rev)
        return self._task_out(t)

    def sync_moodle_task(
        self, user_id: str, external_id: str, item: EventImportItem | None, *, cancelled: bool,
    ) -> bool:
        """Mirror an assignment while keeping task-specific preferences and progress."""
        external_key = f"moodle:{hashlib.sha256(external_id.encode()).hexdigest()}"
        task = self.db.scalar(select(Task).where(
            Task.user_id == user_id, Task.origin == "imported", Task.external_key == external_key,
        ))
        if cancelled:
            if task is None or task.status in _CLOSED_TASK:
                return False
            task.status = "cancelled"
        elif item is None or task is not None and task.status == "cancelled":
            return False
        elif task is None:
            task = Task(
                user_id=user_id, origin="imported", external_key=external_key,
                title=item.title, subject_id=item.subject_id, deadline_at=item.start_at,
            )
            self.db.add(task)
            self.db.flush()
            self._emit(user_id, "TaskChanged", task.id, task.revision, action="source_imported")
            return True
        else:
            previous = self.db.scalar(select(Event).where(
                Event.user_id == user_id, Event.source == "moodle", Event.external_id == external_id,
            ))
            if previous is None:
                return False
            current_fields = (task.title, task.subject_id, task.deadline_at)
            new_fields = (
                item.title if task.title == previous.title else task.title,
                item.subject_id if task.subject_id == previous.subject_id else task.subject_id,
                item.start_at if task.deadline_at == previous.start_at else task.deadline_at,
            )
            if current_fields == new_fields:
                return False
            task.title, task.subject_id, task.deadline_at = new_fields
        task.revision += 1
        self.db.flush()
        self._emit(user_id, "TaskChanged", task.id, task.revision, action="source_changed")
        return True

    def patch_task(self, user_id: str, task_id: str, body: TaskPatch) -> TaskOut:
        t = self._get_owned(Task, task_id, user_id)
        check_revision(t.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        for name in fields & {"title", "priority", "status", "locked", "splittable"}:
            if getattr(body, name) is None:
                raise ValidationFailed(f"{name} cannot be null", details=[{"field": name}])
        if "parent_task_id" in fields:
            self._check_parent(user_id, t.id, body.parent_task_id)
        changed = False
        for name in fields:
            value = getattr(body, name)
            if getattr(t, name) != value:
                setattr(t, name, value)
                changed = True
        if t.status == "done" and t.remaining_min:
            t.remaining_min = 0
            changed = True
        if changed:
            t.revision += 1
            self.db.flush()
            rev = self.bump_revision(user_id)
            self._emit(user_id, "TaskChanged", t.id, t.revision, action="updated", agenda_revision=rev)
        return self._task_out(t)

    def delete_task(self, user_id: str, task_id: str, expected_revision: int | None) -> None:
        t = self._get_owned(Task, task_id, user_id)
        check_revision(t.revision, expected_revision)
        if t.origin == "imported" and t.external_key and t.external_key.startswith("moodle:"):
            if t.status != "cancelled":
                t.status = "cancelled"
                t.revision += 1
                rev = self.bump_revision(user_id)
                self._emit(user_id, "TaskChanged", t.id, t.revision, action="cancelled", agenda_revision=rev)
            return
        children = self.db.scalar(
            select(func.count()).select_from(Task).where(Task.parent_task_id == t.id)
        )
        self.db.delete(t)  # subtasks removed by ON DELETE CASCADE
        rev = self.bump_revision(user_id)
        self._emit(
            user_id,
            "TaskChanged",
            t.id,
            t.revision + 1,
            action="deleted",
            subtasks_deleted=children,
            agenda_revision=rev,
        )

    # ============================================================= availability ====
    def availability(self, user_id: str, start: datetime, end: datetime) -> AvailabilityOut:
        snap, prefs, pending = self.availability_snapshot(user_id, start, end)
        return self._availability_out(user_id, snap, prefs, pending)

    def availability_snapshot(
        self, user_id: str, start: datetime, end: datetime
    ) -> tuple[domain.AvailabilitySnapshot, PlanningPreferences, int]:
        """Domain-level snapshot reused by the planner (free time before sessions)."""
        if end <= start:
            raise ValidationFailed("end must be after start")
        if end - start > domain.MAX_AVAILABILITY_RANGE:
            raise ValidationFailed("availability range is limited to 31 days")
        prefs = get_planning_preferences(self.db, user_id)
        tz = ZoneInfo(prefs.timezone)
        range_ = domain.Interval(start.astimezone(UTC), end.astimezone(UTC))
        events, _ = self.list_events(
            user_id, start, end, types=None, include_cancelled=False, limit=10_000, cursor=None
        )
        busy = [
            domain.BusyBlock(e.id, iv)
            for e in events
            if e.busy
            for iv in domain.clip(
                [domain.event_interval(e.start_at, e.end_at, e.all_day_date, tz)], range_
            )
        ]
        snap = domain.compute_availability(prefs, busy, range_, self.clock.now())
        pending = sum(1 for e in events if e.confirmation_required)
        return snap, prefs, pending

    def _availability_out(
        self,
        user_id: str,
        snap: domain.AvailabilitySnapshot,
        prefs: PlanningPreferences,
        pending: int,
    ) -> AvailabilityOut:
        range_ = snap.range
        warnings = list(snap.warnings)
        if pending:
            warnings.append("PENDING_CONFIRMATIONS")
        return AvailabilityOut(
            input_revision=f"agenda:{self.agenda_revision(user_id)};prefs:{prefs.revision}",
            timezone=prefs.timezone,
            start_at=range_.start,
            end_at=range_.end,
            free=[{"start_at": i.start, "end_at": i.end, "minutes": i.minutes} for i in snap.free],
            busy=[
                {
                    "event_id": b.event_id,
                    "start_at": b.interval.start,
                    "end_at": b.interval.end,
                    "minutes": b.interval.minutes,
                }
                for b in snap.busy
            ],
            conflicts=[
                {
                    "event_id_a": c.event_id_a,
                    "event_id_b": c.event_id_b,
                    "start_at": c.overlap.start,
                    "end_at": c.overlap.end,
                    "minutes": c.overlap.minutes,
                }
                for c in snap.conflicts
            ],
            days=[
                {
                    "date": d.date,
                    "free_min": d.free_min,
                    "daily_max_min": d.daily_max_min,
                    "plannable_min": d.plannable_min,
                }
                for d in snap.days
            ],
            total_free_min=sum(i.minutes for i in snap.free),
            weekly_max_min=prefs.weekly_max_min,
            min_session_min=prefs.min_session_min,
            max_session_min=prefs.max_session_min,
            pending_confirmations=pending,
            warnings=warnings,
        )
