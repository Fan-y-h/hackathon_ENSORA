"""Workspace use cases: Focus Room (T23), Smart Notes (T22), Error Notebook (T22/T24).

Focus time is computed from server-persisted intervals, so a reload never loses time and
breaks/pauses are never counted as work (R26).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import Clock, check_revision
from app.core.db import new_id
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import record_event
from app.modules.workspace.models import (
    Annotation,
    ErrorEntry,
    FocusSession,
    Note,
    Notebook,
    NoteVersion,
    WorkInterval,
)
from app.modules.workspace.schemas import (
    AnnotationCreate,
    AnnotationOut,
    ErrorCreate,
    ErrorOut,
    ErrorPatch,
    FocusFinish,
    FocusOut,
    FocusStart,
    FocusTransition,
    NotebookCreate,
    NotebookOut,
    NoteCreate,
    NoteOut,
    NotePatch,
    NoteVersionOut,
)

MODES = {"classic": (25, 5), "long": (50, 10), "deep": (90, 15), "free": (None, None)}
RECURRING_THRESHOLD = 2
CONSOLIDATION_MIN = 25


def _minutes(a: datetime, b: datetime) -> float:
    return max(0.0, (b - a).total_seconds() / 60)


class WorkspaceService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def _owned(self, model, entity_id: str, user_id: str):
        obj = self.db.get(model, entity_id)
        if obj is None or obj.user_id != user_id:
            raise NotFound(f"{model.__name__} not found")
        return obj

    # ===================================================================== focus ====
    def _intervals(self, f: FocusSession) -> list[WorkInterval]:
        return list(self.db.scalars(
            select(WorkInterval).where(WorkInterval.focus_id == f.id).order_by(WorkInterval.started_at, WorkInterval.id)
        ))

    def _open(self, f: FocusSession) -> WorkInterval | None:
        return self.db.scalar(select(WorkInterval).where(WorkInterval.focus_id == f.id, WorkInterval.ended_at.is_(None)))

    def _focus_out(self, f: FocusSession) -> FocusOut:
        now = self.clock.now()
        ivs = self._intervals(f)
        work = sum(_minutes(i.started_at, i.ended_at or now) for i in ivs if i.kind == "work")
        brk = sum(_minutes(i.started_at, i.ended_at or now) for i in ivs if i.kind == "break")
        current = next((i for i in ivs if i.ended_at is None), None)
        remaining = None
        if current is not None:
            target = f.work_min if current.kind == "work" else f.break_min
            if target:
                remaining = max(0, round(target - _minutes(current.started_at, now)))
        return FocusOut(
            id=f.id, mode=f.mode, status=f.status, goal=f.goal, work_min=f.work_min, break_min=f.break_min,  # type: ignore[arg-type]
            study_session_id=f.study_session_id, task_id=f.task_id, started_at=f.started_at,
            finished_at=f.finished_at, active_min=int(work), break_total_min=int(brk),
            phase_started_at=current.started_at if current else None, phase_remaining_min=remaining,
            intervals=[{"kind": i.kind, "started_at": i.started_at, "ended_at": i.ended_at} for i in ivs],
            reflection=f.reflection, revision=f.revision,
        )

    def active_focus(self, user_id: str) -> FocusOut | None:
        f = self.db.scalar(select(FocusSession).where(FocusSession.user_id == user_id, FocusSession.status != "finished"))
        return self._focus_out(f) if f else None

    def start_focus(self, user_id: str, body: FocusStart) -> FocusOut:
        running = self.db.scalar(select(FocusSession).where(FocusSession.user_id == user_id, FocusSession.status != "finished"))
        if running is not None:
            raise Conflict("A focus session is already running", code="focus_running", details={"focus_id": running.id})
        if body.mode == "custom":
            if body.work_min is None or body.break_min is None:
                raise ValidationFailed("custom mode requires work_min and break_min")
            work, brk = body.work_min, body.break_min
        else:
            work, brk = MODES[body.mode]
        if body.study_session_id:
            from app.modules.planner.models import StudySession
            from app.modules.planner.plan_service import PlannerService

            s = self.db.get(StudySession, body.study_session_id)
            if s is None or s.user_id != user_id:
                raise NotFound("Study session not found")
            if s.status == "planned":
                PlannerService(self.db, self.clock).start_session(user_id, s.id)
            elif s.status != "in_progress":
                raise Conflict(f"Study session is {s.status}", code="session_not_open")
        if body.task_id:
            from app.modules.planner.models import Task

            t = self.db.get(Task, body.task_id)
            if t is None or t.user_id != user_id:
                raise NotFound("Task not found")
        now = self.clock.now()
        f = FocusSession(
            id=new_id(), user_id=user_id, study_session_id=body.study_session_id, task_id=body.task_id,
            mode=body.mode, goal=body.goal, work_min=work, break_min=brk, status="working", started_at=now,
        )
        self.db.add(f)
        self.db.flush()
        self.db.add(WorkInterval(focus_id=f.id, kind="work", started_at=now))
        self.db.flush()
        return self._focus_out(f)

    def transition(self, user_id: str, focus_id: str, body: FocusTransition) -> FocusOut:
        f = self._owned(FocusSession, focus_id, user_id)
        if f.status == "finished":
            raise Conflict("Focus session is finished", code="focus_finished")
        now = self.clock.now()
        target = {"pause": "paused", "resume": "working", "work": "working", "break": "break"}.get(body.action)
        if body.action == "extend":
            if f.work_min is None:
                raise ValidationFailed("free mode has no timer to extend")
            f.work_min += body.minutes or 5
        elif target == f.status:
            return self._focus_out(f)  # idempotent repeat (e.g. double click)
        else:
            allowed = {
                "working": {"paused", "break"},
                "paused": {"working", "break"},
                "break": {"working"},
            }[f.status]
            if target not in allowed:
                raise Conflict(f"Cannot {body.action} while {f.status}", code="invalid_transition")
            current = self._open(f)
            if current is not None:
                current.ended_at = now
            if target in ("working", "break"):
                self.db.add(WorkInterval(focus_id=f.id, kind="work" if target == "working" else "break", started_at=now))
            f.status = target
        f.revision += 1
        self.db.flush()
        return self._focus_out(f)

    def finish_focus(self, user_id: str, focus_id: str, body: FocusFinish) -> FocusOut:
        f = self._owned(FocusSession, focus_id, user_id)
        if f.status == "finished":
            raise Conflict("Focus session already finished", code="focus_finished")
        now = self.clock.now()
        current = self._open(f)
        if current is not None:
            current.ended_at = now
        f.status, f.finished_at = "finished", now
        f.reflection = {"outcome": body.outcome, **(body.reflection.model_dump() if body.reflection else {})}
        f.revision += 1
        self.db.flush()
        out = self._focus_out(f)
        if f.study_session_id and body.outcome != "abandoned":
            from app.modules.planner.models import StudySession
            from app.modules.planner.plan_service import PlannerService
            from app.modules.planner.schemas import Reflection, SessionFinish

            s = self.db.get(StudySession, f.study_session_id)
            if s is not None and s.status == "in_progress":
                r = body.reflection
                PlannerService(self.db, self.clock).finish_session(user_id, s.id, SessionFinish(
                    outcome=body.outcome, actual_min=out.active_min, remaining_min=body.remaining_min,
                    reflection=Reflection(difficulty=r.difficulty, objective_met=r.objective_met, notes=r.notes) if r else None,
                ))
        record_event(self.db, user_id=user_id, type="FocusFinished", entity_id=f.id, revision=f.revision,
                     payload={"active_min": out.active_min})
        return out

    def get_focus(self, user_id: str, focus_id: str) -> FocusOut:
        return self._focus_out(self._owned(FocusSession, focus_id, user_id))

    # ===================================================================== notes ====
    def _notebook_out(self, nb: Notebook) -> NotebookOut:
        count = self.db.scalar(select(func.count()).select_from(Note).where(Note.notebook_id == nb.id)) or 0
        return NotebookOut(id=nb.id, title=nb.title, subject_id=nb.subject_id, note_count=count, revision=nb.revision)

    def list_notebooks(self, user_id: str) -> list[NotebookOut]:
        rows = self.db.scalars(select(Notebook).where(Notebook.user_id == user_id).order_by(Notebook.title, Notebook.id))
        return [self._notebook_out(nb) for nb in rows]

    def create_notebook(self, user_id: str, body: NotebookCreate) -> NotebookOut:
        nb = Notebook(user_id=user_id, title=body.title, subject_id=body.subject_id)
        self.db.add(nb)
        self.db.flush()
        return self._notebook_out(nb)

    @staticmethod
    def _note_out(n: Note) -> NoteOut:
        return NoteOut(
            id=n.id, notebook_id=n.notebook_id, title=n.title, markdown=n.markdown, concept_id=n.concept_id,
            chapter_id=n.chapter_id, citations=n.citations, origin=n.origin, revision=n.revision,
            created_at=n.created_at, updated_at=n.updated_at,
        )

    def _snapshot(self, n: Note) -> None:
        self.db.add(NoteVersion(note_id=n.id, revision=n.revision, title=n.title, markdown=n.markdown,
                                citations=n.citations))

    def create_note(self, user_id: str, body: NoteCreate) -> NoteOut:
        self._owned(Notebook, body.notebook_id, user_id)
        n = Note(
            id=new_id(), notebook_id=body.notebook_id, user_id=user_id, title=body.title, markdown=body.markdown,
            concept_id=body.concept_id, chapter_id=body.chapter_id,
            citations=[c.model_dump() for c in body.citations], origin=body.origin, revision=1,
        )
        self.db.add(n)
        self.db.flush()
        self._snapshot(n)
        self.db.flush()
        return self._note_out(n)

    def list_notes(self, user_id: str, notebook_id: str | None, concept_id: str | None, q: str | None) -> list[NoteOut]:
        query = select(Note).where(Note.user_id == user_id)
        if notebook_id:
            query = query.where(Note.notebook_id == notebook_id)
        if concept_id:
            query = query.where(Note.concept_id == concept_id)
        if q:
            like = f"%{q.lower()}%"
            query = query.where(func.lower(Note.title).like(like) | func.lower(Note.markdown).like(like))
        return [self._note_out(n) for n in self.db.scalars(query.order_by(Note.updated_at.desc(), Note.id))]

    def get_note(self, user_id: str, note_id: str) -> NoteOut:
        return self._note_out(self._owned(Note, note_id, user_id))

    def patch_note(self, user_id: str, note_id: str, body: NotePatch) -> NoteOut:
        n = self._owned(Note, note_id, user_id)
        check_revision(n.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        changed = False
        for name in fields:
            value = getattr(body, name)
            if name in ("title", "markdown") and value is None:
                raise ValidationFailed(f"{name} cannot be null")
            if name == "citations":
                value = [c.model_dump() for c in value or []]
            if getattr(n, name) != value:
                setattr(n, name, value)
                changed = True
        if changed:
            n.revision += 1
            self.db.flush()
            self._snapshot(n)
        return self._note_out(n)

    def delete_note(self, user_id: str, note_id: str) -> None:
        self.db.delete(self._owned(Note, note_id, user_id))

    def note_versions(self, user_id: str, note_id: str) -> list[NoteVersionOut]:
        self._owned(Note, note_id, user_id)
        rows = self.db.scalars(select(NoteVersion).where(NoteVersion.note_id == note_id).order_by(NoteVersion.revision))
        return [NoteVersionOut(revision=v.revision, title=v.title, markdown=v.markdown, citations=v.citations,
                               created_at=v.created_at) for v in rows]

    # ==================================================================== errors ====
    @staticmethod
    def _error_out(e: ErrorEntry) -> ErrorOut:
        return ErrorOut(
            id=e.id, concept_id=e.concept_id, subject_id=e.subject_id, source=e.source, source_ref=e.source_ref,
            category=e.category, prompt_excerpt=e.prompt_excerpt, reasoning=e.reasoning,  # type: ignore[arg-type]
            remediation=e.remediation, occurrences=e.occurrences, recurring=e.occurrences >= RECURRING_THRESHOLD,
            last_seen_at=e.last_seen_at, resolved_at=e.resolved_at, revision=e.revision,
        )

    def list_errors(self, user_id: str, resolved: bool | None, concept_id: str | None) -> list[ErrorOut]:
        q = select(ErrorEntry).where(ErrorEntry.user_id == user_id)
        if resolved is True:
            q = q.where(ErrorEntry.resolved_at.is_not(None))
        elif resolved is False:
            q = q.where(ErrorEntry.resolved_at.is_(None))
        if concept_id:
            q = q.where(ErrorEntry.concept_id == concept_id)
        return [self._error_out(e) for e in self.db.scalars(q.order_by(ErrorEntry.last_seen_at.desc(), ErrorEntry.id))]

    def create_error(self, user_id: str, body: ErrorCreate) -> ErrorOut:
        e = ErrorEntry(user_id=user_id, source="manual", dedupe_key=f"manual:{new_id()}",
                       last_seen_at=self.clock.now(), **body.model_dump())
        self.db.add(e)
        self.db.flush()
        return self._error_out(e)

    def patch_error(self, user_id: str, error_id: str, body: ErrorPatch) -> ErrorOut:
        e = self._owned(ErrorEntry, error_id, user_id)
        check_revision(e.revision, body.expected_revision)
        for name in ("category", "reasoning", "remediation"):
            if name in body.model_fields_set:
                value = getattr(body, name)
                if name == "category" and value is None:
                    raise ValidationFailed("category cannot be null")
                setattr(e, name, value)
        if body.resolved is not None:
            e.resolved_at = self.clock.now() if body.resolved else None
        e.revision += 1
        return self._error_out(e)

    # ================================================================ annotations ====
    @staticmethod
    def _annotation_out(a: Annotation) -> AnnotationOut:
        return AnnotationOut(
            id=a.id,
            document_id=a.document_id,
            document_version_id=a.document_version_id,
            chunk_id=a.chunk_id,
            locator=a.locator,
            excerpt=a.excerpt,
            comment=a.comment,
            note_id=a.note_id,
            created_at=a.created_at,
        )

    def list_annotations(
        self, user_id: str, document_id: str | None = None, note_id: str | None = None
    ) -> list[AnnotationOut]:
        q = select(Annotation).where(Annotation.user_id == user_id)
        if document_id:
            q = q.where(Annotation.document_id == document_id)
        if note_id:
            q = q.where(Annotation.note_id == note_id)
        rows = self.db.scalars(q.order_by(Annotation.created_at.desc(), Annotation.id))
        return [self._annotation_out(a) for a in rows]

    def create_annotation(self, user_id: str, body: AnnotationCreate) -> AnnotationOut:
        if body.note_id is not None:
            self._owned(Note, body.note_id, user_id)
        a = Annotation(
            id=new_id(),
            user_id=user_id,
            note_id=body.note_id,
            document_id=body.document_id,
            document_version_id=body.document_version_id,
            chunk_id=body.chunk_id,
            locator=body.locator,
            excerpt=body.excerpt,
            comment=body.comment,
        )
        self.db.add(a)
        self.db.flush()
        return self._annotation_out(a)


# ---- interfaces used by other modules ---------------------------------------------------------
def record_item_errors(
    db: Session,
    user_id: str,
    now: datetime,
    source: str,
    items: list[tuple[str, str, list[str], str | None]],
) -> int:
    """Log wrong quiz/exam items (question_id, prompt, concept_ids, subject_id).
    Dedupe by question: a repeated failure increments ``occurrences`` and reopens the entry."""
    created = 0
    for question_id, prompt, concept_ids, subject_id in items:
        for concept_id in concept_ids or [None]:
            key = f"question:{question_id}:{concept_id or '-'}"
            e = db.scalar(select(ErrorEntry).where(ErrorEntry.user_id == user_id, ErrorEntry.dedupe_key == key))
            if e is None:
                db.add(ErrorEntry(
                    user_id=user_id, concept_id=concept_id, subject_id=subject_id, source=source,
                    source_ref=question_id, category="unclassified", prompt_excerpt=prompt[:500],
                    dedupe_key=key, last_seen_at=now,
                ))
                created += 1
            else:
                e.occurrences += 1
                e.last_seen_at = now
                e.resolved_at = None
                e.revision += 1
    db.flush()
    return created


@dataclass(frozen=True)
class ErrorNeed:
    key: str
    subject_id: str | None
    concept_ids: tuple[str, ...]
    estimated_min: int
    title: str


def recurring_error_needs(db: Session, user_id: str) -> list[ErrorNeed]:
    groups: dict[str, list[ErrorEntry]] = defaultdict(list)
    for e in db.scalars(select(ErrorEntry).where(
        ErrorEntry.user_id == user_id, ErrorEntry.resolved_at.is_(None),
        ErrorEntry.occurrences >= RECURRING_THRESHOLD,
    )):
        groups[e.concept_id or e.id].append(e)
    out = []
    for group, entries in sorted(groups.items()):
        top = max(e.occurrences for e in entries)
        out.append(ErrorNeed(
            key=f"errors:{group}:{top}", subject_id=entries[0].subject_id,
            concept_ids=(group,) if entries[0].concept_id else (), estimated_min=CONSOLIDATION_MIN,
            title=f"Erreur récurrente — {group if entries[0].concept_id else 'carnet'}",
        ))
    return out
