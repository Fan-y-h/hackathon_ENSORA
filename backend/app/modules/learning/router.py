"""Learning endpoints: decks/cards/reviews, quizzes/attempts, mastery, study needs."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.exam_guard import require_not_exam_mode
from app.core.identity import get_current_user_id
from app.modules.learning.schemas import (
    AttemptCreate,
    AttemptOut,
    AttemptResult,
    CalibrationOut,
    CardCreate,
    CardOut,
    CardPatch,
    DeckCreate,
    DeckOut,
    ExamModeOut,
    ItemGrade,
    LogErrorsRequest,
    MasteryOut,
    MockExamRequest,
    PrerequisitesPut,
    QuizCreate,
    QuizOut,
    ResponsesPatch,
    ReviewRequest,
    ReviewResult,
    StudyNeedOut,
)
from app.modules.learning.service import LearningService

router = APIRouter(tags=["learning"])


def service(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> LearningService:
    return LearningService(db, clock)


User = Annotated[str, Depends(get_current_user_id)]
Svc = Annotated[LearningService, Depends(service)]


@router.get("/decks", response_model=list[DeckOut])
def list_decks(svc: Svc, user_id: User):
    return svc.list_decks(user_id)


@router.post("/decks", response_model=DeckOut, status_code=201)
def create_deck(body: DeckCreate, svc: Svc, user_id: User):
    return svc.create_deck(user_id, body)


@router.get("/decks/{deck_id}/cards", response_model=list[CardOut], dependencies=[Depends(require_not_exam_mode)])
def list_cards(deck_id: str, svc: Svc, user_id: User):
    return svc.list_cards(user_id, deck_id)


@router.post("/decks/{deck_id}/cards", response_model=CardOut, status_code=201, dependencies=[Depends(require_not_exam_mode)])
def create_card(deck_id: str, body: CardCreate, svc: Svc, user_id: User):
    return svc.create_card(user_id, deck_id, body)


@router.patch("/cards/{card_id}", response_model=CardOut, dependencies=[Depends(require_not_exam_mode)])
def patch_card(card_id: str, body: CardPatch, svc: Svc, user_id: User):
    return svc.patch_card(user_id, card_id, body)


@router.get("/reviews/due", response_model=list[CardOut], dependencies=[Depends(require_not_exam_mode)])
def reviews_due(svc: Svc, user_id: User, deck_id: str | None = None, limit: int = Query(50, ge=1, le=200)):
    return svc.reviews_due(user_id, deck_id, limit)


@router.post("/cards/{card_id}/review", response_model=ReviewResult, dependencies=[Depends(require_not_exam_mode)])
def review_card(
    card_id: str,
    body: ReviewRequest,
    svc: Svc,
    user_id: User,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
):
    return svc.review_card(user_id, card_id, body.rating, body.expected_revision, idempotency_key)


@router.get("/quizzes", response_model=list[QuizOut])
def list_quizzes(svc: Svc, user_id: User, subject_id: str | None = None):
    return svc.list_quizzes(user_id, subject_id)


@router.post("/quizzes", response_model=QuizOut, status_code=201)
def create_quiz(body: QuizCreate, svc: Svc, user_id: User):
    return svc.create_quiz(user_id, body)


@router.get("/quizzes/{quiz_id}", response_model=QuizOut)
def get_quiz(quiz_id: str, svc: Svc, user_id: User):
    return svc.get_quiz(user_id, quiz_id)


@router.post("/quizzes/{quiz_id}/attempts", response_model=AttemptOut, status_code=201)
def start_attempt(quiz_id: str, body: AttemptCreate, svc: Svc, user_id: User):
    return svc.start_attempt(user_id, quiz_id, body.mode, body.session_id)


@router.get("/attempts/{attempt_id}", response_model=AttemptOut)
def get_attempt(attempt_id: str, svc: Svc, user_id: User):
    return svc.get_attempt(user_id, attempt_id)


@router.patch("/attempts/{attempt_id}/responses", response_model=AttemptOut)
def save_responses(attempt_id: str, body: ResponsesPatch, svc: Svc, user_id: User):
    return svc.save_responses(user_id, attempt_id, body)


@router.post("/attempts/{attempt_id}/submit", response_model=AttemptResult)
def submit(attempt_id: str, svc: Svc, user_id: User):
    return svc.submit(user_id, attempt_id)


@router.get("/attempts/{attempt_id}/result", response_model=AttemptResult, dependencies=[Depends(require_not_exam_mode)])
def result(attempt_id: str, svc: Svc, user_id: User):
    return svc.result(user_id, attempt_id)


@router.post("/attempts/{attempt_id}/items/{question_id}/grade", response_model=AttemptResult, dependencies=[Depends(require_not_exam_mode)])
def grade_item(attempt_id: str, question_id: str, body: ItemGrade, svc: Svc, user_id: User):
    return svc.grade_item(user_id, attempt_id, question_id, body)


@router.post("/attempts/{attempt_id}/errors", response_model=AttemptResult, dependencies=[Depends(require_not_exam_mode)])
def log_errors(attempt_id: str, body: LogErrorsRequest, svc: Svc, user_id: User):
    return svc.log_errors(user_id, attempt_id, body.question_ids)


@router.post("/quizzes/mock", response_model=QuizOut, status_code=201)
def mock_exam(body: MockExamRequest, svc: Svc, user_id: User):
    return svc.generate_mock_exam(user_id, body)


@router.get("/learning/calibration", response_model=CalibrationOut)
def learning_calibration(svc: Svc, user_id: User):
    return svc.calibration_report(user_id)


@router.get("/concepts/prerequisites")
def get_prerequisites(svc: Svc, user_id: User):
    return {"edges": svc.get_prerequisites(user_id), "interim": "owned by catalog (C1) once available"}


@router.put("/concepts/prerequisites")
def put_prerequisites(body: PrerequisitesPut, svc: Svc, user_id: User):
    return {"edges": svc.put_prerequisites(user_id, body), "interim": "owned by catalog (C1) once available"}


@router.get("/exam-mode", response_model=ExamModeOut)
def exam_mode(svc: Svc, user_id: User):
    return svc.exam_mode(user_id)


@router.get("/mastery", response_model=list[MasteryOut])
def mastery(svc: Svc, user_id: User, subject_id: str | None = None, concept_id: str | None = None):
    return svc.list_mastery(user_id, subject_id, concept_id)


@router.get("/study-needs", response_model=list[StudyNeedOut])
def study_needs(svc: Svc, user_id: User, horizon_days: int = Query(7, ge=1, le=31)):
    return svc.study_needs_out(user_id, horizon_days)
