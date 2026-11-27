"""Smart Planner use cases: preview → accept, replan with diff, session move/lock/start/finish.

Inputs are read through query interfaces (account preferences, learning study needs and
mastery, grades academic signals). Scheduling itself is the pure ``scheduling`` module;
no provider/AI call happens here and sessions only carry a ``generation_spec``.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.common import Clock, check_revision
from app.core.db import new_id
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.events import record_event
from app.modules.account.service import get_planning_preferences, get_self_assessment
from app.modules.grades.academic_service import academic_diagnostic_needs
from app.modules.grades.service import academic_signals_by_subject, grades_revision
from app.modules.learning.service import (
    LearningService,
    concept_mastery,
    exam_concept_frequency,
    learning_revision,
    prerequisite_edges,
)
from app.modules.planner import cpsat, scheduling
from app.modules.planner.domain import Interval, local_day_interval
from app.modules.planner.models import (
    Assessment,
    AssessmentConcept,
    Event,
    Plan,
    StudySession,
    Task,
)
from app.modules.planner.schemas import (
    AcceptRequest,
    PlanDiffOut,
    PlanOut,
    PlanPreviewOut,
    PreviewSessionOut,
    ReplanRequest,
    SessionFinish,
    SessionOut,
    SessionPatch,
)
from app.modules.planner.service import AgendaService
from app.modules.workspace.service import recurring_error_needs

KEPT_STATUSES = ("in_progress", "completed", "partial")
DONE_STATUSES = ("completed", "partial")
ASSESSMENT_LOOKAHEAD = timedelta(days=14)
# Heuristic preparation estimates (minutes) — labeled assumptions, editable later.
NO_SCOPE_ESTIMATE = {"easy": 120, "medium": 180, "hard": 300, None: 180}
CONCEPT_ESTIMATE = {"unknown": 60, "learning": 60, "weak": 45, "acquired": 25, "consolidate": 25}
CONCEPT_ACTIVITY = {
    "unknown": "discovery",
    "learning": "practice",
    "weak": "practice",
    "acquired": "active_recall",
    "consolidate": "active_recall",
}
IMPORTANCE = {"easy": 0.6, "medium": 0.75, "hard": 0.9, None: 0.75}
ARTIFACT_FOR_ACTIVITY = {
    "discovery": "revision_sheet",
    "revision": "revision_sheet",
    "practice": "quiz",
    "active_recall": "flashcards",
    "flashcards": "flashcards",
    "task_work": None,
    "diagnostic": "quiz",
}
OBJECTIVES = {
    "discovery": ("Découvrir et comprendre les notions clés", "Expliquer la notion avec ses propres mots"),
    "revision": ("Réviser le périmètre de l'évaluation", "Refaire un exercice type sans aide"),
    "practice": ("S'entraîner sur des exercices ciblés", "Réussir les exercices sans aide"),
    "active_recall": ("Rappel actif des définitions et formules", "Restituer sans support puis vérifier"),
    "flashcards": ("Réviser les cartes dues", "Terminer la pile de cartes dues"),
    "task_work": ("Avancer la tâche", "Réduire le temps restant estimé"),
    "diagnostic": ("Diagnostiquer les lacunes révélées par la note", "Identifier les concepts à retravailler"),
}
DIAGNOSTIC_WINDOW_DAYS = 7
# Learned duration correction (SMART_PLANNER § Dynamic replan): bounded EMA of actual/estimated.
ESTIMATE_ALPHA = 0.3
ESTIMATE_MIN_OBS = 3
ESTIMATE_BOUNDS = (0.5, 2.0)
DONE_LIKE = ("acquired", "consolidate")


class PlannerService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock
        self.agenda = AgendaService(db, clock)

    # ================================================================== helpers ====
    def _owned_session(self, user_id: str, session_id: str) -> StudySession:
        s = self.db.get(StudySession, session_id)
        if s is None or s.user_id != user_id:
            raise NotFound("Session not found")
        return s

    def _current_plan(self, user_id: str) -> Plan | None:
        return self.db.scalar(
            select(Plan).where(Plan.user_id == user_id, Plan.status == "accepted").order_by(Plan.accepted_at.desc())
        )

    def input_revision(self, user_id: str, horizon_days: int) -> str:
        prefs = get_planning_preferences(self.db, user_id)
        return (
            f"agenda:{self.agenda.agenda_revision(user_id)};prefs:{prefs.revision};"
            f"learning:{learning_revision(self.db, user_id)};grades:{grades_revision(self.db, user_id)};"
            f"h:{horizon_days}"
        )

    def _done_minutes(self, user_id: str) -> dict[str, int]:
        rows = self.db.execute(
            select(StudySession.need_key, func.coalesce(func.sum(StudySession.actual_min), 0))
            .where(StudySession.user_id == user_id, StudySession.status.in_(DONE_STATUSES))
            .group_by(StudySession.need_key)
        )
        return {k: int(v) for k, v in rows}

    @staticmethod
    def session_out(s: StudySession) -> SessionOut:
        return SessionOut(
            id=s.id, plan_id=s.plan_id, need_key=s.need_key, kind=s.kind, title=s.title,
            task_id=s.task_id, assessment_id=s.assessment_id, subject_id=s.subject_id,
            concept_ids=s.concept_ids, activity_type=s.activity_type, start_at=s.start_at, end_at=s.end_at,
            deadline_at=s.deadline_at, estimated_min=s.estimated_min, actual_min=s.actual_min,
            status=s.status, locked=s.locked, priority=s.priority, reason=s.reason,  # type: ignore[arg-type]
            objective=s.objective, success_criteria=s.success_criteria, generation_spec=s.generation_spec,
            started_at=s.started_at, finished_at=s.finished_at, reflection=s.reflection, revision=s.revision,
        )

    def _touch(self, user_id: str, s: StudySession | None = None, event: str = "SessionChanged") -> None:
        """Session changes are planner inputs: bump agenda + plan revisions."""
        self.agenda.bump_revision(user_id)
        plan = self._current_plan(user_id)
        if plan is not None:
            plan.revision += 1
        if s is not None:
            record_event(self.db, user_id=user_id, type=event, entity_id=s.id, revision=s.revision)

    # ============================================================ needs & inputs ====
    def _build_needs(self, user_id: str, now: datetime, horizon: Interval) -> tuple[list[scheduling.Need], list[str]]:
        warnings: list[str] = []
        needs: list[scheduling.Need] = []
        done = self._done_minutes(user_id)
        edges = prerequisite_edges(self.db, user_id)
        declared = get_self_assessment(self.db, user_id)

        def prior_gap(subject_id: str | None, concept_id: str | None) -> float | None:
            """Unknown mastery: declared confidence shifts the 0.5 ranking prior (R01), never mastery."""
            level = declared.get(concept_id or "") or declared.get(subject_id or "")
            return None if level is None else 0.5 + (3 - level) * 0.1
        signals = academic_signals_by_subject(self.db, user_id)

        def academic(subject_id: str | None) -> float | None:
            sig = signals.get(subject_id) if subject_id else None
            return sig.score if sig else None

        def reason(base: str, subject_id: str | None) -> str:
            sig = signals.get(subject_id) if subject_id else None
            if sig is None:
                return base
            extra = f"ACADEMIC_SIGNAL(score={sig.score:.2f},{sig.feasibility}"
            extra += f",required={sig.required_mean})" if sig.required_mean else ")"
            return f"{base};{extra}"

        # Tasks (leaf tasks only: a parent is covered by its subtasks).
        tasks = list(self.db.scalars(
            select(Task).where(Task.user_id == user_id, Task.status.in_(("todo", "in_progress"))).order_by(Task.id)
        ))
        parents = {t.parent_task_id for t in self.db.scalars(select(Task).where(Task.user_id == user_id)) if t.parent_task_id}
        for t in tasks:
            if t.id in parents:
                continue
            if t.remaining_min is None:
                warnings.append(f"TASK_WITHOUT_ESTIMATE:{t.id}")
                continue
            if t.remaining_min <= 0:
                continue
            needs.append(scheduling.Need(
                key=f"task:{t.id}", kind="task", title=t.title, activity_type="task_work",
                remaining_min=t.remaining_min, earliest=None, deadline=t.deadline_at, splittable=t.splittable,
                importance=t.priority / 5, academic=academic(t.subject_id), subject_id=t.subject_id, task_id=t.id,
                reason=reason("TASK_REMAINING_WORK", t.subject_id),
                objective=OBJECTIVES["task_work"][0], success_criteria=OBJECTIVES["task_work"][1],
                report_unscheduled=t.deadline_at is not None and t.deadline_at <= horizon.end,
            ))

        # Assessments with confirmed scope.
        rows = self.db.execute(
            select(Assessment, Event).join(Event, Event.id == Assessment.event_id, isouter=True).where(
                Assessment.user_id == user_id, Assessment.status == "active",
                Assessment.deadline_at > now, Assessment.deadline_at <= horizon.end + ASSESSMENT_LOOKAHEAD,
            ).order_by(Assessment.deadline_at, Assessment.id)
        )
        for a, ev in rows:
            label = ev.title if ev else "Évaluation"
            if ev is not None and ev.confirmation_required:
                warnings.append(f"ASSESSMENT_SLOT_UNCONFIRMED:{a.id}")
                continue
            if a.scope_status != "confirmed":
                warnings.append(f"SCOPE_UNCONFIRMED:{a.id}")
                continue
            report = a.deadline_at <= horizon.end
            concept_ids = list(self.db.scalars(
                select(AssessmentConcept.concept_id).where(AssessmentConcept.assessment_id == a.id)
                .order_by(AssessmentConcept.concept_id)
            ))
            if not concept_ids:
                key = f"assessment:{a.id}"
                minutes = NO_SCOPE_ESTIMATE.get(a.difficulty, 180) - done.get(key, 0)
                warnings.append(f"ESTIMATE_HEURISTIC:{a.id}")
                needs.append(scheduling.Need(
                    key=key, kind="assessment", title=f"Préparer : {label}", activity_type="revision",
                    remaining_min=max(0, minutes), earliest=None, deadline=a.deadline_at,
                    importance=IMPORTANCE.get(a.difficulty, 0.75), academic=academic(a.subject_id),
                    subject_id=a.subject_id, assessment_id=a.id, reason=reason("ASSESSMENT_PREPARATION", a.subject_id),
                    objective=OBJECTIVES["revision"][0], success_criteria=OBJECTIVES["revision"][1],
                    report_unscheduled=report,
                ))
                continue
            mastery = concept_mastery(self.db, user_id, concept_ids, now)
            freq = exam_concept_frequency(self.db, user_id, a.subject_id)
            pre_scope = {p for p, d in edges if d in concept_ids and p not in concept_ids}
            pre_mastery = concept_mastery(self.db, user_id, sorted(pre_scope), now) if pre_scope else {}
            for cid in concept_ids:
                m = mastery[cid]
                key = f"assessment:{a.id}:{cid}"
                activity = CONCEPT_ACTIVITY[m.status]
                after = tuple(
                    f"assessment:{a.id}:{p}" for p, d in sorted(edges)
                    if d == cid and p in concept_ids and mastery[p].status not in DONE_LIKE
                )
                for p, d in sorted(edges):
                    if d == cid and p in pre_mastery and pre_mastery[p].status not in DONE_LIKE:
                        warnings.append(f"PREREQUISITE_OUT_OF_SCOPE:{p}->{cid}")
                importance = IMPORTANCE.get(a.difficulty, 0.75)
                base_reason = f"ASSESSMENT_CONCEPT_{m.status.upper()}"
                if freq:
                    importance = min(1.0, importance * (0.8 + 0.4 * freq.get(cid, 0.0)))
                    base_reason += f";HISTORICAL_EXAM_FREQ={freq.get(cid, 0.0):.2f}"
                if after:
                    base_reason += ";AFTER_PREREQUISITES"
                needs.append(scheduling.Need(
                    key=key, kind="assessment", title=f"{label} — {cid}", activity_type=activity,
                    remaining_min=max(0, CONCEPT_ESTIMATE[m.status] - done.get(key, 0)), earliest=None,
                    deadline=a.deadline_at, importance=importance,
                    gap=prior_gap(a.subject_id, cid) if m.score is None else 1 - m.score,
                    academic=academic(a.subject_id), after=after,
                    subject_id=a.subject_id, assessment_id=a.id, concept_ids=(cid,),
                    reason=reason(base_reason, a.subject_id),
                    objective=OBJECTIVES[activity][0], success_criteria=OBJECTIVES[activity][1],
                    report_unscheduled=report,
                ))

        # Adaptive learning needs (reviews due, weak concepts, flashcards).
        for n in LearningService(self.db, self.clock).study_needs(user_id, horizon.end):
            remaining = n.estimated_min - done.get(n.need_id, 0)
            if remaining <= 0:
                continue
            activity = n.activity_type if n.activity_type in OBJECTIVES else "practice"
            needs.append(scheduling.Need(
                key=n.need_id, kind=n.kind, title=f"{n.kind} — {', '.join(n.concept_ids) or n.subject_id or 'cartes'}",
                activity_type=activity, remaining_min=remaining, earliest=n.earliest_at, deadline=n.due_at,
                importance=0.5, gap=n.priority_factors.get("gap"), forgetting=n.priority_factors.get("forgetting", 0.0),
                academic=academic(n.subject_id), subject_id=n.subject_id, concept_ids=n.concept_ids,
                reason=reason(n.reason, n.subject_id), objective=OBJECTIVES[activity][0],
                success_criteria=n.success_criteria,
                report_unscheduled=n.due_at is not None and n.due_at <= horizon.end,
            ))
        # Grade-driven diagnostics and targeted remediation (T45 / R42).
        for d in academic_diagnostic_needs(self.db, user_id, now):
            remaining = d.estimated_min - done.get(d.key, 0)
            if remaining <= 0:
                continue
            activity = d.activity_type if d.activity_type in OBJECTIVES else "practice"
            needs.append(scheduling.Need(
                key=d.key, kind="diagnostic", title=d.title, activity_type=activity, remaining_min=remaining,
                earliest=None, deadline=now + timedelta(days=DIAGNOSTIC_WINDOW_DAYS), importance=0.7,
                academic=academic(d.subject_id), subject_id=d.subject_id, concept_ids=d.concept_ids,
                reason=reason(d.reason, d.subject_id), objective=OBJECTIVES[activity][0],
                success_criteria=OBJECTIVES[activity][1],
            ))
        # Recurring errors from the error notebook -> consolidation.
        for e in recurring_error_needs(self.db, user_id):
            remaining = e.estimated_min - done.get(e.key, 0)
            if remaining <= 0:
                continue
            needs.append(scheduling.Need(
                key=e.key, kind="consolidation", title=e.title, activity_type="practice", remaining_min=remaining,
                earliest=None, deadline=now + timedelta(days=DIAGNOSTIC_WINDOW_DAYS), importance=0.6,
                gap=0.6, academic=academic(e.subject_id), subject_id=e.subject_id, concept_ids=e.concept_ids,
                reason=reason("RECURRING_ERROR", e.subject_id), objective="Corriger une erreur récurrente",
                success_criteria="Refaire sans aide l'exercice raté",
            ))
        factors = self._estimate_factors(user_id)
        adjusted = []
        for n in needs:
            f = factors.get(n.activity_type)
            if f is not None and n.kind != "task" and n.remaining_min > 0:
                adjusted.append(replace(n, remaining_min=max(1, round(n.remaining_min * f))))
            else:
                adjusted.append(n)
        warnings += [f"ESTIMATE_ADJUSTED:{a}x{f:.2f}" for a, f in sorted(factors.items())]
        return adjusted, warnings

    def _estimate_factors(self, user_id: str) -> dict[str, float]:
        """actual/estimated EMA per activity type over completed sessions (breaks excluded by
        construction: actual_min comes from focus work intervals or the student's report)."""
        rows = self.db.execute(
            select(StudySession.activity_type, StudySession.actual_min, StudySession.estimated_min)
            .where(StudySession.user_id == user_id, StudySession.status == "completed",
                   StudySession.actual_min.is_not(None), StudySession.estimated_min > 0)
            .order_by(StudySession.finished_at, StudySession.id)
        )
        series: dict[str, list[float]] = defaultdict(list)
        for activity, actual, estimated in rows:
            series[activity].append(actual / estimated)
        out = {}
        for activity, ratios in series.items():
            if len(ratios) < ESTIMATE_MIN_OBS:
                continue
            ema = ratios[0]
            for r in ratios[1:]:
                ema = (1 - ESTIMATE_ALPHA) * ema + ESTIMATE_ALPHA * r
            out[activity] = round(min(ESTIMATE_BOUNDS[1], max(ESTIMATE_BOUNDS[0], ema)), 2)
        return out

    def _existing(self, user_id: str, now: datetime, tz: ZoneInfo):
        today = local_day_interval(now.astimezone(tz).date(), tz).start
        sessions = list(self.db.scalars(
            select(StudySession).where(
                StudySession.user_id == user_id, StudySession.status.notin_(("cancelled", "skipped")),
                StudySession.end_at > today - timedelta(days=7),
            ).order_by(StudySession.start_at, StudySession.id)
        ))
        kept, previous, movable, missed = [], defaultdict(list), [], []
        for s in sessions:
            iv = Interval(s.start_at, s.end_at)
            if s.status in KEPT_STATUSES:
                if s.end_at > today:
                    counted = s.actual_min if s.status in DONE_STATUSES and s.actual_min is not None else iv.minutes
                    kept.append((s, scheduling.Kept(s.id, iv, counted, s.locked)))
            elif s.status == "planned":
                if s.start_at < now:
                    missed.append(s)  # never started: its work is rescheduled
                elif s.locked:
                    kept.append((s, scheduling.Kept(s.id, iv, iv.minutes, True)))
                else:
                    previous[s.need_key].append(iv)
                    movable.append(s)
        return kept, dict(previous), movable, missed

    # ================================================================ preview ====
    def preview(
        self, user_id: str, horizon_days: int, reason: str | None = None, algorithm: str = "auto"
    ) -> PlanPreviewOut:
        now = self.clock.now()
        prefs = get_planning_preferences(self.db, user_id)
        tz = ZoneInfo(prefs.timezone)
        horizon = Interval(now, now + timedelta(days=horizon_days))
        snap, _, pending = self.agenda.availability_snapshot(user_id, horizon.start, horizon.end)
        needs, warnings = self._build_needs(user_id, now, horizon)
        kept_pairs, previous, movable, missed = self._existing(user_id, now, tz)
        kept = [k for _, k in kept_pairs]
        # Locked / in-progress sessions already reserve work for their need.
        reserved: dict[str, int] = defaultdict(int)
        for s, k in kept_pairs:
            if s.status in ("planned", "in_progress"):
                reserved[s.need_key] += k.counted_min
        needs = [replace(n, remaining_min=max(0, n.remaining_min - reserved[n.key])) for n in needs]
        limits =scheduling.Limits(prefs.timezone, prefs.daily_max_min, prefs.weekly_max_min,
                                   prefs.min_session_min, prefs.max_session_min)
        # First slot on the next 5-minute boundary (>= 1 min ahead) so a preview accepted a
        # few seconds later does not already contain a session that started in the past.
        start_at = _ceil_minutes(now + timedelta(minutes=1), 5)
        result = scheduling.schedule(needs, snap.free, kept, limits, start_at, horizon, previous)
        violations = scheduling.check_invariants(result, needs, snap.free, kept, limits, start_at)
        if violations:  # pragma: no cover - defensive: the greedy placement must never violate
            raise RuntimeError(f"planner invariant violated: {violations}")
        algorithm_version = scheduling.ALGORITHM_VERSION
        chosen = prefs.planner_algorithm if algorithm == "auto" else algorithm
        if chosen == "cpsat":
            if not cpsat.available():
                warnings.append("CPSAT_UNAVAILABLE_FALLBACK_GREEDY")
            else:
                alt, status = cpsat.schedule_cpsat(needs, snap.free, kept, limits, start_at, horizon, previous)
                ok = alt is not None and not scheduling.check_invariants(alt, needs, snap.free, kept, limits, start_at)
                if ok and cpsat.weighted_coverage(alt) >= cpsat.weighted_coverage(result):  # type: ignore[arg-type]
                    warnings.append(f"SOLVER:{status}")
                    if status != "OPTIMAL":
                        warnings.append("SOLVER_TIME_LIMIT_NOT_PROVEN_OPTIMAL")
                    result, algorithm_version = alt, cpsat.ALGORITHM_VERSION  # type: ignore[assignment]
                else:
                    warnings.append(f"SOLVER:{status};FALLBACK_GREEDY_BASELINE")

        warnings += snap.warnings
        if pending:
            warnings.append(f"PENDING_CONFIRMATIONS:{pending}")
        if missed:
            warnings.append(f"MISSED_SESSIONS:{len(missed)}")
        busy = [b.interval for b in snap.busy]
        for s, k in kept_pairs:
            if k.locked and any(k.interval.overlaps(b) for b in busy):
                warnings.append(f"LOCK_CONFLICT:{s.id}")
        if not needs:
            warnings.append("NOTHING_TO_PLAN")

        by_key = {n.key: n for n in needs}
        sessions = []
        for p in result.placed:
            n = by_key[p.need_key]
            artifact = ARTIFACT_FOR_ACTIVITY.get(n.activity_type)
            sessions.append(PreviewSessionOut(
                id=new_id(), need_key=n.key, kind=n.kind, title=n.title, task_id=n.task_id,
                assessment_id=n.assessment_id, subject_id=n.subject_id, concept_ids=list(n.concept_ids),
                activity_type=n.activity_type, start_at=p.interval.start, end_at=p.interval.end,
                estimated_min=p.minutes, priority=round(p.priority, 4), reason=n.reason,
                objective=n.objective, success_criteria=n.success_criteria,
                generation_spec=None if artifact is None else {
                    "artifact_type": artifact, "subject_id": n.subject_id, "concept_ids": list(n.concept_ids),
                    "duration_min": p.minutes, "objective": n.objective, "generate_on": "session_start",
                },
            ))
        reported = [u for u in result.unscheduled if u.reason != "AFTER_HORIZON"]
        deferred = sum(u.remaining_min for u in result.unscheduled if u.reason == "AFTER_HORIZON")
        if reported:
            warnings.append("INSUFFICIENT_CAPACITY" if any(u.reason == "CAPACITY" for u in reported) else "UNSCHEDULED_WORK")
        diff = self._diff(movable, sessions)
        input_revision = self.input_revision(user_id, horizon_days)

        out = PlanPreviewOut(
            preview_id=new_id(), input_revision=input_revision, algorithm_version=algorithm_version,
            horizon_start=horizon.start, horizon_end=horizon.end, sessions=sessions,
            kept_sessions=[self.session_out(s) for s, _ in kept_pairs],
            unscheduled=[
                {"need_id": u.need_key, "title": by_key[u.need_key].title, "remaining_min": u.remaining_min,
                 "reason": u.reason} for u in reported
            ],
            warnings=sorted(set(warnings)),
            summary={
                "scheduled_min": sum(p.minutes for p in result.placed),
                "unscheduled_min": sum(u.remaining_min for u in reported),
                "deferred_min": deferred,
                "kept_min": sum(k.counted_min for k in kept),
                "per_day": [{"date": d, "planned_min": m, "daily_max_min": prefs.daily_max_min}
                            for d, m in result.day_load.items()],
            },
            diff=diff, reason=reason,
        )
        for old in self.db.scalars(select(Plan).where(Plan.user_id == user_id, Plan.status == "preview")):
            old.status = "discarded"
        self.db.add(Plan(
            id=out.preview_id, user_id=user_id, status="preview", horizon_start=horizon.start,
            horizon_end=horizon.end, horizon_days=horizon_days, input_revision=input_revision,
            algorithm_version=algorithm_version, preview=out.model_dump(mode="json"),
            warnings=out.warnings, reason=reason,
        ))
        self.db.flush()
        return out

    @staticmethod
    def _diff(movable: list[StudySession], sessions: list[PreviewSessionOut]) -> PlanDiffOut:
        old_by_key: dict[str, list[StudySession]] = defaultdict(list)
        for s in movable:
            old_by_key[s.need_key].append(s)
        new_by_key: dict[str, list[PreviewSessionOut]] = defaultdict(list)
        for s in sessions:
            new_by_key[s.need_key].append(s)
        added, moved, removed, unchanged = [], [], [], 0
        for key in sorted(set(old_by_key) | set(new_by_key)):
            olds, news = old_by_key.get(key, []), new_by_key.get(key, [])
            same = {(o.start_at, o.end_at) for o in olds} & {(n.start_at, n.end_at) for n in news}
            olds = [o for o in olds if (o.start_at, o.end_at) not in same]
            news = [n for n in news if (n.start_at, n.end_at) not in same]
            unchanged += len(same)
            for o, n in zip(olds, news, strict=False):
                moved.append({"session_id": o.id, "need_key": key, "from_start": o.start_at, "from_end": o.end_at,
                              "to_start": n.start_at, "to_end": n.end_at})
            for o in olds[len(news):]:
                removed.append({"session_id": o.id, "need_key": key, "start_at": o.start_at, "end_at": o.end_at})
            for n in news[len(olds):]:
                added.append({"session_id": None, "need_key": key, "start_at": n.start_at, "end_at": n.end_at})
        return PlanDiffOut(added=added, moved=moved, removed=removed, unchanged=unchanged)

    # ================================================================= accept ====
    def accept(self, user_id: str, body: AcceptRequest) -> PlanOut:
        plan = self.db.get(Plan, body.preview_id)
        if plan is None or plan.user_id != user_id:
            raise NotFound("Preview not found")
        if plan.status != "preview":
            raise Conflict(f"Preview is {plan.status}", code="preview_not_active")
        current_rev = self.input_revision(user_id, plan.horizon_days)
        if body.input_revision != plan.input_revision or current_rev != plan.input_revision:
            raise Conflict("Plan inputs changed since the preview; request a new preview", code="stale_preview",
                           details={"preview_revision": plan.input_revision, "current_revision": current_rev})
        now = self.clock.now()
        sessions = plan.preview.get("sessions", [])
        if any(datetime.fromisoformat(s["start_at"]) < now for s in sessions):
            raise Conflict("Preview contains sessions in the past; request a new preview", code="preview_expired")

        prefs = get_planning_preferences(self.db, user_id)
        _, _, movable, missed = self._existing(user_id, now, ZoneInfo(prefs.timezone))
        for s in movable:  # unstarted, unlocked, future: replaced by the new plan
            self.db.delete(s)
        for s in missed:
            s.status, s.reflection = "skipped", {"missed": True}
            s.revision += 1
        old = self._current_plan(user_id)
        if old is not None:
            old.status = "superseded"
            for s in self.db.scalars(select(StudySession).where(
                StudySession.plan_id == old.id, StudySession.status.in_(("planned", *KEPT_STATUSES))
            )):
                s.plan_id = plan.id  # kept sessions (locked / in progress / done) carry over
        self.db.flush()
        for s in sessions:
            self.db.add(StudySession(
                id=s["id"], user_id=user_id, plan_id=plan.id, need_key=s["need_key"], kind=s["kind"],
                title=s["title"], task_id=s["task_id"], assessment_id=s["assessment_id"], subject_id=s["subject_id"],
                concept_ids=s["concept_ids"], activity_type=s["activity_type"],
                start_at=datetime.fromisoformat(s["start_at"]), end_at=datetime.fromisoformat(s["end_at"]),
                deadline_at=self._deadline_for(s), estimated_min=s["estimated_min"], priority=s["priority"],
                reason=s["reason"], objective=s["objective"], success_criteria=s["success_criteria"],
                generation_spec=s["generation_spec"],
            ))
        plan.status, plan.accepted_at = "accepted", now
        self.agenda.bump_revision(user_id)
        self.db.flush()
        # The accepted plan's baseline is the state *after* acceptance (its own sessions included),
        # so /planner/status only reports later changes.
        plan.input_revision = self.input_revision(user_id, plan.horizon_days)
        record_event(self.db, user_id=user_id, type="PlanAccepted", entity_id=plan.id, revision=plan.revision,
                     payload={"sessions": len(sessions)})
        self.db.flush()
        return self.current(user_id)

    def _deadline_for(self, s: dict) -> datetime | None:
        if s.get("task_id"):
            t = self.db.get(Task, s["task_id"])
            return t.deadline_at if t else None
        if s.get("assessment_id"):
            a = self.db.get(Assessment, s["assessment_id"])
            return a.deadline_at if a else None
        return None

    def status(self, user_id: str) -> dict:
        """T45: is the accepted plan outdated, and by which inputs (grades, agenda, ...)?"""
        plan = self._current_plan(user_id)
        if plan is None:
            return {"has_plan": False, "dirty": True, "changed_inputs": [], "suggested_reason": "manual"}
        current = self.input_revision(user_id, plan.horizon_days)
        before = dict(part.split(":", 1) for part in plan.input_revision.split(";"))
        after = dict(part.split(":", 1) for part in current.split(";"))
        changed = sorted(k for k in after if before.get(k) != after[k])
        reason_for = {"grades": "grades_changed", "learning": "mastery_changed", "prefs": "preferences_changed",
                      "agenda": "agenda_changed"}
        suggested = next((reason_for[k] for k in ("grades", "learning", "agenda", "prefs") if k in changed), None)
        return {
            "has_plan": True, "plan_id": plan.id, "plan_revision": plan.revision, "dirty": bool(changed),
            "changed_inputs": changed, "suggested_reason": suggested,
            "accepted_input_revision": plan.input_revision, "current_input_revision": current,
        }

    def current(self, user_id: str) -> PlanOut:
        plan = self._current_plan(user_id)
        if plan is None:
            raise NotFound("No accepted plan yet", code="no_plan")
        sessions = self.db.scalars(
            select(StudySession).where(StudySession.user_id == user_id, StudySession.plan_id == plan.id,
                                       StudySession.status != "cancelled")
            .order_by(StudySession.start_at, StudySession.id)
        )
        return PlanOut(
            id=plan.id, status=plan.status, horizon_start=plan.horizon_start, horizon_end=plan.horizon_end,
            input_revision=plan.input_revision, accepted_at=plan.accepted_at, warnings=plan.warnings,
            revision=plan.revision, sessions=[self.session_out(s) for s in sessions],
        )

    def replan(self, user_id: str, body: ReplanRequest) -> PlanPreviewOut:
        plan = self._current_plan(user_id)
        if plan is None:
            raise Conflict("No accepted plan to replan; request a preview", code="no_plan")
        check_revision(plan.revision, body.base_plan_revision)
        return self.preview(user_id, body.horizon_days, reason=body.reason, algorithm=body.algorithm)

    # =============================================================== sessions ====
    def patch_session(self, user_id: str, session_id: str, body: SessionPatch) -> SessionOut:
        s = self._owned_session(user_id, session_id)
        check_revision(s.revision, body.expected_revision)
        fields = body.model_fields_set - {"expected_revision"}
        if s.status != "planned":
            raise Conflict("Only planned sessions can be moved or locked", code="session_not_planned")
        if fields & {"start_at", "end_at"}:
            start = body.start_at or s.start_at
            end = body.end_at or s.end_at
            self._validate_move(user_id, s, start, end)
            s.start_at, s.end_at = start, end
            s.estimated_min = int((end - start).total_seconds() // 60)
            # A manual move is a user decision: lock it unless explicitly unlocked.
            s.locked = True if body.locked is None else body.locked
        elif body.locked is not None:
            s.locked = body.locked
        s.revision += 1
        self._touch(user_id, s)
        return self.session_out(s)

    def _validate_move(self, user_id: str, s: StudySession, start: datetime, end: datetime) -> None:
        prefs = get_planning_preferences(self.db, user_id)
        tz = ZoneInfo(prefs.timezone)
        now = self.clock.now()
        minutes = int((end - start).total_seconds() // 60)
        errors = []
        if end <= start:
            raise ValidationFailed("end_at must be after start_at")
        if start < now:
            errors.append({"code": "PAST", "message": "cannot move a session into the past"})
        if not prefs.min_session_min <= minutes <= prefs.max_session_min:
            errors.append({"code": "SESSION_LENGTH", "message": "outside min/max session length"})
        if s.deadline_at is not None and end > s.deadline_at:
            errors.append({"code": "AFTER_DEADLINE", "message": "session ends after its deadline"})
        day = start.astimezone(tz).date()
        day_iv = local_day_interval(day, tz)
        if end > day_iv.end:
            errors.append({"code": "CROSSES_MIDNIGHT", "message": "session must stay within one day"})
        snap, _, _ = self.agenda.availability_snapshot(user_id, day_iv.start, day_iv.end)
        if not any(f.start <= start and end <= f.end for f in snap.free):
            errors.append({"code": "OUTSIDE_FREE_TIME", "message": "not inside a free work window"})
        others = list(self.db.scalars(select(StudySession).where(
            StudySession.user_id == user_id, StudySession.id != s.id,
            StudySession.status.in_(("planned", *KEPT_STATUSES)),
            StudySession.start_at < day_iv.end, StudySession.end_at > day_iv.start,
        )))
        if any(o.start_at < end and start < o.end_at for o in others):
            errors.append({"code": "OVERLAP", "message": "overlaps another session"})
        load = sum((o.actual_min if o.status in DONE_STATUSES and o.actual_min is not None else o.estimated_min)
                   for o in others) + minutes
        if load > prefs.daily_max_min:
            errors.append({"code": "DAILY_CAP", "message": "daily study limit exceeded"})
        if errors:
            raise ValidationFailed("Session move violates a hard constraint", details=errors, code="constraint_violation")

    def start_session(self, user_id: str, session_id: str) -> SessionOut:
        s = self._owned_session(user_id, session_id)
        if s.status != "planned":
            raise Conflict(f"Session is {s.status}", code="session_not_planned")
        running = self.db.scalar(select(StudySession).where(
            StudySession.user_id == user_id, StudySession.status == "in_progress"))
        if running is not None:
            raise Conflict("Another session is already in progress", code="session_in_progress",
                           details={"session_id": running.id})
        s.status, s.started_at = "in_progress", self.clock.now()
        s.revision += 1
        self._touch(user_id, s, "SessionStarted")
        return self.session_out(s)

    def finish_session(self, user_id: str, session_id: str, body: SessionFinish) -> SessionOut:
        s = self._owned_session(user_id, session_id)
        now = self.clock.now()
        if s.status in ("completed", "partial", "skipped", "cancelled"):
            raise Conflict(f"Session already {s.status}", code="session_finished")
        if body.outcome in DONE_STATUSES and s.status != "in_progress":
            raise Conflict("Start the session before completing it", code="session_not_started")
        if body.outcome == "skipped":
            actual = 0
        elif body.actual_min is not None:
            actual = body.actual_min
        else:  # measured from the server-side start time, capped at the planned duration
            actual = min(s.estimated_min, int((now - (s.started_at or now)).total_seconds() // 60))
        s.status, s.actual_min, s.finished_at = body.outcome, actual, now
        s.reflection = body.reflection.model_dump() if body.reflection else None
        s.revision += 1
        if s.task_id and body.outcome != "skipped":
            t = self.db.get(Task, s.task_id)
            if t is not None and t.status not in ("done", "cancelled"):
                base = t.remaining_min if t.remaining_min is not None else s.estimated_min
                t.remaining_min = body.remaining_min if body.remaining_min is not None else max(0, base - actual)
                t.status = "done" if t.remaining_min == 0 else "in_progress"
                t.revision += 1
                record_event(self.db, user_id=user_id, type="TaskChanged", entity_id=t.id, revision=t.revision)
        self._touch(user_id, s, "SessionFinished")
        return self.session_out(s)


def _ceil_minutes(dt: datetime, step: int) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    if base < dt:
        base += timedelta(minutes=1)
    return base + timedelta(minutes=(-base.minute) % step)
