"""Read-only subject aggregation over P1 source catalog and P3 domain tables."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import Clock
from app.core.errors import NotFound
from app.modules.account.models import Profile
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


def _read_p1_subjects_and_docs(
    p1_engine, academic_year: str | None,
) -> tuple[dict[str, dict[str, Any]], dict[str, list[SubjectDocumentOut]], dict[str, str]]:
    if p1_engine is None:
        return {}, {}, {}
    from backend.app.core.database import LOCAL_USER, connections, documents, latest_consent_granted, records

    subjects: dict[str, dict[str, Any]] = {}
    docs_by_subject: dict[str, list[SubjectDocumentOut]] = defaultdict(list)
    known_aliases: dict[str, set[str]] = defaultdict(set)
    consent = (
        connections.c.user_id == LOCAL_USER,
        latest_consent_granted(connections.c.id, "read").is_(True),
    )

    with p1_engine.connect() as conn:
        rows = conn.execute(
            select(connections.c.source, records.c.external_id, records.c.payload)
            .join(connections, records.c.connection_id == connections.c.id)
            .where(records.c.kind == "subject", *consent)
            .order_by((connections.c.source != "pamplemousse"), records.c.external_id)
        )
        for source, external_id, payload in rows:
            data = payload or {}
            if academic_year and source == "pamplemousse" and (data.get("provenance") or {}).get("academic_year") != academic_year:
                continue
            code = data.get("code") or data.get("source_code")
            sid = external_id.strip()
            if not sid:
                continue
            explicit = [key for key in (data.get("aliases") or []) if key in subjects]
            if source == "moodle" and len(explicit) == 1:
                known_aliases[sid].add(explicit[0])
                continue
            subjects[sid] = {
                "id": sid,
                "code": code,
                "title": str(data.get("title") or sid),
                "source": str(source),
            }
            if code:
                known_aliases[code.strip().upper()].add(sid)

        aliases = {alias: next(iter(ids)) for alias, ids in known_aliases.items() if len(ids) == 1}
        provenance = documents.c.metadata_json["provenance"]
        public = (
            func.coalesce(documents.c.metadata_json["access_status"].as_string(), "active") == "active",
            documents.c.active_version_id.is_not(None),
            (connections.c.source != "ensora_drive")
            | (func.ensora_drive_folder_allowed(provenance["folder"].as_string()) == 1),
        )
        for doc_id, active_ver, meta, source in conn.execute(
            select(documents.c.id, documents.c.active_version_id, documents.c.metadata_json, connections.c.source)
            .join(connections, documents.c.connection_id == connections.c.id)
            .where(*consent, *public)
        ):
            data = meta or {}
            match = data.get("match") or {}
            override = data.get("user_override") or {}
            course_id = data.get("course_id")
            course_subject = aliases.get(f"moodle:course:{course_id}") if source == "moodle" and course_id else None
            raw_subject = (
                override.get("subject_id")
                or course_subject
                or (match.get("subject_id") if match.get("confirmed") else None)
                or data.get("subject_id")
            )
            if not raw_subject:
                continue
            raw_subject = str(raw_subject).strip()
            sid = aliases.get(raw_subject, aliases.get(raw_subject.upper(), raw_subject))
            if academic_year and source == "moodle" and sid not in subjects:
                continue
            classification = data.get("classification") or {}
            docs_by_subject[sid].append(
                SubjectDocumentOut(
                    id=str(doc_id),
                    title=str(data.get("title") or "Document"),
                    resource_type=str(classification.get("resource_type") or "other"),
                    active_version_id=str(active_ver),
                    source=str(source),
                )
            )
            if sid not in subjects:
                subjects[sid] = {
                    "id": sid,
                    "code": sid if len(sid) <= 12 else None,
                    "title": str(data.get("course_title") or sid),
                    "source": str(source),
                }

    return subjects, docs_by_subject, aliases


class CatalogService:
    def __init__(self, db: Session, clock: Clock, p1_engine=None) -> None:
        self.db = db
        self.clock = clock
        self.p1_engine = p1_engine

    def _collect_subjects(
        self, user_id: str,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[SubjectDocumentOut]], dict[str, str]]:
        year = self.db.scalar(select(Profile.academic_year).where(Profile.user_id == user_id))
        subjects, docs_by_subject, aliases = _read_p1_subjects_and_docs(self.p1_engine, year)

        def register(sid: str | None, title: str | None = None, source: str = "workspace") -> None:
            if not sid:
                return
            key = sid.strip()
            key = aliases.get(key, aliases.get(key.upper(), key))
            if not key:
                return
            if key not in subjects:
                subjects[key] = {"id": key, "code": key if len(key) <= 12 else None, "title": title or key, "source": source}
            elif subjects[key]["title"] == key and title:
                subjects[key]["title"] = title

        for ev in self.db.scalars(select(Event).where(Event.user_id == user_id, Event.subject_id.is_not(None))):
            if ev.source in {"pamplemousse", "moodle"} and ev.subject_id not in subjects and (ev.subject_id or "").upper() not in aliases:
                continue
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
            .where(AcademicProgram.user_id == user_id, AcademicProgram.provenance != "pamplemousse", AcademicNode.kind == "subject")
        ):
            register(node.subject_id or node.key, node.title, source="grades")

        return subjects, docs_by_subject, aliases

    def list_subjects(self, user_id: str) -> SubjectListOut:
        now = self.clock.now()
        subjects, docs_by_subject, aliases = self._collect_subjects(user_id)

        assessments_by_sub: dict[str, list[datetime]] = defaultdict(list)
        for a in self.db.scalars(
            select(Assessment).where(
                Assessment.user_id == user_id,
                Assessment.status == "active",
                Assessment.subject_id.is_not(None),
                Assessment.deadline_at.is_not(None),
            )
        ):
            assessments_by_sub[aliases.get(a.subject_id, aliases.get(a.subject_id.upper(), a.subject_id))].append(a.deadline_at)

        mastery_by_sub: dict[str, list[float]] = defaultdict(list)
        for m in self.db.scalars(
            select(Mastery).where(
                Mastery.user_id == user_id,
                Mastery.subject_id.is_not(None),
                Mastery.score.is_not(None),
            )
        ):
            mastery_by_sub[aliases.get(m.subject_id, aliases.get(m.subject_id.upper(), m.subject_id))].append(float(m.score))

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
        subjects, docs_by_subject, aliases = self._collect_subjects(user_id)
        subject_id = aliases.get(subject_id, aliases.get(subject_id.upper(), subject_id))
        identifiers = [subject_id, *(alias for alias, canonical in aliases.items() if canonical == subject_id)]
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
                .where(Assessment.user_id == user_id, Assessment.subject_id.in_(identifiers),
                       Assessment.status == "active", Assessment.deadline_at.is_not(None))
                .order_by(Assessment.deadline_at, Assessment.id)
            )
        ]

        concept_rows = list(
            self.db.scalars(
                select(Mastery)
                .where(Mastery.user_id == user_id, Mastery.subject_id.in_(identifiers))
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
        for q in self.db.scalars(select(Quiz).where(Quiz.user_id == user_id, Quiz.subject_id.in_(identifiers)).order_by(Quiz.title)):
            qcount = self.db.scalar(select(func.count()).select_from(Question).where(Question.quiz_id == q.id)) or 0
            quizzes.append(SubjectQuizOut(id=q.id, title=q.title, question_count=qcount))

        decks: list[SubjectDeckOut] = []
        for d in self.db.scalars(select(Deck).where(Deck.user_id == user_id, Deck.subject_id.in_(identifiers)).order_by(Deck.title)):
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
            self.db.scalars(select(Notebook.id).where(Notebook.user_id == user_id, Notebook.subject_id.in_(identifiers)))
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
