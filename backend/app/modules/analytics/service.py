"""Read-only analytics / dashboard read model (R27, T12).

Every value is derived from persisted sessions, attempts, reviews, mastery and grades, with
explicit denominators. Academic marks, practice-quiz scores and mastery estimates are
reported in separate sections and never mixed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import Clock, UtcDatetime
from app.core.errors import ValidationFailed
from app.modules.account.service import get_planning_preferences
from app.modules.grades import domain as gdomain
from app.modules.grades.models import AcademicProgram
from app.modules.grades.service import GradesService
from app.modules.learning import domain as ldomain
from app.modules.learning.models import Attempt, CardReview, Mastery, Quiz
from app.modules.planner.models import Assessment, Event, Plan, StudySession, Task
from app.modules.planner.plan_service import PlannerService


class Ratio(BaseModel):
    value: float | None
    numerator: int
    denominator: int


class AnalyticsOut(BaseModel):
    start_at: UtcDatetime
    end_at: UtcDatetime
    timezone: str
    subject_id: str | None
    study: dict
    per_day: list[dict]
    per_subject: list[dict]
    quizzes: dict
    flashcards: dict
    mastery: dict
    academic: list[dict]
    upcoming: dict
    plan: dict | None
    notes: list[str]


class ReadinessScoreOut(BaseModel):
    estimate: float | None = None
    low: float | None = None
    high: float | None = None
    coverage: float | None = None
    concepts: int = 0
    known: int = 0


class WeakConceptOut(BaseModel):
    concept_id: str
    score: float | None = None


class ReadinessAssessmentOut(BaseModel):
    assessment_id: str
    title: str | None = None
    deadline_at: str
    days_left: float
    scope_status: str
    readiness: ReadinessScoreOut
    planned_min_remaining: int
    studied_min: int
    weakest_concepts: list[WeakConceptOut]
    historical_exam_weighted: bool


class ReadinessOut(BaseModel):
    assessments: list[ReadinessAssessmentOut]
    notes: list[str]


class HeatmapDayOut(BaseModel):
    date: str
    study_min: int
    focus_min: int
    reviews: int
    evidence: int


class HeatmapRegularityOut(BaseModel):
    active_days: int
    days_in_range: int
    value: float


class HeatmapOut(BaseModel):
    timezone: str
    days: list[HeatmapDayOut]
    regularity: HeatmapRegularityOut


class MasteryCellOut(BaseModel):
    score: float | None = None
    status: str
    confidence: float
    next_due_at: str | None = None


class MasteryConceptRowOut(BaseModel):
    concept_id: str
    dimensions: dict[str, MasteryCellOut]


class MasteryMapOut(BaseModel):
    dimensions: list[str]
    concepts: list[MasteryConceptRowOut]
    note: str


def _ratio(num: int, den: int) -> Ratio:
    return Ratio(value=None if den == 0 else round(num / den, 4), numerator=num, denominator=den)


class AnalyticsService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def build(self, user_id: str, start: datetime, end: datetime, subject_id: str | None) -> AnalyticsOut:
        if end <= start:
            raise ValidationFailed("end must be after start")
        if end - start > timedelta(days=366):
            raise ValidationFailed("range is limited to 366 days")
        now = self.clock.now()
        tz = ZoneInfo(get_planning_preferences(self.db, user_id).timezone)

        # --- study sessions -----------------------------------------------------------
        q = select(StudySession).where(
            StudySession.user_id == user_id, StudySession.start_at >= start, StudySession.start_at < end,
            StudySession.status != "cancelled",
        )
        if subject_id:
            q = q.where(StudySession.subject_id == subject_id)
        sessions = list(self.db.scalars(q))
        status_counts = Counter(s.status for s in sessions)
        due = [s for s in sessions if s.end_at <= now]
        done = [s for s in due if s.status in ("completed", "partial")]
        per_day: dict = defaultdict(lambda: {"planned_min": 0, "actual_min": 0, "sessions": 0})
        per_subject: dict = defaultdict(lambda: {"planned_min": 0, "actual_min": 0, "sessions": 0})
        for s in sessions:
            for bucket in (per_day[s.start_at.astimezone(tz).date()], per_subject[s.subject_id]):
                bucket["planned_min"] += s.estimated_min
                bucket["actual_min"] += s.actual_min or 0
                bucket["sessions"] += 1
        planned_done = sum(s.estimated_min for s in done)
        study = {
            "planned_min": sum(s.estimated_min for s in sessions),
            "actual_min": sum(s.actual_min or 0 for s in sessions),
            "sessions": dict(sorted(status_counts.items())),
            "adherence": _ratio(len(done), len(due)).model_dump(),
            "time_accuracy": _ratio(sum(s.actual_min or 0 for s in done), planned_done).model_dump(),
        }

        # --- practice quizzes (not official marks) ---------------------------------------
        aq = select(Attempt, Quiz).join(Quiz, Quiz.id == Attempt.quiz_id).where(
            Attempt.user_id == user_id, Attempt.status.in_(("submitted", "expired")),
            Attempt.submitted_at >= start, Attempt.submitted_at < end,
        )
        if subject_id:
            aq = aq.where(Quiz.subject_id == subject_id)
        attempts = [a for a, _ in self.db.execute(aq)]
        earned = sum(a.score or 0 for a in attempts)
        possible = sum(a.max_score or 0 for a in attempts)
        quizzes = {
            "attempts": len(attempts),
            "score_ratio": {"value": None if possible == 0 else round(earned / possible, 4),
                            "earned_points": earned, "possible_points": possible},
            "pending_review_items": sum(a.pending_review for a in attempts),
        }

        # --- flashcards ------------------------------------------------------------------
        reviews = list(self.db.scalars(select(CardReview).where(
            CardReview.user_id == user_id, CardReview.reviewed_at >= start, CardReview.reviewed_at < end)))
        ratings = Counter(r.rating for r in reviews)
        flashcards = {
            "reviews": len(reviews),
            "ratings": {k: ratings.get(k, 0) for k in ldomain.RATINGS},
            "again_rate": _ratio(ratings.get("again", 0), len(reviews)).model_dump(),
        }

        # --- mastery estimates (current, not range-bound) --------------------------------
        mq = select(Mastery).where(Mastery.user_id == user_id)
        if subject_id:
            mq = mq.where(Mastery.subject_id == subject_id)
        rows = list(self.db.scalars(mq))
        by_status = Counter(ldomain.mastery_status(m.score, m.n_effective, m.next_due_at, now) for m in rows)
        known = [m.score for m in rows if m.score is not None]
        mastery = {
            "concept_dimensions": len(rows),
            "concepts": len({m.concept_id for m in rows}),
            "by_status": {k: by_status.get(k, 0) for k in ("unknown", "learning", "weak", "acquired", "consolidate")},
            "mean_known_score": None if not known else round(sum(known) / len(known), 4),
            "known_count": len(known),
            "reviews_overdue": sum(1 for m in rows if m.next_due_at is not None and m.next_due_at < now),
            "weakest": [
                {"concept_id": m.concept_id, "dimension": m.dimension, "score": round(m.score, 4)}
                for m in sorted((m for m in rows if m.score is not None), key=lambda m: (m.score, m.concept_id))[:5]
            ],
        }

        # --- academic results (official marks entered by the student) --------------------
        academic = []
        grades = GradesService(self.db, self.clock)
        for p in self.db.scalars(select(AcademicProgram).where(AcademicProgram.user_id == user_id)):
            try:
                summ = grades.summary(user_id, p.id, None)
            except Exception:  # empty or invalid curriculum: reported, not hidden
                academic.append({"program_id": p.id, "title": p.title, "status": "not_configured"})
                continue
            root = summ.nodes[0]
            academic.append({
                "program_id": p.id, "title": p.title, "provenance": p.provenance,
                "provisional": None if root.provisional is None else str(gdomain.q(root.provisional)),
                "coverage": str(gdomain.q(root.coverage)),
                "official_status": "unknown",
            })

        # --- upcoming ----------------------------------------------------------------------
        aq2 = (
            select(Assessment, Event).join(Event, Event.id == Assessment.event_id, isouter=True)
            .where(Assessment.user_id == user_id, Assessment.status == "active", Assessment.deadline_at > now)
            .order_by(Assessment.deadline_at).limit(5)
        )
        tasks = list(self.db.scalars(select(Task).where(
            Task.user_id == user_id, Task.status.in_(("todo", "in_progress")))))
        week = now + timedelta(days=7)
        upcoming = {
            "assessments": [
                {"assessment_id": a.id, "title": ev.title if ev else None, "deadline_at": a.deadline_at.isoformat(),
                 "scope_status": a.scope_status, "confirmation_required": bool(ev and ev.confirmation_required)}
                for a, ev in self.db.execute(aq2)
            ],
            "tasks_open": len(tasks),
            "tasks_overdue": sum(1 for t in tasks if t.deadline_at and t.deadline_at < now),
            "tasks_due_7d": sum(1 for t in tasks if t.deadline_at and now <= t.deadline_at < week),
            "remaining_task_min": sum(t.remaining_min or 0 for t in tasks),
        }

        # --- current plan ------------------------------------------------------------------
        plan_info = None
        plan = self.db.scalar(select(Plan).where(Plan.user_id == user_id, Plan.status == "accepted")
                              .order_by(Plan.accepted_at.desc()))
        if plan is not None:
            current_rev = PlannerService(self.db, self.clock).input_revision(user_id, plan.horizon_days)
            plan_info = {
                "plan_id": plan.id, "revision": plan.revision, "accepted_at": plan.accepted_at.isoformat()
                if plan.accepted_at else None,
                "inputs_changed_since_accept": current_rev != plan.input_revision,
            }

        return AnalyticsOut(
            start_at=start, end_at=end, timezone=str(tz), subject_id=subject_id, study=study,
            per_day=[{"date": d.isoformat(), **v} for d, v in sorted(per_day.items())],
            per_subject=[{"subject_id": k, **v} for k, v in sorted(per_subject.items(), key=lambda kv: str(kv[0]))],
            quizzes=quizzes, flashcards=flashcards, mastery=mastery, academic=academic,
            upcoming=upcoming, plan=plan_info,
            notes=[
                "Mastery values are pedagogical estimates, not grades.",
                "Practice quiz scores are not official marks.",
                "Official validation status is unknown until rules are verified.",
            ],
        )


# ---- T25 (P1) / T33 (P2): readiness, activity heatmap, mastery map -------------------------
def readiness_report(db: Session, clock: Clock, user_id: str) -> dict:
    from app.modules.learning.calibration import ConceptState, readiness
    from app.modules.learning.service import concept_states, exam_concept_frequency
    from app.modules.planner.models import AssessmentConcept

    now = clock.now()
    out = []
    rows = db.execute(
        select(Assessment, Event).join(Event, Event.id == Assessment.event_id, isouter=True)
        .where(Assessment.user_id == user_id, Assessment.status == "active", Assessment.deadline_at > now)
        .order_by(Assessment.deadline_at, Assessment.id)
    )
    for a, ev in rows:
        concepts = list(db.scalars(select(AssessmentConcept.concept_id).where(AssessmentConcept.assessment_id == a.id)
                                   .order_by(AssessmentConcept.concept_id)))
        freq = exam_concept_frequency(db, user_id, a.subject_id)
        states = concept_states(db, user_id, concepts)
        r = readiness([ConceptState(c, states[c][0], states[c][1], 1.0 + freq.get(c, 0.0)) for c in concepts])
        sessions = list(db.scalars(select(StudySession).where(
            StudySession.user_id == user_id, StudySession.assessment_id == a.id, StudySession.status != "cancelled")))
        out.append({
            "assessment_id": a.id, "title": ev.title if ev else None, "deadline_at": a.deadline_at.isoformat(),
            "days_left": round((a.deadline_at - now).total_seconds() / 86400, 2), "scope_status": a.scope_status,
            "readiness": r,
            "planned_min_remaining": sum(s.estimated_min for s in sessions if s.status == "planned" and s.start_at >= now),
            "studied_min": sum(s.actual_min or 0 for s in sessions if s.status in ("completed", "partial")),
            "weakest_concepts": sorted(
                [{"concept_id": c, "score": None if states[c][0] is None else round(states[c][0], 4)} for c in concepts],
                key=lambda x: (x["score"] is not None, x["score"] or 0))[:5],
            "historical_exam_weighted": bool(freq),
        })
    return {"assessments": out, "notes": [
        "Readiness = importance-weighted mean of the weakest known dimension per concept; unknown concepts reduce coverage.",
        "Band combines evidence-count uncertainty (Wilson) with the unassessed share. Not a grade prediction.",
    ]}


def heatmap_report(db: Session, clock: Clock, user_id: str, start: datetime, end: datetime) -> dict:
    if end <= start or end - start > timedelta(days=366):
        raise ValidationFailed("invalid range (max 366 days)")
    from app.modules.learning.models import Evidence
    from app.modules.workspace.models import FocusSession, WorkInterval

    tz = ZoneInfo(get_planning_preferences(db, user_id).timezone)
    days: dict = defaultdict(lambda: {"study_min": 0, "focus_min": 0, "reviews": 0, "evidence": 0})
    for s in db.scalars(select(StudySession).where(StudySession.user_id == user_id, StudySession.start_at >= start,
                                                   StudySession.start_at < end,
                                                   StudySession.status.in_(("completed", "partial")))):
        days[s.start_at.astimezone(tz).date()]["study_min"] += s.actual_min or 0
    for iv in db.scalars(select(WorkInterval).join(FocusSession, FocusSession.id == WorkInterval.focus_id).where(
            FocusSession.user_id == user_id, WorkInterval.kind == "work", WorkInterval.started_at >= start,
            WorkInterval.started_at < end, WorkInterval.ended_at.is_not(None))):
        days[iv.started_at.astimezone(tz).date()]["focus_min"] += int((iv.ended_at - iv.started_at).total_seconds() // 60)
    for r in db.scalars(select(CardReview).where(CardReview.user_id == user_id, CardReview.reviewed_at >= start,
                                                 CardReview.reviewed_at < end)):
        days[r.reviewed_at.astimezone(tz).date()]["reviews"] += 1
    for e in db.scalars(select(Evidence).where(Evidence.user_id == user_id, Evidence.occurred_at >= start,
                                               Evidence.occurred_at < end, Evidence.derived_from.is_(None))):
        days[e.occurred_at.astimezone(tz).date()]["evidence"] += 1
    ordered = [{"date": d.isoformat(), **v} for d, v in sorted(days.items())]
    active = sum(1 for d in ordered if d["study_min"] or d["focus_min"] or d["reviews"])
    span = (end.astimezone(tz).date() - start.astimezone(tz).date()).days or 1
    return {"timezone": str(tz), "days": ordered,
            "regularity": {"active_days": active, "days_in_range": span, "value": round(active / span, 4)}}


def mastery_map(db: Session, clock: Clock, user_id: str, subject_id: str | None) -> dict:
    now = clock.now()
    q = select(Mastery).where(Mastery.user_id == user_id)
    if subject_id:
        q = q.where(Mastery.subject_id == subject_id)
    concepts: dict = defaultdict(dict)
    for m in db.scalars(q.order_by(Mastery.concept_id, Mastery.dimension)):
        concepts[m.concept_id][m.dimension] = {
            "score": None if m.score is None else round(m.score, 4),
            "status": ldomain.mastery_status(m.score, m.n_effective, m.next_due_at, now),
            "confidence": round(ldomain.confidence(m.n_effective), 4),
            "next_due_at": m.next_due_at.isoformat() if m.next_due_at else None,
        }
    return {"dimensions": list(ldomain.DIMENSIONS),
            "concepts": [{"concept_id": c, "dimensions": d} for c, d in sorted(concepts.items())],
            "note": "Missing cells = no objective evidence yet (unknown, not zero)."}
