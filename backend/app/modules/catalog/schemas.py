"""DTOs for unified /api/v1/subjects endpoints (contracts/API.md)."""

from __future__ import annotations

from pydantic import BaseModel

from app.core.common import UtcDatetime


class SubjectSummaryOut(BaseModel):
    id: str
    code: str | None = None
    title: str
    source: str
    document_count: int = 0
    assessment_count: int = 0
    mastery_mean: float | None = None
    next_deadline_at: UtcDatetime | None = None


class SubjectListOut(BaseModel):
    items: list[SubjectSummaryOut]


class SubjectDocumentOut(BaseModel):
    id: str
    title: str
    resource_type: str = "other"
    active_version_id: str | None = None
    source: str = "local"


class SubjectAssessmentOut(BaseModel):
    id: str
    title: str
    deadline_at: UtcDatetime
    scope_status: str


class SubjectConceptOut(BaseModel):
    concept_id: str
    score: float | None = None
    status: str
    next_due_at: UtcDatetime | None = None


class SubjectQuizOut(BaseModel):
    id: str
    title: str
    question_count: int


class SubjectDeckOut(BaseModel):
    id: str
    title: str
    due_count: int


class SubjectNoteOut(BaseModel):
    id: str
    title: str
    revision: int


class SubjectArtifactOut(BaseModel):
    id: str
    type: str
    title: str
    stale: bool = False


class SubjectDetailOut(BaseModel):
    id: str
    code: str | None = None
    title: str
    documents: list[SubjectDocumentOut]
    assessments: list[SubjectAssessmentOut]
    concepts: list[SubjectConceptOut]
    quizzes: list[SubjectQuizOut]
    decks: list[SubjectDeckOut]
    notes: list[SubjectNoteOut]
    artifacts: list[SubjectArtifactOut]
