"""Agenda DTOs: events, assessments, tasks and availability snapshot."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.core.common import UtcDatetime
from app.modules.planner.domain import MAX_EVENT_DURATION

EventType = Literal["class", "td", "tp", "exam", "presentation", "deadline", "personal", "other"]
EventStatus = Literal["confirmed", "tentative", "cancelled"]
EventSource = Literal["manual", "fixture", "ics", "pamplemousse", "moodle", "google_calendar", "import"]
AssessmentFormat = Literal["written", "oral", "practical", "project", "online", "mixed", "other"]
Difficulty = Literal["easy", "medium", "hard"]
ScopeStatus = Literal["unconfirmed", "confirmed"]
TaskStatus = Literal["todo", "in_progress", "done", "cancelled"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def check_timing(start_at, end_at, all_day_date) -> None:
    timed = start_at is not None or end_at is not None
    if timed and all_day_date is not None:
        raise ValueError("use either start_at/end_at or all_day_date, not both")
    if not timed and all_day_date is None:
        raise ValueError("start_at/end_at or all_day_date is required")
    if timed:
        if start_at is None or end_at is None:
            raise ValueError("start_at and end_at must both be set")
        if end_at <= start_at:
            raise ValueError("end_at must be after start_at")
        if end_at - start_at > MAX_EVENT_DURATION:
            raise ValueError("an event cannot last more than 14 days")


# --- events ---------------------------------------------------------------------------
class EventCreate(_Strict):
    type: EventType
    title: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    all_day_date: date | None = None
    room: str | None = Field(default=None, max_length=120)
    teacher: str | None = Field(default=None, max_length=120)
    group: str | None = Field(default=None, max_length=64)
    busy: bool | None = Field(default=None, description="Defaults to false for deadlines")
    status: Literal["confirmed", "tentative"] = "confirmed"
    recurrence_key: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=5000)

    @model_validator(mode="after")
    def _timing(self) -> EventCreate:
        check_timing(self.start_at, self.end_at, self.all_day_date)
        return self


class EventPatch(_Strict):
    expected_revision: int | None = None
    type: EventType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    subject_id: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    all_day_date: date | None = None
    room: str | None = Field(default=None, max_length=120)
    teacher: str | None = Field(default=None, max_length=120)
    group: str | None = Field(default=None, max_length=64)
    busy: bool | None = None
    status: EventStatus | None = None
    notes: str | None = Field(default=None, max_length=5000)


class EventConfirm(_Strict):
    decision: Literal["confirm", "reject"]
    expected_revision: int | None = None


class EventImportItem(_Strict):
    """Normalized item produced by an authorized connector or a synthetic fixture."""

    external_id: str = Field(min_length=1, max_length=255)
    type: EventType
    title: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    all_day_date: date | None = None
    room: str | None = None
    teacher: str | None = None
    group: str | None = None
    busy: bool | None = None
    status: EventStatus | None = None
    confirmation_required: bool = False
    recurrence_key: str | None = None

    @model_validator(mode="after")
    def _timing(self) -> EventImportItem:
        check_timing(self.start_at, self.end_at, self.all_day_date)
        return self


class ImportResult(BaseModel):
    created: int
    updated: int
    unchanged: int
    agenda_revision: int


class EventOut(BaseModel):
    id: str
    type: EventType
    title: str
    subject_id: str | None
    start_at: UtcDatetime | None
    end_at: UtcDatetime | None
    all_day_date: date | None
    room: str | None
    teacher: str | None
    group: str | None
    status: EventStatus
    busy: bool
    source: EventSource
    external_id: str | None
    read_only: bool
    confirmation_required: bool
    recurrence_key: str | None
    notes: str | None
    assessment_id: str | None
    origin: Literal["agenda"] = "agenda"
    revision: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


# --- assessments ----------------------------------------------------------------------
class AssessmentOut(BaseModel):
    id: str
    event_id: str | None
    title: str | None
    type: EventType | None
    subject_id: str | None
    deadline_at: UtcDatetime | None
    format: AssessmentFormat | None
    duration_min: int | None
    difficulty: Difficulty | None
    scope_status: ScopeStatus
    scope_revision: int
    concept_ids: list[str]
    status: Literal["active", "cancelled"]
    confirmation_required: bool
    revision: int


class AssessmentPatch(_Strict):
    expected_revision: int | None = None
    subject_id: str | None = None
    format: AssessmentFormat | None = None
    duration_min: int | None = Field(default=None, ge=1, le=24 * 60)
    difficulty: Difficulty | None = None
    scope_status: ScopeStatus | None = None
    concept_ids: list[str] | None = Field(default=None, max_length=500)


# --- tasks ----------------------------------------------------------------------------
class TaskCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    subject_id: str | None = None
    parent_task_id: str | None = None
    deadline_at: AwareDatetime | None = None
    estimated_min: int | None = Field(default=None, ge=1, le=100_000)
    remaining_min: int | None = Field(default=None, ge=0, le=100_000)
    priority: int = Field(default=3, ge=1, le=5)
    locked: bool = False
    splittable: bool = True


class TaskPatch(_Strict):
    expected_revision: int | None = None
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    subject_id: str | None = None
    parent_task_id: str | None = None
    deadline_at: AwareDatetime | None = None
    estimated_min: int | None = Field(default=None, ge=1, le=100_000)
    remaining_min: int | None = Field(default=None, ge=0, le=100_000)
    priority: int | None = Field(default=None, ge=1, le=5)
    status: TaskStatus | None = None
    locked: bool | None = None
    splittable: bool | None = None


class DecomposeRequest(_Strict):
    text: str = Field(min_length=1, max_length=50_000)
    total_estimated_min: int | None = Field(default=None, ge=5, le=100_000)


class ProposedStep(BaseModel):
    title: str
    detail: str
    estimated_min: int
    deliverable: bool


class DecomposeOut(BaseModel):
    method: str
    ai_used: bool = False
    steps: list[ProposedStep]
    notes: list[str]


class SubtasksCreate(_Strict):
    items: list[TaskCreate] = Field(min_length=1, max_length=100)


class TaskOut(BaseModel):
    id: str
    title: str
    description: str | None
    subject_id: str | None
    parent_task_id: str | None
    deadline_at: UtcDatetime | None
    estimated_min: int | None
    remaining_min: int | None
    priority: int
    status: TaskStatus
    locked: bool
    splittable: bool
    origin: Literal["manual", "imported", "ai"]
    overdue: bool
    revision: int
    created_at: UtcDatetime
    updated_at: UtcDatetime


# --- availability ---------------------------------------------------------------------
class IntervalOut(BaseModel):
    start_at: UtcDatetime
    end_at: UtcDatetime
    minutes: int


class BusyOut(IntervalOut):
    event_id: str


class EventConflictOut(IntervalOut):
    event_id_a: str
    event_id_b: str


class DayOut(BaseModel):
    date: date
    free_min: int
    daily_max_min: int
    plannable_min: int


class AvailabilityOut(BaseModel):
    input_revision: str
    timezone: str
    start_at: UtcDatetime
    end_at: UtcDatetime
    free: list[IntervalOut]
    busy: list[BusyOut]
    conflicts: list[EventConflictOut] = []
    days: list[DayOut]
    total_free_min: int
    weekly_max_min: int
    min_session_min: int
    max_session_min: int
    pending_confirmations: int
    warnings: list[str]


# --- planner: previews, plans, sessions ---------------------------------------------------
SessionStatus = Literal["planned", "in_progress", "completed", "partial", "skipped", "cancelled"]


PlannerAlgorithmChoice = Literal["auto", "greedy", "cpsat"]


class PreviewRequest(_Strict):
    horizon_days: int = Field(default=7, ge=1, le=31)
    algorithm: PlannerAlgorithmChoice = "auto"


class ReplanRequest(_Strict):
    base_plan_revision: int
    reason: Literal[
        "manual", "assessment_changed", "agenda_changed", "preferences_changed", "missed_session",
        "partial_session", "new_task", "mastery_changed", "grades_changed",
    ] = "manual"
    horizon_days: int = Field(default=7, ge=1, le=31)
    algorithm: PlannerAlgorithmChoice = "auto"


class AcceptRequest(_Strict):
    preview_id: str
    input_revision: str


class PreviewSessionOut(BaseModel):
    id: str
    need_key: str
    kind: str
    title: str
    task_id: str | None
    assessment_id: str | None
    subject_id: str | None
    concept_ids: list[str]
    activity_type: str
    start_at: UtcDatetime
    end_at: UtcDatetime
    estimated_min: int
    priority: float
    reason: str
    objective: str
    success_criteria: str
    resource_ids: list[str] = []
    generation_spec: dict | None
    locked: bool = False


class SessionOut(PreviewSessionOut):
    plan_id: str
    deadline_at: UtcDatetime | None
    actual_min: int | None
    status: SessionStatus
    started_at: UtcDatetime | None
    finished_at: UtcDatetime | None
    reflection: dict | None
    revision: int


class UnscheduledOut(BaseModel):
    need_id: str
    title: str
    remaining_min: int
    reason: str
    explanation: str = ""


class DiffMove(BaseModel):
    session_id: str
    need_key: str
    from_start: UtcDatetime
    from_end: UtcDatetime
    to_start: UtcDatetime
    to_end: UtcDatetime


class DiffItem(BaseModel):
    session_id: str | None
    need_key: str
    start_at: UtcDatetime
    end_at: UtcDatetime


class PlanDiffOut(BaseModel):
    added: list[DiffItem]
    moved: list[DiffMove]
    removed: list[DiffItem]
    unchanged: int


class DayLoadOut(BaseModel):
    date: date
    planned_min: int
    daily_max_min: int


class PlanSummaryOut(BaseModel):
    scheduled_min: int
    unscheduled_min: int
    deficit_min: int = 0
    deferred_min: int
    kept_min: int
    per_day: list[DayLoadOut]


class PlanPreviewOut(BaseModel):
    preview_id: str
    input_revision: str
    algorithm_version: str
    solver_status: str = "GREEDY"
    horizon_start: UtcDatetime
    horizon_end: UtcDatetime
    sessions: list[PreviewSessionOut]
    kept_sessions: list[SessionOut]
    unscheduled: list[UnscheduledOut]
    conflicts: list[EventConflictOut] = []
    recommendations: list[str] = []
    warnings: list[str]
    summary: PlanSummaryOut
    diff: PlanDiffOut
    reason: str | None = None


class PlannerStatusOut(BaseModel):
    has_plan: bool
    plan_id: str | None = None
    plan_revision: int | None = None
    dirty: bool
    changed_inputs: list[str]
    suggested_reason: str | None
    accepted_input_revision: str | None = None
    current_input_revision: str | None = None


class PlanOut(BaseModel):
    id: str
    status: str
    horizon_start: UtcDatetime
    horizon_end: UtcDatetime
    input_revision: str
    accepted_at: UtcDatetime | None
    warnings: list[str]
    revision: int
    sessions: list[SessionOut]


class SessionPatch(_Strict):
    expected_revision: int | None = None
    start_at: AwareDatetime | None = None
    end_at: AwareDatetime | None = None
    locked: bool | None = None


class Reflection(_Strict):
    difficulty: int | None = Field(default=None, ge=1, le=5)
    objective_met: bool | None = None
    notes: str | None = Field(default=None, max_length=5000)


class SessionFinish(_Strict):
    outcome: Literal["completed", "partial", "skipped"]
    actual_min: int | None = Field(default=None, ge=0, le=24 * 60)
    remaining_min: int | None = Field(default=None, ge=0, le=100_000)
    reflection: Reflection | None = None
