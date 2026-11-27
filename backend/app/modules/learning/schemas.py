"""Learning DTOs. Active-attempt payloads never contain answer keys or explanations."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.common import UtcDatetime

Dimension = Literal["theory", "memory", "reasoning", "application", "implementation", "autonomy", "retention"]
Rating = Literal["again", "hard", "good", "easy"]
QuestionType = Literal["single_choice", "multiple_choice", "true_false", "fill_blank", "numeric", "open"]
Difficulty = Literal["easy", "medium", "hard"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- decks & cards ---------------------------------------------------------------------
class DeckCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None


class DeckOut(BaseModel):
    id: str
    title: str
    subject_id: str | None
    card_count: int
    due_count: int
    revision: int


class CardCreate(_Strict):
    front: str = Field(min_length=1, max_length=5000)
    back: str = Field(min_length=1, max_length=5000)
    concept_id: str | None = None


class CardPatch(_Strict):
    expected_revision: int | None = None
    front: str | None = Field(default=None, min_length=1, max_length=5000)
    back: str | None = Field(default=None, min_length=1, max_length=5000)
    concept_id: str | None = None


class CardOut(BaseModel):
    id: str
    deck_id: str
    concept_id: str | None
    front: str
    back: str
    interval_days: int
    due_at: UtcDatetime | None
    review_count: int
    last_rating: Rating | None
    is_due: bool
    revision: int


class ReviewRequest(_Strict):
    rating: Rating
    expected_revision: int | None = None


class ReviewResult(BaseModel):
    card_id: str
    review_id: str
    rating: Rating
    interval_days: int
    next_due_at: UtcDatetime
    mastery_updates: list[MasteryOut]
    replayed: bool = False
    algorithm: str = "interval-heuristic-v1"
    baseline_interval_days: int | None = None
    retrievability_before: float | None = None


# --- quizzes ---------------------------------------------------------------------------
class OptionIn(_Strict):
    id: str = Field(min_length=1, max_length=32)
    text: str = Field(min_length=1, max_length=2000)


class QuestionIn(_Strict):
    type: QuestionType
    prompt: str = Field(min_length=1, max_length=10000)
    options: list[OptionIn] = Field(default_factory=list, max_length=20)
    answer: Any = Field(
        default=None,
        description="single_choice: option id; multiple_choice: [ids]; true_false: bool; "
        "fill_blank: [accepted strings]; numeric: {value, tolerance}; open: rubric text",
    )
    explanation: str | None = Field(default=None, max_length=10000)
    points: float = Field(default=1.0, gt=0, le=100)
    concept_ids: list[str] = Field(default_factory=list, max_length=20)
    dimension: Dimension = "theory"
    difficulty: Difficulty | None = None
    partial_credit: bool = Field(default=False, description="multiple_choice only, explicit rubric choice")


class QuizCreate(_Strict):
    title: str = Field(min_length=1, max_length=200)
    subject_id: str | None = None
    type: Literal["practice", "diagnostic", "exam"] = "practice"
    time_limit_min: int | None = Field(default=None, ge=1, le=600)
    origin: Literal["manual", "historical_exam"] = Field(
        default="manual", description="historical_exam = a past paper; used for exam-pattern weighting")
    questions: list[QuestionIn] = Field(min_length=1, max_length=200)


class QuestionPublic(BaseModel):
    id: str
    ordinal: int
    type: QuestionType
    prompt: str
    options: list[OptionIn]
    points: float
    concept_ids: list[str]
    dimension: Dimension


class QuizOut(BaseModel):
    id: str
    title: str
    subject_id: str | None
    type: str
    time_limit_min: int | None
    question_count: int
    total_points: float
    questions: list[QuestionPublic]
    revision: int


class AttemptCreate(_Strict):
    mode: Literal["practice", "exam"] = "practice"
    session_id: str | None = None


class ResponseIn(_Strict):
    question_id: str
    response: Any = None
    assisted: bool = False


class ResponsesPatch(_Strict):
    responses: list[ResponseIn] = Field(min_length=1, max_length=200)


class AttemptOut(BaseModel):
    """Active payload: question IDs, prompts, options, points, expiry and status only."""

    id: str
    quiz_id: str
    mode: str
    status: Literal["active", "submitted", "expired"]
    started_at: UtcDatetime
    expires_at: UtcDatetime | None
    questions: list[QuestionPublic]
    responses: dict[str, Any]


class ItemResult(BaseModel):
    question_id: str
    response: Any
    correct_answer: Any
    explanation: str | None
    score: float | None
    points: float
    earned: float | None
    assisted: bool
    status: Literal["graded", "pending_review"]
    grader: str | None = None
    feedback: str | None = None


class AttemptResult(BaseModel):
    attempt_id: str
    status: Literal["submitted", "expired"]
    score: float
    max_score: float
    ratio: float | None
    pending_review: int
    items: list[ItemResult]
    mastery_updates: list[MasteryOut]
    recommendations: list[dict] = []
    errors_logged: int = 0


class ItemGrade(_Strict):
    score: float = Field(ge=0, le=1)
    grader: Literal["self", "teacher", "ai_provisional"] = "self"
    feedback: str | None = Field(default=None, max_length=10000)


class LogErrorsRequest(_Strict):
    question_ids: list[str] | None = None


class ExamModeOut(BaseModel):
    active: bool
    attempt_id: str | None = None
    quiz_id: str | None = None
    expires_at: UtcDatetime | None = None
    blocked_features: list[str] = []


class MockExamRequest(_Strict):
    subject_id: str
    title: str | None = Field(default=None, max_length=200)
    question_count: int = Field(default=10, ge=1, le=100)
    time_limit_min: int = Field(default=60, ge=5, le=600)
    concept_ids: list[str] | None = Field(default=None, max_length=100, description="current syllabus scope")
    difficulty: Difficulty | None = None


class PrerequisiteEdge(_Strict):
    prerequisite_id: str = Field(min_length=1, max_length=36)
    dependent_id: str = Field(min_length=1, max_length=36)


class PrerequisitesPut(_Strict):
    edges: list[PrerequisiteEdge] = Field(max_length=2000)


class CalibrationOut(BaseModel):
    samples: int
    brier: float | None
    base_rate: float | None
    brier_skill: float | None
    bins: list[dict]
    sufficient: bool
    by_kind: dict[str, dict]
    notes: list[str]


# --- mastery & study needs --------------------------------------------------------------
class MasteryOut(BaseModel):
    concept_id: str
    subject_id: str | None
    dimension: Dimension
    score: float | None
    status: Literal["unknown", "learning", "weak", "acquired", "consolidate"]
    confidence: float
    evidence_count: int
    interval_days: int
    last_review_at: UtcDatetime | None
    next_due_at: UtcDatetime | None
    algorithm_version: str


class StudyNeedOut(BaseModel):
    need_id: str
    kind: Literal["review", "practice", "flashcards"]
    activity_type: str
    subject_id: str | None
    concept_ids: list[str]
    dimension: Dimension | None
    estimated_min: int
    earliest_at: UtcDatetime | None
    due_at: UtcDatetime | None
    priority_factors: dict[str, float]
    reason: str
    success_criteria: str


ReviewResult.model_rebuild()
AttemptResult.model_rebuild()
