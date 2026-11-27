"""Workspace DTOs: Focus Room, Smart Notes, Error Notebook."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.common import UtcDatetime

FocusMode = Literal["classic", "long", "deep", "custom", "free"]
ErrorCategory = Literal[
    "calculation", "formula_recall", "comprehension", "reasoning", "programming", "method", "careless",
    "unclassified", "other",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- focus ----------------------------------------------------------------------------
class FocusStart(_Strict):
    mode: FocusMode = "classic"
    study_session_id: str | None = None
    task_id: str | None = None
    goal: str | None = Field(default=None, max_length=1000)
    work_min: int | None = Field(default=None, ge=5, le=240)
    break_min: int | None = Field(default=None, ge=1, le=60)


class FocusTransition(_Strict):
    action: Literal["pause", "resume", "break", "work", "extend"]
    minutes: int | None = Field(default=None, ge=1, le=120, description="for extend")


class FocusReflection(_Strict):
    objective_met: bool | None = None
    difficulty: int | None = Field(default=None, ge=1, le=5)
    active_recall_done: bool | None = None
    notes: str | None = Field(default=None, max_length=5000)


class FocusFinish(_Strict):
    outcome: Literal["completed", "partial", "abandoned"] = "completed"
    remaining_min: int | None = Field(default=None, ge=0, le=100_000)
    reflection: FocusReflection | None = None


class IntervalOut(BaseModel):
    kind: Literal["work", "break"]
    started_at: UtcDatetime
    ended_at: UtcDatetime | None


class FocusOut(BaseModel):
    id: str
    mode: FocusMode
    status: Literal["working", "paused", "break", "finished"]
    goal: str | None
    work_min: int | None
    break_min: int | None
    study_session_id: str | None
    task_id: str | None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None
    active_min: int
    break_total_min: int
    phase_started_at: UtcDatetime | None
    phase_remaining_min: int | None
    intervals: list[IntervalOut]
    reflection: dict | None
    revision: int


# --- notes ----------------------------------------------------------------------------
class Citation(_Strict):
    document_id: str
    version_id: str | None = None
    locator: dict | None = None
    excerpt: str | None = Field(default=None, max_length=5000)


class NotebookCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None


class NotebookOut(BaseModel):
    id: str
    title: str
    subject_id: str | None
    note_count: int
    revision: int


class NoteCreate(_Strict):
    notebook_id: str
    title: str = Field(min_length=1, max_length=200)
    markdown: str = Field(default="", max_length=200_000)
    concept_id: str | None = None
    chapter_id: str | None = None
    citations: list[Citation] = Field(default_factory=list, max_length=200)
    origin: Literal["manual", "tutor", "studio"] = "manual"


class NotePatch(_Strict):
    expected_revision: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    markdown: str | None = Field(default=None, max_length=200_000)
    concept_id: str | None = None
    chapter_id: str | None = None
    citations: list[Citation] | None = Field(default=None, max_length=200)


class NoteOut(BaseModel):
    id: str
    notebook_id: str
    title: str
    markdown: str
    concept_id: str | None
    chapter_id: str | None
    citations: list[dict]
    citations_resolved: bool = False  # catalog (C1) will resolve exact document versions
    origin: str
    revision: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


class NoteVersionOut(BaseModel):
    revision: int
    title: str
    markdown: str
    citations: list[dict]
    created_at: UtcDatetime


# --- errors ---------------------------------------------------------------------------
class ErrorCreate(_Strict):
    concept_id: str | None = None
    subject_id: str | None = None
    category: ErrorCategory = "unclassified"
    prompt_excerpt: str | None = Field(default=None, max_length=5000)
    reasoning: str | None = Field(default=None, max_length=5000)
    remediation: str | None = Field(default=None, max_length=5000)


class ErrorPatch(_Strict):
    expected_revision: int | None = None
    category: ErrorCategory | None = None
    reasoning: str | None = Field(default=None, max_length=5000)
    remediation: str | None = Field(default=None, max_length=5000)
    resolved: bool | None = None


class ErrorOut(BaseModel):
    id: str
    concept_id: str | None
    subject_id: str | None
    source: str
    source_ref: str | None
    category: ErrorCategory
    prompt_excerpt: str | None
    reasoning: str | None
    remediation: str | None
    occurrences: int
    recurring: bool
    last_seen_at: UtcDatetime
    resolved_at: UtcDatetime | None
    revision: int
