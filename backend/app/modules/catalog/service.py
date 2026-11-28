"""Read-only subject aggregation over P1 source catalog and P3 domain tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import Clock
from app.core.errors import NotFound
from app.modules.catalog.schemas import (
    SubjectAssessmentOut,
    SubjectConceptOut,
    SubjectDeckOut,
    SubjectDetailOut,
    SubjectDocumentOut,
    SubjectListOut,
    SubjectNoteOut,
    SubjectQuizOut,
    SubjectSummaryOut,
)
from app.modules.grades.models import AcademicNode, AcademicProgram
from app.modules.learning import domain as ldomain
from app.modules.learning.models import Deck, Flashcard, Mastery, Question, Quiz
from app.modules.planner.models import Assessment, Event, StudySession, Task
from app.modules.workspace.models import Note, Notebook


def _read_p1_subjects_and_docs(p1_engine) -> tuple[dict[str, dict[str, Any]], dict[str, list[SubjectDocumentOut]]]:
    if p1_engine is None:
        return {}, {}
    try:
        from backend.app.core.database import connections, documents, records
    except Exception:
        return {}, {}

    subjects: dict[str, dict[str, Any]] = {}
    docs_by_subject: dict[str, list[SubjectDocumentOut]] = defaultdict(list)

    with p1_engine.connect() as conn:
        for source, ext_id, payload in conn.execute(
            select(connections.c.source, records.c.external_id, records.c.payload)
            .join(connections, records.c.connection_id == connections.c.id)
            .where(records.c.kind == "subject")
        ):
            data = payload or {}
            code = data.get("code") or data.get("source_code")
            sid = (code or ext_id or "").strip()
            if not sid:
                continue
            subjects[sid] = {
                "id": sid,
                "code": code,
                "title": str(data.get("title") or sid),
                "source": str(source or "pamplemousse"),
            }

        for doc_id, active_ver, meta, source in conn.execute(
            select(documents.c.id, documents.c.active_version_id, documents.c.metadata_json, connections.c.source)
            .join(connections, documents.c.connection_id == connections.c.id)
        ):
            m = meta or {}
            match = m.get("match") or {}
            sid = match.get("subject_id") or m.get("subject_id")
            if not sid:
                continue
            sid = str(sid).strip()
            classification = m.get("classification") or {}
            provenance = m.get("provenance") or {}
            docs_by_subject[sid].append(
                SubjectDocumentOut(
                    id=str(doc_id),
                    title=str(m.get("title") or "Document"),
                    resource_type=str(classification.get("resource_type") or "other"),
                    active_version_id=str(active_ver) if active_ver else None,
                    source=str(provenance.get("source") or source or "local"),
                )
            )
            if sid not in subjects:
                subjects[sid] = {
                    "id": sid,
                    "code": sid if len(sid) <= 12 else None,
                    "title": str(m.get("course_title") or sid),
                    "source": str(provenance.get("source") or source or "local"),
                }

    return subjects, docs_by_subject


class CatalogService:
    def __init__(self, db: Session, clock: Clock, p1_engine=None) -> None:
        self.db = db
        self.clock = clock
        self.p1_engine = p1_engine

    def _collect_subjects(self, user_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, list[SubjectDocumentOut]]]:
        subjects, docs_by_subject = _read_p1_subjects_and_docs(self.p1_engine)

        def register(sid: str | None, title: str | None = None, source: str = "workspace") -> None:
            if not sid:
                return
            key = sid.strip()
            if not key:
                return
            if key not in subjects:
                subjects[key] = {"id": key, "code": key if len(key) <= 12 else None, "title": title or key, "source": source}
            elif subjects[key]["title"] == key and title:
                subjects[key]["title"] = title

        for ev in self.db.scalars(select(Event).where(Event.user_id == user_id, Event.subject_id.is_not(None))):
            register(ev.subject_id, source=ev.source or "agenda")
        for t in self.db.scalars(select(Task).where(Task.user_id == user_id, Task.subject_id.is_not(None))):
            register(t.subject_id, source="tasks")
        for s in self.db.scalars(select(StudySession).where(StudySession.user_id == user_id, StudySession.subject_id.is_not(None))):
            register(s.subject_id, source="planner")
        for a in self.db.scalars(select(Assessment).where(Assessment.user_id == user_id, Assessment.subject_id.is_not(None))):
            register(a.subject_id, source="agenda")
        for m in self.db.scalars(select(Mastery).where(Mastery.user_id == user_id, Mastery.subject_id.is_not(None))):
            register(m.subject_id, source="learning")
        for q in self.db.scalars(select(Quiz).where(Quiz.user_id == user_id, Quiz.subject_id.is_not(None))):
            register(q.subject_id, source="learning")
        for d in self.db.scalars(select(Deck).where(Deck.user_id == user_id, Deck.subject_id.is_not(None))):
            register(d.subject_id, source="learning")
        for nb in self.db.scalars(select(Notebook).where(Notebook.user_id == user_id, Notebook.subject_id.is_not(None))):
            register(nb.subject_id, nb.title, source="notes")
        for node in self.db.scalars(
            select(AcademicNode)
            .join(AcademicProgram, AcademicProgram.id == AcademicNode.program_id)
            .where(AcademicProgram.user_id == user_id, AcademicNode.kind == "subject")
        ):
            register(node.subject_id or node.key, node.title, source="grades")

        return subjects, docs_by_subject

    def list_subjects(self, user_id: str) -> SubjectListOut:
        now = self.clock.now()
        subjects, docs_by_subject = self._collect_subjects(user_id)

        assessments_by_sub: dict[str, list[datetime]] = defaultdict(list)
        for a in self.db.scalars(
            select(Assessment).where(
                Assessment.user_id == user_id,
                Assessment.status == "active",
                Assessment.subject_id.is_not(None),
            )
        ):
            assessments_by_sub[a.subject_id].append(a.deadline_at)

        mastery_by_sub: dict[str, list[float]] = defaultdict(list)
        for m in self.db.scalars(
            select(Mastery).where(
                Mastery.user_id == user_id,
                Mastery.subject_id.is_not(None),
                Mastery.score.is_not(None),
            )
        ):
            mastery_by_sub[m.subject_id].append(float(m.score))

        items: list[SubjectSummaryOut] = []
        for sid, info in sorted(subjects.items(), key=lambda kv: (kv[1]["title"].lower(), kv[0])):
            deadlines = sorted(d for d in assessments_by_sub.get(sid, []) if d > now)
            scores = mastery_by_sub.get(sid, [])
            items.append(
                SubjectSummaryOut(
                    id=sid,
                    code=info["code"],
                    title=info["title"],
                    source=info["source"],
                    document_count=len(docs_by_subject.get(sid, [])),
                    assessment_count=len(assessments_by_sub.get(sid, [])),
                    mastery_mean=round(sum(scores) / len(scores), 4) if scores else None,
                    next_deadline_at=deadlines[0] if deadlines else None,
                )
            )
        return SubjectListOut(items=items)

    def get_subject(self, user_id: str, subject_id: str) -> SubjectDetailOut:
        now = self.clock.now()
        subjects, docs_by_subject = self._collect_subjects(user_id)
        info = subjects.get(subject_id)
        if info is None:
            raise NotFound("Subject not found")

        assessments = [
            SubjectAssessmentOut(
                id=a.id,
                title=ev.title if ev else "Évaluation",
                deadline_at=a.deadline_at,
                scope_status=a.scope_status,
            )
            for a, ev in self.db.execute(
                select(Assessment, Event)
                .join(Event, Event.id == Assessment.event_id, isouter=True)
                .where(Assessment.user_id == user_id, Assessment.subject_id == subject_id, Assessment.status == "active")
                .order_by(Assessment.deadline_at, Assessment.id)
            )
        ]

        concept_rows = list(
            self.db.scalars(
                select(Mastery)
                .where(Mastery.user_id == user_id, Mastery.subject_id == subject_id)
                .order_by(Mastery.concept_id, Mastery.dimension)
            )
        )
        by_concept: dict[str, list[Mastery]] = defaultdict(list)
        for row in concept_rows:
            by_concept[row.concept_id].append(row)
        concepts: list[SubjectConceptOut] = []
        for cid, rows in sorted(by_concept.items()):
            known = [r.score for r in rows if r.score is not None]
            avg = round(sum(known) / len(known), 4) if known else None
            due_dates = [r.next_due_at for r in rows if r.next_due_at is not None]
            next_due = min(due_dates) if due_dates else None
            status = ldomain.mastery_status(avg, max(r.n_effective for r in rows), next_due, now)
            concepts.append(SubjectConceptOut(concept_id=cid, score=avg, status=status, next_due_at=next_due))

        quizzes: list[SubjectQuizOut] = []
        for q in self.db.scalars(select(Quiz).where(Quiz.user_id == user_id, Quiz.subject_id == subject_id).order_by(Quiz.title)):
            qcount = self.db.scalar(select(func.count()).select_from(Question).where(Question.quiz_id == q.id)) or 0
            quizzes.append(SubjectQuizOut(id=q.id, title=q.title, question_count=qcount))

        decks: list[SubjectDeckOut] = []
        for d in self.db.scalars(select(Deck).where(Deck.user_id == user_id, Deck.subject_id == subject_id).order_by(Deck.title)):
            due = (
                self.db.scalar(
                    select(func.count())
                    .select_from(Flashcard)
                    .where(
                        Flashcard.deck_id == d.id,
                        (Flashcard.next_due_at.is_(None)) | (Flashcard.next_due_at <= now),
                    )
                )
                or 0
            )
            decks.append(SubjectDeckOut(id=d.id, title=d.title, due_count=due))

        nb_ids = list(
            self.db.scalars(select(Notebook.id).where(Notebook.user_id == user_id, Notebook.subject_id == subject_id))
        )
        notes = (
            [
                SubjectNoteOut(id=n.id, title=n.title, revision=n.revision)
                for n in self.db.scalars(
                    select(Note).where(Note.user_id == user_id, Note.notebook_id.in_(nb_ids)).order_by(Note.updated_at.desc())
                )
            ]
            if nb_ids
            else []
        )

        return SubjectDetailOut(
            id=info["id"],
            code=info["code"],
            title=info["title"],
            documents=docs_by_subject.get(subject_id, []),
            assessments=assessments,
            concepts=concepts,
            quizzes=quizzes,
            decks=decks,
            notes=notes,
            artifacts=[],
        )
