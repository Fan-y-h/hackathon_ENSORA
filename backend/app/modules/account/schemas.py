"""Profile / preferences DTOs for ``GET/PATCH /api/v1/profile``."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.common import UtcDatetime
from app.modules.account.domain import (
    MINUTES_PER_DAY,
    MINUTES_PER_WEEK,
    is_valid_timezone,
    parse_hhmm,
)

Cohort = Literal["1A", "2A", "3A"]
StudentStatus = Literal["civil_servant", "engineer"]
Locale = Literal["fr", "en"]
StudyFormat = Literal[
    "revision_sheet",
    "flashcards",
    "quiz",
    "open_questions",
    "exercises",
    "past_exams",
    "podcast",
    "video",
    "animation",
    "infographic",
    "mind_map",
]
RepetitionAlgorithm = Literal["heuristic", "fsrs"]
PlannerAlgorithm = Literal["greedy", "cpsat"]
NotificationType = Literal[
    "new_assessment", "timetable_change", "deadline_soon", "task_overdue", "review_due",
    "session_soon", "plan_outdated", "grade_recorded", "academic_risk",
]
GoalKind = Literal["study_time", "subject", "exam_preparation", "habit", "custom"]

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$|^24:00$")
_ACADEMIC_YEAR = re.compile(r"^(\d{4})-(\d{4})$")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeWindow(_Strict):
    """Weekly local-time window, half-open ``[start, end)``; weekday 0 = Monday."""

    weekday: int = Field(ge=0, le=6)
    start: str = Field(examples=["09:00"])
    end: str = Field(examples=["12:00"])

    @field_validator("start", "end")
    @classmethod
    def _hhmm(cls, v: str) -> str:
        if not _HHMM.match(v):
            raise ValueError("expected HH:MM (00:00–24:00)")
        return v

    @model_validator(mode="after")
    def _ordered(self) -> TimeWindow:
        if self.start == "24:00":
            raise ValueError("start cannot be 24:00")
        if parse_hhmm(self.start) >= parse_hhmm(self.end):
            raise ValueError("start must be before end")
        return self


class GoalIn(_Strict):
    id: str | None = Field(default=None, description="Omit to create; keep to update")
    label: str = Field(min_length=1, max_length=200)
    kind: GoalKind = "custom"
    target_minutes_per_week: int | None = Field(default=None, ge=1, le=MINUTES_PER_WEEK)
    subject_id: str | None = None
    due_date: date | None = None
    active: bool = True


class Goal(GoalIn):
    id: str  # type: ignore[assignment]


class ProviderPolicy(_Strict):
    external_allowed: bool = False
    preferred_mode: Literal["mock", "local", "external"] = "mock"

    @model_validator(mode="after")
    def _consent(self) -> ProviderPolicy:
        if self.preferred_mode == "external" and not self.external_allowed:
            raise ValueError("external mode requires external_allowed=true (explicit consent)")
        return self


class NotificationSettingsPatch(_Strict):
    """Per-type toggles (true by default) and an optional email address for reminders."""

    new_assessment: bool | None = None
    timetable_change: bool | None = None
    deadline_soon: bool | None = None
    task_overdue: bool | None = None
    review_due: bool | None = None
    session_soon: bool | None = None
    plan_outdated: bool | None = None
    grade_recorded: bool | None = None
    academic_risk: bool | None = None
    email_address: str | None = Field(default=None, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class PreferencesOut(BaseModel):
    work_windows: list[TimeWindow]
    rest_windows: list[TimeWindow]
    daily_max_min: int
    weekly_max_min: int
    min_session_min: int
    max_session_min: int
    preferred_formats: list[StudyFormat]
    provider_policy: ProviderPolicy
    repetition_algorithm: RepetitionAlgorithm
    desired_retention: float
    planner_algorithm: PlannerAlgorithm
    notification_settings: dict[str, bool | str]
    revision: int


class PreferencesPatch(_Strict):
    work_windows: list[TimeWindow] | None = Field(default=None, max_length=70)
    rest_windows: list[TimeWindow] | None = Field(default=None, max_length=70)
    daily_max_min: int | None = Field(default=None, ge=1, le=MINUTES_PER_DAY)
    weekly_max_min: int | None = Field(default=None, ge=1, le=MINUTES_PER_WEEK)
    min_session_min: int | None = Field(default=None, ge=5, le=MINUTES_PER_DAY)
    max_session_min: int | None = Field(default=None, ge=5, le=MINUTES_PER_DAY)
    preferred_formats: list[StudyFormat] | None = None
    provider_policy: ProviderPolicy | None = None
    repetition_algorithm: RepetitionAlgorithm | None = None
    desired_retention: float | None = Field(default=None, ge=0.7, le=0.97)
    planner_algorithm: PlannerAlgorithm | None = None
    notification_settings: NotificationSettingsPatch | None = None


class ProfileOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    cohort: Cohort | None
    track: str | None
    student_status: StudentStatus | None
    academic_year: str | None
    locale: Locale
    timezone: str
    goals: list[Goal]
    self_assessment: dict[str, int]
    preferences: PreferencesOut
    onboarding_complete: bool
    revision: int
    updated_at: UtcDatetime


class ProfilePatch(_Strict):
    """Partial update. Omitted fields are unchanged; ``null`` clears nullable fields."""

    expected_revision: int | None = None
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    cohort: Cohort | None = None
    track: str | None = Field(default=None, max_length=120)
    student_status: StudentStatus | None = None
    academic_year: str | None = Field(default=None, examples=["2026-2027"])
    locale: Locale | None = None
    timezone: str | None = None
    goals: list[GoalIn] | None = Field(default=None, max_length=50)
    self_assessment: dict[str, Annotated[int, Field(ge=1, le=5)]] | None = Field(
        default=None, max_length=500, description="subject_id or concept_id → declared confidence 1–5")
    preferences: PreferencesPatch | None = None

    @field_validator("academic_year")
    @classmethod
    def _year(cls, v: str | None) -> str | None:
        if v is None:
            return v
        m = _ACADEMIC_YEAR.match(v)
        if not m or int(m.group(2)) != int(m.group(1)) + 1:
            raise ValueError("expected YYYY-YYYY with consecutive years")
        return v

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_timezone(v):
            raise ValueError("unknown IANA timezone")
        return v


class StudentStatusOptionOut(BaseModel):
    status: StudentStatus
    label: str
