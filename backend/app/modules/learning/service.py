"""Learning use cases: flashcard review, quiz attempts, evidence → mastery, study needs."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.common import Clock, check_revision
from app.core.db import new_id
from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.core.events import record_event
from app.modules.account.service import get_planning_preferences
from app.modules.learning import calibration, domain, fsrs
from app.modules.learning.catalog_port import get_catalog
from app.modules.learning.models import (
    Attempt,
    AttemptItem,
    CardReview,
    ConceptEdge,
    Deck,
    Evidence,
    Flashcard,
    Mastery,
    Question,
    Quiz,
)
from app.modules.learning.schemas import (
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
    ItemResult,
    MasteryOut,
    MockExamRequest,
    PrerequisitesPut,
    QuestionPublic,
    QuizCreate,
    QuizOut,
    ResponsesPatch,
    ReviewResult,
    StudyNeedOut,
)

REVIEW_SESSION_MIN = 20
REVIEW_WINDOW_DAYS = 3
PRACTICE_SESSION_MIN = 30


@dataclass(frozen=True)
class StudyNeed:
    """Learning → planner contract (docs/ADAPTIVE_LEARNING.md § StudyNeed)."""

    need_id: str
    kind: str
    activity_type: str
    subject_id: str | None
    concept_ids: tuple[str, ...]
    dimension: str | None
    estimated_min: int
    earliest_at: datetime | None
    due_at: datetime | None
    reason: str
    success_criteria: str
    priority_factors: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ConceptMastery:
    concept_id: str
    score: float | None  # weakest known dimension
    status: str


class LearningService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def _owned(self, model, entity_id: str, user_id: str):
        obj = self.db.get(model, entity_id)
        if obj is None or obj.user_id != user_id:
            raise NotFound(f"{model.__name__} not found")
        return obj

    # ================================================================ decks/cards ====
    def _card_out(self, c: Flashcard) -> CardOut:
        now = self.clock.now()
        return CardOut(
            id=c.id,
            deck_id=c.deck_id,
            concept_id=c.concept_id,
            front=c.front,
            back=c.back,
            interval_days=c.interval_days,
            due_at=c.due_at,
            review_count=c.review_count,
            last_rating=c.last_rating,  # type: ignore[arg-type]
            is_due=c.due_at is None or c.due_at <= now,
            revision=c.revision,
        )

    def _deck_out(self, d: Deck) -> DeckOut:
        now = self.clock.now()
        total = self.db.scalar(select(func.count()).where(Flashcard.deck_id == d.id)) or 0
        due = (
            self.db.scalar(
                select(func.count()).where(
                    Flashcard.deck_id == d.id,
                    or_(Flashcard.due_at.is_(None), Flashcard.due_at <= now),
                )
            )
            or 0
        )
        return DeckOut(
            id=d.id, title=d.title, subject_id=d.subject_id, card_count=total, due_count=due, revision=d.revision
        )

    def list_decks(self, user_id: str) -> list[DeckOut]:
        decks = self.db.scalars(select(Deck).where(Deck.user_id == user_id).order_by(Deck.title, Deck.id))
        return [self._deck_out(d) for d in decks]

    def create_deck(self, user_id: str, body: DeckCreate) -> DeckOut:
        get_catalog(self.db).check_subject(user_id, body.subject_id)
        d = Deck(user_id=user_id, title=body.title, subject_id=body.subject_id)
        self.db.add(d)
        self.db.flush()
        return self._deck_out(d)

    def list_cards(self, user_id: str, deck_id: str) -> list[CardOut]:
        self._owned(Deck, deck_id, user_id)
        cards = self.db.scalars(
            select(Flashcard).where(Flashcard.deck_id == deck_id).order_by(Flashcard.created_at, Flashcard.id)
        )
        return [self._card_out(c) for c in cards]

    def create_card(self, user_id: str, deck_id: str, body: CardCreate) -> CardOut:
        front = body.front.strip()
        back = body.back.strip()
        if not front or not back:
            raise ValidationFailed("front and back must be non-empty")
        if body.concept_id:
            get_catalog(self.db).check_concepts(user_id, [body.concept_id])
        self._owned(Deck, deck_id, user_id)
        c = Flashcard(
            deck_id=deck_id, user_id=user_id, front=front, back=back, concept_id=body.concept_id
        )
        self.db.add(c)
        self.db.flush()
        return self._card_out(c)

    def patch_card(self, user_id: str, card_id: str, body: CardPatch) -> CardOut:
        c = self._owned(Flashcard, card_id, user_id)
        check_revision(c.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        for name in fields & {"front", "back"}:
            val = getattr(body, name)
            if val is None or not str(val).strip():
                raise ValidationFailed(f"{name} cannot be null or blank")
        changed = False
        for name in fields:
            val = getattr(body, name)
            if name in ("front", "back") and isinstance(val, str):
                val = val.strip()
            if getattr(c, name) != val:
                setattr(c, name, val)
                changed = True
        if changed:
            c.revision += 1
        return self._card_out(c)

    def reviews_due(self, user_id: str, deck_id: str | None, limit: int) -> list[CardOut]:
        now = self.clock.now()
        q = select(Flashcard).where(
            Flashcard.user_id == user_id, or_(Flashcard.due_at.is_(None), Flashcard.due_at <= now)
        )
        if deck_id:
            q = q.where(Flashcard.deck_id == deck_id)
        q = q.order_by(Flashcard.due_at.is_(None), Flashcard.due_at, Flashcard.id).limit(limit)
        return [self._card_out(c) for c in self.db.scalars(q)]

    def review_card(
        self,
        user_id: str,
        card_id: str,
        rating: str,
        expected_revision: int | None,
        idempotency_key: str | None,
    ) -> ReviewResult:
        c = self._owned(Flashcard, card_id, user_id)
        if idempotency_key:
            prior = self.db.scalar(
                select(CardReview).where(
                    CardReview.user_id == user_id, CardReview.idempotency_key == idempotency_key
                )
            )
            if prior is not None:
                if prior.card_id != card_id or prior.rating != rating:
                    raise Conflict("Idempotency-Key reused with a different request", code="idempotency_mismatch")
                return ReviewResult(
                    card_id=card_id,
                    review_id=prior.id,
                    rating=prior.rating,  # type: ignore[arg-type]
                    interval_days=prior.interval_days,
                    next_due_at=prior.due_at,
                    mastery_updates=[],
                    replayed=True,
                    algorithm=prior.algorithm_version,
                )
        check_revision(c.revision, expected_revision)
        now = self.clock.now()
        baseline = domain.next_interval(rating, c.interval_days)
        prefs = get_planning_preferences(self.db, user_id)
        algorithm, interval, r_before = domain.ALGORITHM_VERSION, baseline, None
        if prefs.repetition_algorithm == "fsrs":
            if c.fsrs_stability is None or c.last_reviewed_at is None:
                state = fsrs.init_state(rating)
            else:
                elapsed = (now - c.last_reviewed_at).total_seconds() / 86400
                r_before = fsrs.retrievability(elapsed, c.fsrs_stability)
                state = fsrs.next_state(fsrs.MemoryState(c.fsrs_stability, c.fsrs_difficulty or 5.0), rating, elapsed)
            c.fsrs_stability, c.fsrs_difficulty = state.stability, state.difficulty
            interval = fsrs.interval_days(state.stability, prefs.desired_retention)
            algorithm = fsrs.ALGORITHM_VERSION
        due = now + timedelta(days=interval)
        review = CardReview(
            card_id=c.id,
            user_id=user_id,
            rating=rating,
            reviewed_at=now,
            interval_days=interval,
            due_at=due,
            algorithm_version=algorithm,
            idempotency_key=idempotency_key or new_id(),
        )
        self.db.add(review)
        c.interval_days, c.due_at, c.last_rating, c.last_reviewed_at = interval, due, rating, now
        c.review_count += 1
        c.revision += 1
        self.db.flush()
        updates: list[MasteryOut] = []
        if c.concept_id:
            deck = self.db.get(Deck, c.deck_id)
            ev = Evidence(
                user_id=user_id,
                concept_id=c.concept_id,
                subject_id=deck.subject_id if deck else None,
                dimension="memory",
                activity_kind="card",
                activity_id=review.id,
                item_id=c.id,
                score=domain.RATING_SCORE[rating],
                weight=domain.CARD_EVIDENCE_WEIGHT,
                occurred_at=now,
            )
            self.db.add(ev)
            self.db.flush()
            m = self._apply_evidence([ev], interval_override=interval)
            updates = [self._mastery_out(x) for x in m]
        record_event(self.db, user_id=user_id, type="EvidenceRecorded", entity_id=review.id, revision=1)
        return ReviewResult(
            card_id=c.id,
            review_id=review.id,
            rating=rating,  # type: ignore[arg-type]
            interval_days=interval,
            next_due_at=due,
            mastery_updates=updates,
            algorithm=algorithm,
            baseline_interval_days=baseline,
            retrievability_before=None if r_before is None else round(r_before, 4),
        )

    # ==================================================================== quizzes ====
    def _questions(self, quiz_id: str) -> list[Question]:
        return list(self.db.scalars(select(Question).where(Question.quiz_id == quiz_id).order_by(Question.ordinal)))

    @staticmethod
    def _public(q: Question) -> QuestionPublic:
        return QuestionPublic(
            id=q.id,
            ordinal=q.ordinal,
            type=q.type,  # type: ignore[arg-type]
            prompt=q.prompt,
            options=q.options,
            points=q.points,
            concept_ids=q.concept_ids,
            dimension=q.dimension,  # type: ignore[arg-type]
        )

    def _quiz_out(self, quiz: Quiz) -> QuizOut:
        qs = self._questions(quiz.id)
        return QuizOut(
            id=quiz.id,
            title=quiz.title,
            subject_id=quiz.subject_id,
            type=quiz.type,
            time_limit_min=quiz.time_limit_min,
            question_count=len(qs),
            total_points=sum(q.points for q in qs),
            questions=[self._public(q) for q in qs],
            revision=quiz.revision,
        )

    def create_quiz(self, user_id: str, body: QuizCreate) -> QuizOut:
        title = body.title.strip()
        if not title:
            raise ValidationFailed("Quiz title must be non-empty")
        catalog = get_catalog(self.db)
        catalog.check_subject(user_id, body.subject_id)
        catalog.check_concepts(user_id, [c for q in body.questions for c in q.concept_ids])
        if body.type == "exam" and body.time_limit_min is None:
            raise ValidationFailed("exam quizzes require time_limit_min")
        errors = []
        for i, q in enumerate(body.questions):
            if not q.prompt.strip():
                errors.append({"question": i, "message": "prompt must be non-empty"})
            option_ids = [o.id for o in q.options]
            if any(not o.id.strip() or not o.text.strip() for o in q.options):
                errors.append({"question": i, "message": "option id and text must be non-empty"})
            if len(set(option_ids)) != len(option_ids):
                errors.append({"question": i, "message": "duplicate option ids"})
            if q.type not in ("single_choice", "multiple_choice") and q.options:
                errors.append({"question": i, "message": f"{q.type} questions take no options"})
            msg = domain.validate_answer_key(q.type, q.answer, option_ids)
            if msg:
                errors.append({"question": i, "message": msg})
        if errors:
            raise ValidationFailed("Invalid questions", details=errors)
        quiz = Quiz(
            user_id=user_id,
            subject_id=body.subject_id,
            title=title,
            type=body.type,
            time_limit_min=body.time_limit_min,
            origin=body.origin,
        )
        self.db.add(quiz)
        self.db.flush()
        for i, q in enumerate(body.questions):
            self.db.add(
                Question(
                    quiz_id=quiz.id,
                    ordinal=i,
                    type=q.type,
                    prompt=q.prompt.strip(),
                    options=[{"id": o.id.strip(), "text": o.text.strip()} for o in q.options],
                    private_answer=q.answer,
                    explanation=q.explanation.strip() if q.explanation else None,
                    points=q.points,
                    concept_ids=list(dict.fromkeys(q.concept_ids)),
                    dimension=q.dimension,
                    difficulty=q.difficulty,
                    partial_credit=q.partial_credit and q.type == "multiple_choice",
                )
            )
        self.db.flush()
        return self._quiz_out(quiz)

    def list_quizzes(self, user_id: str, subject_id: str | None) -> list[QuizOut]:
        q = select(Quiz).where(Quiz.user_id == user_id)
        if subject_id:
            q = q.where(Quiz.subject_id == subject_id)
        return [self._quiz_out(x) for x in self.db.scalars(q.order_by(Quiz.created_at, Quiz.id))]

    def get_quiz(self, user_id: str, quiz_id: str) -> QuizOut:
        return self._quiz_out(self._owned(Quiz, quiz_id, user_id))

    # =================================================================== attempts ====
    def start_attempt(self, user_id: str, quiz_id: str, mode: str, session_id: str | None) -> AttemptOut:
        if self.exam_mode(user_id).active:
            raise Forbidden("Finish the active exam first", code="exam_mode_active")
        quiz = self._owned(Quiz, quiz_id, user_id)
        if quiz.type == "exam" and mode != "exam":
            raise ValidationFailed("exam quizzes can only be attempted in exam mode")
        now = self.clock.now()
        expires = None
        if mode == "exam":
            if not quiz.time_limit_min:
                raise ValidationFailed("exam mode requires a quiz time limit")
            expires = now + timedelta(minutes=quiz.time_limit_min)
        a = Attempt(
            user_id=user_id,
            quiz_id=quiz.id,
            quiz_revision=quiz.revision,
            mode=mode,
            started_at=now,
            expires_at=expires,
            session_id=session_id,
        )
        self.db.add(a)
        self.db.flush()
        return self._attempt_out(a)

    def _attempt_out(self, a: Attempt) -> AttemptOut:
        items = self.db.scalars(select(AttemptItem).where(AttemptItem.attempt_id == a.id))
        return AttemptOut(
            id=a.id,
            quiz_id=a.quiz_id,
            mode=a.mode,
            status=a.status,  # type: ignore[arg-type]
            started_at=a.started_at,
            expires_at=a.expires_at,
            questions=[self._public(q) for q in self._questions(a.quiz_id)],
            responses={i.question_id: i.response for i in items},
        )

    def _load_attempt(self, user_id: str, attempt_id: str) -> Attempt:
        a = self._owned(Attempt, attempt_id, user_id)
        if a.status == "active" and a.expires_at is not None and self.clock.now() >= a.expires_at:
            self._finalize(a, "expired")  # server-side timer is authoritative
        return a

    def get_attempt(self, user_id: str, attempt_id: str) -> AttemptOut:
        return self._attempt_out(self._load_attempt(user_id, attempt_id))

    def save_responses(self, user_id: str, attempt_id: str, body: ResponsesPatch) -> AttemptOut:
        a = self._load_attempt(user_id, attempt_id)
        if a.status != "active":
            raise Conflict(f"Attempt is {a.status}", code=f"attempt_{a.status}")
        questions_by_id = {q.id: q for q in self._questions(a.quiz_id)}
        unknown = [r.question_id for r in body.responses if r.question_id not in questions_by_id]
        if unknown:
            raise ValidationFailed("Unknown question ids", details=unknown)
        if a.mode == "exam" and any(r.assisted for r in body.responses):
            raise ValidationFailed("Assistance is not allowed in exam mode")
        # Deduplicate repeated question_ids within the same PATCH (last entry wins)
        deduped = {}
        for r in body.responses:
            deduped[r.question_id] = r
        invalid_responses = []
        for qid, r in deduped.items():
            q = questions_by_id[qid]
            opt_ids = [o["id"] for o in (q.options or []) if isinstance(o, dict) and "id" in o]
            msg = domain.validate_response_payload(q.type, r.response, opt_ids)
            if msg:
                invalid_responses.append({"question_id": qid, "message": msg})
        if invalid_responses:
            raise ValidationFailed("Invalid response payloads", details=invalid_responses)
        now = self.clock.now()
        for qid, r in deduped.items():
            item = self.db.scalar(
                select(AttemptItem).where(AttemptItem.attempt_id == a.id, AttemptItem.question_id == qid)
            )
            if item is None:
                item = AttemptItem(attempt_id=a.id, question_id=qid)
                self.db.add(item)
            response_val = list(dict.fromkeys(r.response)) if (questions_by_id[qid].type == "multiple_choice" and isinstance(r.response, list)) else r.response
            item.response, item.assisted, item.updated_at = response_val, r.assisted, now
        self.db.flush()
        return self._attempt_out(a)

    def submit(self, user_id: str, attempt_id: str) -> AttemptResult:
        exam = self.exam_mode(user_id)
        if exam.active and exam.attempt_id != attempt_id:
            raise Forbidden("Finish the active exam first", code="exam_mode_active")
        a = self._load_attempt(user_id, attempt_id)
        if a.status == "active":
            updates = self._finalize(a, "submitted")
            return self._result(a, updates)
        return self._result(a, [])  # idempotent: already finalized, nothing re-recorded

    def _wrong_items(self, a: Attempt, question_ids: list[str] | None):
        quiz = self.db.get(Quiz, a.quiz_id)
        items = {i.question_id: i for i in self.db.scalars(select(AttemptItem).where(AttemptItem.attempt_id == a.id))}
        out = []
        for q in self._questions(a.quiz_id):
            i = items.get(q.id)
            if question_ids is not None and q.id not in question_ids:
                continue
            if i is not None and i.score is not None and i.score < 1 and (i.response is not None or a.mode == "exam"):
                out.append((q.id, q.prompt, q.concept_ids, quiz.subject_id if quiz else None))
        return out

    def _log_errors(self, a: Attempt, question_ids: list[str] | None) -> int:
        from app.modules.workspace.service import record_item_errors

        return record_item_errors(self.db, a.user_id, self.clock.now(), "exam" if a.mode == "exam" else "quiz",
                                  self._wrong_items(a, question_ids))

    def log_errors(self, user_id: str, attempt_id: str, question_ids: list[str] | None) -> AttemptResult:
        a = self._load_attempt(user_id, attempt_id)
        if a.status == "active":
            raise Forbidden("Submit the attempt first", code="attempt_active")
        logged = self._log_errors(a, question_ids)
        res = self._result(a, [])
        res.errors_logged = logged
        return res

    def grade_item(self, user_id: str, attempt_id: str, question_id: str, body: ItemGrade) -> AttemptResult:
        """T35: grade an open answer after submission. Teacher = full evidence, self = half,
        AI = provisional (stored and labeled, no evidence until confirmed)."""
        a = self._load_attempt(user_id, attempt_id)
        if a.status == "active":
            raise Forbidden("Grading is only possible after submission", code="attempt_active")
        item = self.db.scalar(select(AttemptItem).where(AttemptItem.attempt_id == a.id, AttemptItem.question_id == question_id))
        q = self.db.get(Question, question_id)
        if item is None or q is None or q.quiz_id != a.quiz_id:
            raise NotFound("Item not found")
        if item.score is not None and item.grader != "ai_provisional":
            raise Conflict("Item already graded", code="item_already_graded")
        was_pending = item.score is None
        if not was_pending:
            a.score = (a.score or 0) - (item.score or 0) * q.points
        item.score, item.grader, item.feedback = body.score, body.grader, body.feedback
        a.score = (a.score or 0) + body.score * q.points
        if was_pending:
            a.pending_review = max(0, a.pending_review - 1)
        updates: list[Mastery] = []
        if body.grader != "ai_provisional" and item.response is not None:
            quiz = self.db.get(Quiz, a.quiz_id)
            weight = domain.evidence_weight(item.assisted, 1.0 if body.grader == "teacher" else 0.5)
            evs = [
                Evidence(user_id=user_id, concept_id=cid, subject_id=quiz.subject_id if quiz else None,
                         dimension=q.dimension, activity_kind="quiz", activity_id=a.id, item_id=q.id,
                         score=body.score, weight=weight, assisted=item.assisted, difficulty=q.difficulty,
                         occurred_at=self.clock.now())
                for cid in q.concept_ids
            ]
            self.db.add_all(evs)
            self.db.flush()
            updates = self._apply_evidence(evs)
        self.db.flush()
        return self._result(a, updates)

    def exam_mode(self, user_id: str) -> ExamModeOut:
        """Lets the UI hide tutor/studio/corrections while a timed exam is active (R21)."""
        for a in self.db.scalars(select(Attempt).where(Attempt.user_id == user_id, Attempt.status == "active",
                                                       Attempt.mode == "exam")):
            a = self._load_attempt(user_id, a.id)  # expires on read if needed
            if a.status == "active":
                return ExamModeOut(active=True, attempt_id=a.id, quiz_id=a.quiz_id, expires_at=a.expires_at,
                                   blocked_features=["tutor", "studio", "corrections", "solutions", "notes_ai"])
        return ExamModeOut(active=False)

    def result(self, user_id: str, attempt_id: str) -> AttemptResult:
        a = self._load_attempt(user_id, attempt_id)
        if a.status == "active":
            raise Forbidden("Results are hidden while the attempt is active", code="attempt_active")
        return self._result(a, [])

    def _finalize(self, a: Attempt, status: str) -> list[Mastery]:
        quiz = self.db.get(Quiz, a.quiz_id)
        now = self.clock.now()
        items = {i.question_id: i for i in self.db.scalars(select(AttemptItem).where(AttemptItem.attempt_id == a.id))}
        score = max_score = 0.0
        pending = 0
        evidence: list[Evidence] = []
        for q in self._questions(a.quiz_id):
            item = items.get(q.id)
            if item is None:
                item = AttemptItem(attempt_id=a.id, question_id=q.id, response=None)
                self.db.add(item)
            item.score = domain.score_item(q.type, q.private_answer, item.response, q.partial_credit)
            item.grader = None if item.score is None else "auto"
            max_score += q.points
            if item.score is None:
                pending += 1
                continue
            score += item.score * q.points
            if item.response is None and a.mode != "exam":
                continue  # unanswered practice item: no objective evidence either way
            for concept_id in q.concept_ids:
                evidence.append(
                    Evidence(
                        user_id=a.user_id,
                        concept_id=concept_id,
                        subject_id=quiz.subject_id if quiz else None,
                        dimension=q.dimension,
                        activity_kind="quiz",
                        activity_id=a.id,
                        item_id=q.id,
                        score=item.score,
                        weight=domain.evidence_weight(item.assisted),
                        assisted=item.assisted,
                        difficulty=q.difficulty,
                        occurred_at=now,
                    )
                )
        a.status, a.submitted_at = status, now
        a.score, a.max_score, a.pending_review = score, max_score, pending
        self.db.add_all(evidence)
        self.db.flush()
        updated = self._apply_evidence(evidence)
        if a.mode == "exam":  # T24: exam errors go to the error notebook automatically
            self._log_errors(a, None)
        record_event(
            self.db, user_id=a.user_id, type="EvidenceRecorded", entity_id=a.id, revision=1,
            payload={"evidence_count": len(evidence), "status": status},
        )
        return updated

    def _apply_evidence(self, evidence: list[Evidence], interval_override: int | None = None) -> list[Mastery]:
        """EMA per evidence row; one interval step per concept/dimension per activity.
        T33: records the model's prior prediction on each row and derives retention (delayed
        recall) / autonomy (unassisted application) evidence."""
        now = self.clock.now()
        evidence = self._enrich(evidence)
        groups: dict[tuple[str, str], list[Evidence]] = defaultdict(list)
        for ev in evidence:
            groups[(ev.concept_id, ev.dimension)].append(ev)
        updated = []
        for (concept_id, dimension), evs in sorted(groups.items()):
            user_id = evs[0].user_id
            m = self.db.scalar(
                select(Mastery).where(
                    Mastery.user_id == user_id, Mastery.concept_id == concept_id, Mastery.dimension == dimension
                )
            )
            if m is None:
                m = Mastery(
                    id=new_id(),
                    interval_days=0,
                    n_effective=0.0,
                    evidence_count=0,
                    user_id=user_id,
                    concept_id=concept_id,
                    dimension=dimension,
                    subject_id=evs[0].subject_id,
                    algorithm_version=domain.ALGORITHM_VERSION,
                    revision=0,
                )
                self.db.add(m)
            state = None if m.score is None else domain.MasteryState(m.score, m.n_effective, m.evidence_count)
            for ev in evs:
                state = domain.update_mastery(state, ev.score, ev.weight)
            assert state is not None
            m.score, m.n_effective, m.evidence_count = state.score, state.n_effective, state.evidence_count
            mean = sum(e.score for e in evs) / len(evs)
            interval = interval_override or domain.next_interval(domain.rating_from_score(mean), m.interval_days)
            m.interval_days = interval
            m.last_review_at = now
            m.next_due_at = now + timedelta(days=interval)
            m.subject_id = m.subject_id or evs[0].subject_id
            m.algorithm_version = domain.ALGORITHM_VERSION
            m.revision += 1
            updated.append(m)
            record_event(self.db, user_id=user_id, type="MasteryUpdated", entity_id=m.id, revision=m.revision)
        self.db.flush()
        return updated

    def _enrich(self, evidence: list[Evidence]) -> list[Evidence]:
        out = list(evidence)
        for ev in evidence:
            prior = self.db.scalar(select(Mastery.score).where(
                Mastery.user_id == ev.user_id, Mastery.concept_id == ev.concept_id, Mastery.dimension == ev.dimension))
            if ev.predicted is None:
                ev.predicted = prior
            last = self.db.scalar(select(func.max(Evidence.occurred_at)).where(
                Evidence.user_id == ev.user_id, Evidence.concept_id == ev.concept_id,
                Evidence.id != ev.id, Evidence.occurred_at < ev.occurred_at))
            days = None if last is None else (ev.occurred_at - last).total_seconds() / 86400
            for dim in calibration.derived_dimensions(ev.dimension, ev.assisted, days):
                exists = self.db.scalar(select(Evidence.id).where(
                    Evidence.activity_id == ev.activity_id, Evidence.item_id == ev.item_id,
                    Evidence.concept_id == ev.concept_id, Evidence.dimension == dim))
                if exists:
                    continue
                d_prior = self.db.scalar(select(Mastery.score).where(
                    Mastery.user_id == ev.user_id, Mastery.concept_id == ev.concept_id, Mastery.dimension == dim))
                extra = Evidence(
                    id=new_id(), user_id=ev.user_id, concept_id=ev.concept_id, subject_id=ev.subject_id,
                    dimension=dim, activity_kind=ev.activity_kind, activity_id=ev.activity_id, item_id=ev.item_id,
                    score=ev.score, weight=ev.weight * calibration.DERIVED_WEIGHT, assisted=False,
                    difficulty=ev.difficulty, occurred_at=ev.occurred_at, predicted=d_prior, derived_from=ev.id,
                )
                self.db.add(extra)
                out.append(extra)
        self.db.flush()
        return out

    # ------------------------------------------------------------------ T33 / T24 / R12 --
    def calibration_report(self, user_id: str) -> CalibrationOut:
        rows = list(self.db.execute(select(Evidence.predicted, Evidence.score, Evidence.activity_kind).where(
            Evidence.user_id == user_id, Evidence.predicted.is_not(None), Evidence.derived_from.is_(None))))
        overall = calibration.calibration([(p, s) for p, s, _ in rows])
        by_kind = {}
        for kind in sorted({k for _, _, k in rows}):
            r = calibration.calibration([(p, s) for p, s, k in rows if k == kind])
            by_kind[kind] = {"samples": r.samples, "brier": r.brier, "brier_skill": r.brier_skill}
        notes = ["Predicted = mastery estimate just before each objective observation; success = score >= 0.5."]
        if not overall.sufficient:
            notes.append(f"Fewer than {calibration.MIN_CALIBRATION_SAMPLES} samples: not enough to judge calibration.")
        return CalibrationOut(samples=overall.samples, brier=overall.brier, base_rate=overall.base_rate,
                              brier_skill=overall.brier_skill, bins=overall.bins, sufficient=overall.sufficient,
                              by_kind=by_kind, notes=notes)

    def generate_mock_exam(self, user_id: str, body: MockExamRequest) -> QuizOut:
        """Deterministic mock exam from the student's own question bank (no AI): maximize
        coverage of the requested (current-syllabus) concepts, then prefer historical-exam items."""
        quizzes = {q.id: q for q in self.db.scalars(select(Quiz).where(Quiz.user_id == user_id,
                                                                     Quiz.subject_id == body.subject_id))}
        pool = [q for q in self.db.scalars(select(Question).where(Question.quiz_id.in_(list(quizzes))))
                .all() if q.type != "open" or q.private_answer is not None]
        scope = set(body.concept_ids or [])
        if scope:
            pool = [q for q in pool if scope & set(q.concept_ids)]
        if body.difficulty:
            pool = [q for q in pool if q.difficulty in (None, body.difficulty)]
        seen_prompts, unique = set(), []
        for q in sorted(pool, key=lambda q: (quizzes[q.quiz_id].origin != "historical_exam", q.quiz_id, q.ordinal)):
            key = q.prompt.strip().lower()
            if key not in seen_prompts:
                seen_prompts.add(key)
                unique.append(q)
        if not unique:
            raise ValidationFailed("No question in the bank matches this subject/scope", code="empty_question_bank")
        chosen, covered = [], set()
        remaining = list(unique)
        while remaining and len(chosen) < body.question_count:
            best = max(remaining, key=lambda q: (len(set(q.concept_ids) - covered), -unique.index(q)))
            chosen.append(best)
            covered |= set(best.concept_ids)
            remaining.remove(best)
        quiz = Quiz(user_id=user_id, subject_id=body.subject_id, type="exam", time_limit_min=body.time_limit_min,
                    title=body.title or f"Examen blanc ({len(chosen)} questions)", origin="mock_from_bank")
        self.db.add(quiz)
        self.db.flush()
        for i, q in enumerate(chosen):
            self.db.add(Question(
                quiz_id=quiz.id, ordinal=i, type=q.type, prompt=q.prompt, options=q.options,
                private_answer=q.private_answer, explanation=q.explanation, points=q.points,
                concept_ids=q.concept_ids, dimension=q.dimension, difficulty=q.difficulty,
                partial_credit=q.partial_credit,
            ))
        self.db.flush()
        out = self._quiz_out(quiz)
        if scope - covered:
            out.title += " — concepts non couverts : " + ", ".join(sorted(scope - covered))
        return out

    def get_prerequisites(self, user_id: str) -> list[dict]:
        rows = self.db.scalars(select(ConceptEdge).where(ConceptEdge.user_id == user_id)
                               .order_by(ConceptEdge.prerequisite_id, ConceptEdge.dependent_id))
        return [{"prerequisite_id": e.prerequisite_id, "dependent_id": e.dependent_id} for e in rows]

    def put_prerequisites(self, user_id: str, body: PrerequisitesPut) -> list[dict]:
        edges = {(e.prerequisite_id, e.dependent_id) for e in body.edges}
        get_catalog(self.db).check_concepts(user_id, sorted({c for e in edges for c in e}))
        if any(a == b for a, b in edges):
            raise ValidationFailed("A concept cannot be its own prerequisite", code="self_loop")
        cycle = find_cycle(edges)
        if cycle:
            raise ValidationFailed("Prerequisite graph contains a cycle", code="prerequisite_cycle", details=cycle)
        for old in self.db.scalars(select(ConceptEdge).where(ConceptEdge.user_id == user_id)):
            self.db.delete(old)
        self.db.flush()
        for a, b in sorted(edges):
            self.db.add(ConceptEdge(user_id=user_id, prerequisite_id=a, dependent_id=b))
        record_event(self.db, user_id=user_id, type="PrerequisitesChanged", entity_id=user_id, revision=len(edges))
        self.db.flush()
        return self.get_prerequisites(user_id)

    def _result(self, a: Attempt, updates: list[Mastery]) -> AttemptResult:
        items = {i.question_id: i for i in self.db.scalars(select(AttemptItem).where(AttemptItem.attempt_id == a.id))}
        out = []
        for q in self._questions(a.quiz_id):
            i = items.get(q.id)
            s = i.score if i else 0.0
            out.append(
                ItemResult(
                    question_id=q.id,
                    response=i.response if i else None,
                    correct_answer=q.private_answer,
                    explanation=q.explanation,
                    score=s,
                    points=q.points,
                    earned=None if s is None else s * q.points,
                    assisted=bool(i and i.assisted),
                    status="pending_review" if s is None else "graded",
                    grader=i.grader if i else None,
                    feedback=i.feedback if i else None,
                )
            )
        graded_max = (a.max_score or 0) - sum(o.points for o in out if o.score is None)
        return AttemptResult(
            attempt_id=a.id,
            status=a.status,  # type: ignore[arg-type]
            score=a.score or 0.0,
            max_score=a.max_score or 0.0,
            ratio=(a.score or 0.0) / graded_max if graded_max > 0 else None,
            pending_review=a.pending_review,
            items=out,
            mastery_updates=[self._mastery_out(m) for m in updates],
            recommendations=self._recommendations(a),
        )

    def _recommendations(self, a: Attempt) -> list[dict]:
        failed: dict[str, int] = {}
        for _, _, concepts, _ in self._wrong_items(a, None):
            for c in concepts:
                failed[c] = failed.get(c, 0) + 1
        return [
            {"concept_id": c, "failed_items": n,
             "suggestion": "Exercices ciblés puis rappel actif" if n > 1 else "Revoir la notion puis refaire l'item"}
            for c, n in sorted(failed.items())
        ]

    # ==================================================================== mastery ====
    def _mastery_out(self, m: Mastery) -> MasteryOut:
        return MasteryOut(
            concept_id=m.concept_id,
            subject_id=m.subject_id,
            dimension=m.dimension,  # type: ignore[arg-type]
            score=m.score,
            status=domain.mastery_status(m.score, m.n_effective, m.next_due_at, self.clock.now()),  # type: ignore[arg-type]
            confidence=domain.confidence(m.n_effective),
            evidence_count=m.evidence_count,
            interval_days=m.interval_days,
            last_review_at=m.last_review_at,
            next_due_at=m.next_due_at,
            algorithm_version=m.algorithm_version,
        )

    def list_mastery(self, user_id: str, subject_id: str | None, concept_id: str | None) -> list[MasteryOut]:
        q = select(Mastery).where(Mastery.user_id == user_id)
        if subject_id:
            q = q.where(Mastery.subject_id == subject_id)
        if concept_id:
            q = q.where(Mastery.concept_id == concept_id)
        return [self._mastery_out(m) for m in self.db.scalars(q.order_by(Mastery.concept_id, Mastery.dimension))]

    def _window_end(self, user_id: str, start: datetime, days: int) -> datetime:
        """Review windows close at the end of a local day, at least REVIEW_WINDOW_DAYS later,
        so a due time late in the evening still leaves usable study slots."""
        tz = ZoneInfo(get_planning_preferences(self.db, user_id).timezone)
        last_day = (start + timedelta(days=days)).astimezone(tz).date()
        return datetime.combine(last_day + timedelta(days=1), time(0), tzinfo=tz).astimezone(UTC)

    def study_needs(self, user_id: str, horizon_end: datetime) -> list[StudyNeed]:
        now = self.clock.now()
        needs: list[StudyNeed] = []
        for m in self.db.scalars(
            select(Mastery).where(Mastery.user_id == user_id).order_by(Mastery.concept_id, Mastery.dimension)
        ):
            status = domain.mastery_status(m.score, m.n_effective, m.next_due_at, now)
            gap = 0.5 if m.score is None else 1 - m.score
            if m.next_due_at is not None and m.next_due_at <= horizon_end:
                elapsed = (now - m.last_review_at).total_seconds() / 86400 if m.last_review_at else 0.0
                needs.append(
                    StudyNeed(
                        need_id=f"review:{m.concept_id}:{m.dimension}:{m.next_due_at.date().isoformat()}",
                        kind="review",
                        activity_type="active_recall",
                        subject_id=m.subject_id,
                        concept_ids=(m.concept_id,),
                        dimension=m.dimension,
                        estimated_min=REVIEW_SESSION_MIN,
                        earliest_at=max(now, m.next_due_at),
                        due_at=self._window_end(user_id, max(now, m.next_due_at), max(REVIEW_WINDOW_DAYS, m.interval_days)),
                        reason="REVIEW_OVERDUE" if m.next_due_at < now else "REVIEW_DUE",
                        success_criteria="Rappeler sans support puis vérifier par quiz ou cartes",
                        priority_factors={
                            "gap": gap,
                            "forgetting": domain.forgetting_risk(elapsed, m.interval_days),
                        },
                    )
                )
            elif status in ("learning", "weak"):
                needs.append(
                    StudyNeed(
                        need_id=f"practice:{m.concept_id}:{m.dimension}",
                        kind="practice",
                        activity_type="practice",
                        subject_id=m.subject_id,
                        concept_ids=(m.concept_id,),
                        dimension=m.dimension,
                        estimated_min=PRACTICE_SESSION_MIN,
                        earliest_at=None,
                        due_at=None,
                        reason="WEAK_MASTERY",
                        success_criteria="Réussir des exercices ciblés sans aide",
                        priority_factors={"gap": gap, "forgetting": 0.0},
                    )
                )
        # Flashcards due within the horizon, grouped by deck with concept coverage & forgetting risk.
        due_cards = list(
            self.db.execute(
                select(Deck.id, Deck.subject_id, Flashcard)
                .join(Flashcard, Flashcard.deck_id == Deck.id)
                .where(Deck.user_id == user_id, or_(Flashcard.due_at.is_(None), Flashcard.due_at <= horizon_end))
                .order_by(Deck.id, Flashcard.id)
            )
        )
        by_deck: dict[tuple[str, str | None], list[Flashcard]] = defaultdict(list)
        for deck_id, subject_id, card in due_cards:
            by_deck[(deck_id, subject_id)].append(card)
        for (deck_id, subject_id), cards in sorted(by_deck.items(), key=lambda kv: kv[0][0]):
            due_dates = [c.due_at for c in cards if c.due_at is not None]
            first_due = min(due_dates) if due_dates else None
            earliest = max(now, first_due) if first_due else now
            deck_concepts = tuple(sorted({c.concept_id for c in cards if c.concept_id}))
            risks: list[float] = []
            gaps: list[float] = []
            for c in cards:
                if c.last_reviewed_at is not None:
                    elapsed = max(0.0, (now - c.last_reviewed_at).total_seconds() / 86400)
                    if c.fsrs_stability is not None and c.fsrs_stability > 0:
                        risks.append(max(0.0, min(1.0, 1.0 - fsrs.retrievability(elapsed, c.fsrs_stability))))
                    else:
                        risks.append(domain.forgetting_risk(elapsed, max(1, c.interval_days)))
                else:
                    risks.append(0.5)
                if c.last_rating in domain.RATING_SCORE:
                    gaps.append(1.0 - domain.RATING_SCORE[c.last_rating])
                else:
                    gaps.append(0.5)
            mean_risk = round(sum(risks) / len(risks), 4) if risks else 0.5
            mean_gap = round(sum(gaps) / len(gaps), 4) if gaps else 0.5
            needs.append(
                StudyNeed(
                    need_id=f"cards:{deck_id}:{earliest.date().isoformat()}",
                    kind="flashcards",
                    activity_type="flashcards",
                    subject_id=subject_id,
                    concept_ids=deck_concepts,
                    dimension="memory",
                    estimated_min=min(45, max(10, math.ceil(len(cards) * 1.5))),
                    earliest_at=earliest,
                    due_at=self._window_end(user_id, earliest, REVIEW_WINDOW_DAYS),
                    reason="CARDS_OVERDUE" if (first_due is not None and first_due < now) else "CARDS_DUE",
                    success_criteria=f"Réviser {len(cards)} carte(s) dues",
                    priority_factors={"gap": mean_gap, "forgetting": mean_risk},
                )
            )
        return needs

    def study_needs_out(self, user_id: str, horizon_days: int) -> list[StudyNeedOut]:
        end = self.clock.now() + timedelta(days=horizon_days)
        return [
            StudyNeedOut(
                need_id=n.need_id,
                kind=n.kind,  # type: ignore[arg-type]
                activity_type=n.activity_type,
                subject_id=n.subject_id,
                concept_ids=list(n.concept_ids),
                dimension=n.dimension,  # type: ignore[arg-type]
                estimated_min=n.estimated_min,
                earliest_at=n.earliest_at,
                due_at=n.due_at,
                priority_factors=n.priority_factors,
                reason=n.reason,
                success_criteria=n.success_criteria,
            )
            for n in self.study_needs(user_id, end)
        ]


# ---- query interfaces for other modules (read-only) -------------------------------------
def concept_mastery(db: Session, user_id: str, concept_ids: list[str], now: datetime) -> dict[str, ConceptMastery]:
    if not concept_ids:
        return {}
    rows = list(db.scalars(select(Mastery).where(Mastery.user_id == user_id, Mastery.concept_id.in_(concept_ids))))
    out: dict[str, ConceptMastery] = {}
    for cid in concept_ids:
        mine = [m for m in rows if m.concept_id == cid and m.score is not None]
        if not mine:
            out[cid] = ConceptMastery(cid, None, "unknown")
            continue
        weakest = min(mine, key=lambda m: m.score)  # type: ignore[arg-type, return-value]
        out[cid] = ConceptMastery(
            cid, weakest.score, domain.mastery_status(weakest.score, weakest.n_effective, weakest.next_due_at, now)
        )
    return out


def find_cycle(edges: set[tuple[str, str]]) -> list[str] | None:
    graph: dict[str, list[str]] = defaultdict(list)
    for a, b in sorted(edges):
        graph[a].append(b)
    state: dict[str, int] = {}
    path: list[str] = []

    def visit(n: str) -> list[str] | None:
        state[n] = 1
        path.append(n)
        for m in graph.get(n, []):
            if state.get(m) == 1:
                return path[path.index(m):] + [m]
            if state.get(m) is None:
                found = visit(m)
                if found:
                    return found
        state[n] = 2
        path.pop()
        return None

    for node in sorted(graph):
        if state.get(node) is None:
            found = visit(node)
            if found:
                return found
    return None


def prerequisite_edges(db: Session, user_id: str) -> set[tuple[str, str]]:
    """Confirmed prerequisites, always read through the catalog port (C1 will own them)."""
    from app.modules.learning.catalog_port import get_catalog

    return get_catalog(db).prerequisites(user_id)


def exam_concept_frequency(db: Session, user_id: str, subject_id: str | None) -> dict[str, float]:
    """Share of the student's historical exam papers (in this subject) that test each concept."""
    q = select(Quiz).where(Quiz.user_id == user_id, Quiz.origin == "historical_exam")
    if subject_id:
        q = q.where(Quiz.subject_id == subject_id)
    papers = []
    for quiz in db.scalars(q):
        concepts = set()
        for question in db.scalars(select(Question).where(Question.quiz_id == quiz.id)):
            concepts |= set(question.concept_ids)
        papers.append(concepts)
    return calibration.exam_frequency(papers)


def concept_states(db: Session, user_id: str, concept_ids: list[str]) -> dict[str, tuple[float | None, float]]:
    """Weakest known dimension score and its effective evidence per concept (readiness)."""
    out: dict[str, tuple[float | None, float]] = {}
    rows = list(db.scalars(select(Mastery).where(Mastery.user_id == user_id, Mastery.concept_id.in_(concept_ids))))
    for cid in concept_ids:
        mine = [m for m in rows if m.concept_id == cid and m.score is not None]
        if not mine:
            out[cid] = (None, 0.0)
        else:
            w = min(mine, key=lambda m: m.score)  # type: ignore[arg-type, return-value]
            out[cid] = (w.score, w.n_effective)
    return out


def record_external_evidence(
    db: Session, user_id: str, *, concept_id: str, subject_id: str | None, dimension: str, activity_kind: str,
    activity_id: str, item_id: str, score: float, weight: float, occurred_at: datetime, clock: Clock,
) -> list[Mastery]:
    """Learning-owned entry point for confirmed evidence produced elsewhere (e.g. a corrected
    paper itemized by the student, T46). Idempotent on (activity, item, concept, dimension)."""
    exists = db.scalar(select(Evidence.id).where(Evidence.activity_id == activity_id, Evidence.item_id == item_id,
                                                 Evidence.concept_id == concept_id, Evidence.dimension == dimension))
    if exists:
        return []
    ev = Evidence(id=new_id(), user_id=user_id, concept_id=concept_id, subject_id=subject_id, dimension=dimension,
                  activity_kind=activity_kind, activity_id=activity_id, item_id=item_id, score=score, weight=weight,
                  occurred_at=occurred_at)
    db.add(ev)
    db.flush()
    updated = LearningService(db, clock)._apply_evidence([ev])
    record_event(db, user_id=user_id, type="EvidenceRecorded", entity_id=activity_id, revision=1,
                 payload={"evidence_count": 1, "kind": activity_kind})
    return updated


def learning_revision(db: Session, user_id: str) -> str:
    ev = db.scalar(select(func.count()).select_from(Evidence).where(Evidence.user_id == user_id)) or 0
    edges = db.scalar(select(func.count()).select_from(ConceptEdge).where(ConceptEdge.user_id == user_id)) or 0
    cards = db.scalar(select(func.count()).select_from(Flashcard).where(Flashcard.user_id == user_id)) or 0
    reviews = db.scalar(select(func.count()).select_from(CardReview).where(CardReview.user_id == user_id)) or 0
    return f"{ev}-{cards}-{reviews}-{edges}"


def learning_signals_for_planner(
    db: Session,
    clock: Clock,
    user_id: str,
    *,
    horizon_days: int = 7,
) -> dict:
    """Deterministic academic intelligence signals for Smart Planner and UI (zero LLM calls).

    Exposes mastery by subject, weakest concepts, overdue/due spaced-repetition reviews,
    FSRS retrievability alerts, structured study needs, calibration quality, and exam state.
    """
    now = clock.now()
    svc = LearningService(db, clock)
    horizon_end = now + timedelta(days=horizon_days)

    mastery_rows = list(
        db.scalars(select(Mastery).where(Mastery.user_id == user_id).order_by(Mastery.concept_id, Mastery.dimension))
    )
    by_subject: dict[str, dict] = {}
    weakest: list[dict] = []
    for m in mastery_rows:
        status = domain.mastery_status(m.score, m.n_effective, m.next_due_at, now)
        overdue = m.next_due_at is not None and m.next_due_at < now
        subj_key = m.subject_id or "__unassigned__"
        bucket = by_subject.setdefault(
            subj_key,
            {
                "subject_id": m.subject_id,
                "scores": [],
                "concepts": set(),
                "weak_count": 0,
                "learning_count": 0,
                "consolidate_count": 0,
                "acquired_count": 0,
                "overdue_reviews": 0,
            },
        )
        bucket["concepts"].add(m.concept_id)
        if m.score is not None:
            bucket["scores"].append(m.score)
        if status == "weak":
            bucket["weak_count"] += 1
        elif status == "learning":
            bucket["learning_count"] += 1
        elif status == "consolidate":
            bucket["consolidate_count"] += 1
        elif status == "acquired":
            bucket["acquired_count"] += 1
        if overdue:
            bucket["overdue_reviews"] += 1
        if status in ("learning", "weak", "consolidate") or overdue:
            weakest.append(
                {
                    "concept_id": m.concept_id,
                    "subject_id": m.subject_id,
                    "dimension": m.dimension,
                    "score": None if m.score is None else round(m.score, 4),
                    "status": status,
                    "confidence": round(domain.confidence(m.n_effective), 4),
                    "next_due_at": m.next_due_at.isoformat() if m.next_due_at else None,
                    "overdue": overdue,
                }
            )
    weakest.sort(key=lambda x: (not x["overdue"], x["score"] if x["score"] is not None else -1.0, x["concept_id"]))

    mastery_summary = [
        {
            "subject_id": b["subject_id"],
            "concept_count": len(b["concepts"]),
            "mean_score": round(sum(b["scores"]) / len(b["scores"]), 4) if b["scores"] else None,
            "weak_count": b["weak_count"],
            "learning_count": b["learning_count"],
            "consolidate_count": b["consolidate_count"],
            "acquired_count": b["acquired_count"],
            "overdue_reviews": b["overdue_reviews"],
        }
        for _, b in sorted(by_subject.items(), key=lambda kv: kv[0])
    ]

    # Due flashcards & FSRS low-retrievability alerts
    due_cards = list(
        db.execute(
            select(Deck.id, Deck.title, Deck.subject_id, Flashcard)
            .join(Flashcard, Flashcard.deck_id == Deck.id)
            .where(Deck.user_id == user_id, or_(Flashcard.due_at.is_(None), Flashcard.due_at <= horizon_end))
            .order_by(Deck.id, Flashcard.id)
        )
    )
    fsrs_alerts: list[dict] = []
    deck_map: dict[str, dict] = {}
    for deck_id, deck_title, subj_id, card in due_cards:
        d = deck_map.setdefault(
            deck_id,
            {"deck_id": deck_id, "title": deck_title, "subject_id": subj_id, "due_count": 0, "concept_ids": set()},
        )
        d["due_count"] += 1
        if card.concept_id:
            d["concept_ids"].add(card.concept_id)
        if card.fsrs_stability is not None and card.last_reviewed_at is not None:
            elapsed = max(0.0, (now - card.last_reviewed_at).total_seconds() / 86400)
            r_now = fsrs.retrievability(elapsed, card.fsrs_stability)
            if r_now < 0.85:
                fsrs_alerts.append(
                    {
                        "card_id": card.id,
                        "deck_id": deck_id,
                        "concept_id": card.concept_id,
                        "retrievability": round(r_now, 4),
                        "stability_days": round(card.fsrs_stability, 2),
                        "due_at": card.due_at.isoformat() if card.due_at else None,
                    }
                )
    fsrs_alerts.sort(key=lambda x: (x["retrievability"], x["card_id"]))

    cal = svc.calibration_report(user_id)
    needs = [n.model_dump(mode="json") for n in svc.study_needs_out(user_id, horizon_days)]

    return {
        "ai_used": False,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "horizon_days": horizon_days,
        "exam_mode_active": svc.exam_mode(user_id).active,
        "mastery_by_subject": mastery_summary,
        "weakest_concepts": weakest[:20],
        "due_flashcards": {
            "total_due": len(due_cards),
            "decks": [
                {
                    "deck_id": v["deck_id"],
                    "title": v["title"],
                    "subject_id": v["subject_id"],
                    "due_count": v["due_count"],
                    "concept_ids": sorted(v["concept_ids"]),
                }
                for v in deck_map.values()
            ],
        },
        "fsrs_alerts": fsrs_alerts[:20],
        "study_needs": needs,
        "calibration": {
            "samples": cal.samples,
            "sufficient": cal.sufficient,
            "brier": cal.brier,
            "brier_skill": cal.brier_skill,
        },
    }
