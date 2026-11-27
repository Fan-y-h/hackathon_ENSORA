"""In-app notifications (R28, T25): derived from outbox events and time-based checks.

Generation is idempotent thanks to a per-user ``dedupe_key``; each type can be disabled in
the profile (``preferences.notification_settings``). Only the in-app channel exists; email
or messaging delivery is planned and not claimed. Payloads never include mark values.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func, or_, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.common import Clock, UtcDatetime
from app.core.db import Base, UTCDateTime, new_id, utcnow
from app.core.errors import NotFound
from app.core.events import OutboxEvent
from app.modules.account.service import get_planning_preferences

EVENT_LOOKBACK = timedelta(days=14)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    channel: Mapped[str] = mapped_column(String(16), default="in_app", nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # R28 email reminders: not_requested | not_configured | sent | failed
    email_status: Mapped[str] = mapped_column(String(16), default="not_requested", server_default="not_requested",
                                              nullable=False)


EMAIL_TYPES = {"deadline_soon", "task_overdue", "review_due", "session_soon", "plan_outdated", "academic_risk"}


def _smtp_send(to: str, subject: str, body: str) -> None:  # pragma: no cover - real network
    import os
    import smtplib
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = os.environ["SMTP_FROM"], to, subject
    msg.set_content(body)
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587")), timeout=10) as s:
        if os.environ.get("SMTP_STARTTLS", "true").lower() == "true":
            s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
        s.send_message(msg)


def smtp_configured() -> bool:
    import os

    return bool(os.environ.get("SMTP_HOST") and os.environ.get("SMTP_FROM"))


SENDER = {"send": _smtp_send}  # replaced in tests


class NotificationOut(BaseModel):
    id: str
    type: str
    target_id: str | None
    title: str
    body: str | None
    channel: str
    created_at: UtcDatetime
    read: bool
    email_status: str


class NotificationPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    read: bool


class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int
    channels: list[Literal["in_app", "email"]] = ["in_app"]


class NotificationService:
    def __init__(self, db: Session, clock: Clock) -> None:
        self.db = db
        self.clock = clock

    def _add(self, user_id: str, enabled: dict, type_: str, key: str, title: str,
             body: str | None = None, target: str | None = None) -> None:
        if not enabled.get(type_, True):
            return
        exists = self.db.scalar(select(Notification.id).where(Notification.user_id == user_id,
                                                               Notification.dedupe_key == key))
        if exists is None:
            n = Notification(user_id=user_id, type=type_, dedupe_key=key, title=title, body=body,
                             target_id=target, created_at=self.clock.now())
            self.db.add(n)
            self.db.flush()
            self._email(user_id, n)

    def _email(self, user_id: str, n: Notification) -> None:
        """Opt-in email copy (address in profile preferences); never claims delivery when
        SMTP is not configured. Bodies contain no mark values by construction."""
        address = get_planning_preferences(self.db, user_id).notification_settings.get("email_address")
        if not address or n.type not in EMAIL_TYPES:
            return
        if not smtp_configured():
            n.email_status = "not_configured"
            return
        try:
            SENDER["send"](address, f"[Ensora+] {n.title}", n.body or n.title)
            n.email_status = "sent"
        except Exception:  # delivery failure stays visible, never raised to the user flow
            n.email_status = "failed"

    def refresh(self, user_id: str) -> None:
        from app.modules.grades.academic_service import AcademicService
        from app.modules.grades.models import AcademicProgram
        from app.modules.learning.models import Flashcard, Mastery
        from app.modules.planner.models import (
            Assessment,
            Event,
            Plan,
            StudySession,
            Task,
        )
        from app.modules.planner.plan_service import PlannerService

        now = self.clock.now()
        prefs = get_planning_preferences(self.db, user_id)
        on = prefs.notification_settings
        today = now.date().isoformat()

        # 1) Domain events (outbox).
        for e in self.db.scalars(select(OutboxEvent).where(OutboxEvent.user_id == user_id,
                                                          OutboxEvent.occurred_at >= now - EVENT_LOOKBACK)):
            action = (e.payload or {}).get("action")
            if e.type == "AssessmentChanged" and action == "created":
                a = self.db.get(Assessment, e.entity_id)
                ev = self.db.get(Event, a.event_id) if a and a.event_id else None
                self._add(user_id, on, "new_assessment", f"evt:{e.event_id}",
                          f"Nouvelle évaluation : {ev.title if ev else 'évaluation'}",
                          "Confirme son périmètre pour qu'elle soit planifiée.", e.entity_id)
            elif e.type == "EventChanged" and action == "source_changed":
                ev = self.db.get(Event, e.entity_id)
                self._add(user_id, on, "timetable_change", f"evt:{e.event_id}",
                          f"Emploi du temps modifié : {ev.title if ev else 'événement'}", None, e.entity_id)
            elif e.type in ("grade.recorded", "grade.updated"):
                self._add(user_id, on, "grade_recorded", f"evt:{e.event_id}",
                          "Nouvelle note enregistrée", "Tes objectifs et ton planning peuvent être recalculés.",
                          (e.payload or {}).get("program_id"))

        # 2) Time-based checks.
        for t in self.db.scalars(select(Task).where(Task.user_id == user_id, Task.status.in_(("todo", "in_progress")),
                                                     Task.deadline_at.is_not(None))):
            if t.deadline_at < now:
                self._add(user_id, on, "task_overdue", f"overdue:{t.id}", f"Tâche en retard : {t.title}", None, t.id)
            elif t.deadline_at < now + timedelta(hours=48):
                self._add(user_id, on, "deadline_soon", f"deadline:{t.id}:{t.deadline_at.date()}",
                          f"Échéance dans moins de 48 h : {t.title}", None, t.id)
        for a, ev in self.db.execute(select(Assessment, Event).join(Event, Event.id == Assessment.event_id, isouter=True)
                                     .where(Assessment.user_id == user_id, Assessment.status == "active",
                                            Assessment.deadline_at > now,
                                            Assessment.deadline_at < now + timedelta(hours=72))):
            self._add(user_id, on, "deadline_soon", f"assessment:{a.id}:{a.deadline_at.date()}",
                      f"Évaluation dans moins de 72 h : {ev.title if ev else 'évaluation'}", None, a.id)
        due_cards = self.db.scalar(select(func.count()).select_from(Flashcard).where(
            Flashcard.user_id == user_id, or_(Flashcard.due_at.is_(None), Flashcard.due_at <= now))) or 0
        if due_cards:
            self._add(user_id, on, "review_due", f"cards:{today}", f"{due_cards} carte(s) à réviser aujourd'hui")
        overdue = self.db.scalar(select(func.count()).select_from(Mastery).where(
            Mastery.user_id == user_id, Mastery.next_due_at.is_not(None), Mastery.next_due_at <= now)) or 0
        if overdue:
            self._add(user_id, on, "review_due", f"mastery:{today}", f"{overdue} notion(s) à réviser (rappel espacé)")
        for s in self.db.scalars(select(StudySession).where(StudySession.user_id == user_id,
                                                             StudySession.status == "planned",
                                                             StudySession.start_at > now,
                                                             StudySession.start_at <= now + timedelta(hours=1))):
            self._add(user_id, on, "session_soon", f"session:{s.id}", f"Séance bientôt : {s.title}", None, s.id)
        plan = self.db.scalar(select(Plan).where(Plan.user_id == user_id, Plan.status == "accepted")
                              .order_by(Plan.accepted_at.desc()))
        if plan is not None:
            st = PlannerService(self.db, self.clock).status(user_id)
            if st["dirty"]:
                digest = hashlib.sha1(st["current_input_revision"].encode()).hexdigest()[:12]
                self._add(user_id, on, "plan_outdated", f"plan:{plan.id}:{digest}", "Ton planning n'est plus à jour",
                          "Changements : " + ", ".join(st["changed_inputs"]), plan.id)
        acad = AcademicService(self.db, self.clock)
        for p in self.db.scalars(select(AcademicProgram).where(AcademicProgram.user_id == user_id)):
            try:
                val = acad.validation(user_id, p.id)
            except Exception:  # unconfigured program: nothing to evaluate
                continue
            for n in val.nodes:
                if n.risk == "high":
                    label = "" if val.verification_status == "verified" else " (règles non vérifiées)"
                    self._add(user_id, on, "academic_risk", f"risk:{p.id}:{n.node_key}:{val.rule_set_version}",
                              f"Risque académique : {n.title}{label}", ", ".join(n.reason_codes), n.node_id)

    def list(self, user_id: str, unread_only: bool, limit: int) -> NotificationList:
        self.refresh(user_id)
        q = select(Notification).where(Notification.user_id == user_id)
        if unread_only:
            q = q.where(Notification.read_at.is_(None))
        rows = self.db.scalars(q.order_by(Notification.created_at.desc(), Notification.id).limit(limit))
        unread = self.db.scalar(select(func.count()).select_from(Notification).where(
            Notification.user_id == user_id, Notification.read_at.is_(None))) or 0
        email_on = bool(get_planning_preferences(self.db, user_id).notification_settings.get("email_address"))
        return NotificationList(items=[self._out(n) for n in rows], unread_count=unread,
                                channels=["in_app", "email"] if email_on and smtp_configured() else ["in_app"])

    @staticmethod
    def _out(n: Notification) -> NotificationOut:
        return NotificationOut(id=n.id, type=n.type, target_id=n.target_id, title=n.title, body=n.body,
                               channel=n.channel, created_at=n.created_at, read=n.read_at is not None,
                               email_status=n.email_status)

    def mark(self, user_id: str, notification_id: str, read: bool) -> NotificationOut:
        n = self.db.get(Notification, notification_id)
        if n is None or n.user_id != user_id:
            raise NotFound("Notification not found")
        n.read_at = (n.read_at or self.clock.now()) if read else None
        return self._out(n)

    def read_all(self, user_id: str) -> int:
        rows = list(self.db.scalars(select(Notification).where(Notification.user_id == user_id,
                                                                Notification.read_at.is_(None))))
        for n in rows:
            n.read_at = self.clock.now()
        return len(rows)
