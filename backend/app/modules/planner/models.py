"""Agenda tables owned by the planner module: events, assessments, tasks (docs/DATA_MODEL.md)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UTCDateTime, new_id


class AgendaState(Base):
    """Per-user agenda input revision; bumped on any event/assessment/task change."""

    __tablename__ = "agenda_state"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class Event(TimestampMixin, Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("user_id", "source", "external_id", name="uq_event_source_external"),
        CheckConstraint(
            "(all_day_date IS NULL AND start_at IS NOT NULL AND end_at IS NOT NULL "
            "AND end_at > start_at) OR "
            "(all_day_date IS NOT NULL AND start_at IS NULL AND end_at IS NULL)",
            name="ck_event_timed_or_all_day",
        ),
        Index("ix_events_user_start", "user_id", "start_at"),
        Index("ix_events_user_day", "user_id", "all_day_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    start_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    all_day_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    room: Mapped[str | None] = mapped_column(String(120), nullable=True)
    teacher: Mapped[str | None] = mapped_column(String(120), nullable=True)
    group_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False)
    busy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    recurrence_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class Assessment(TimestampMixin, Base):
    __tablename__ = "assessments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[str | None] = mapped_column(
        ForeignKey("events.id", ondelete="CASCADE"), unique=True, nullable=True
    )
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    duration_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    scope_status: Mapped[str] = mapped_column(String(16), default="unconfirmed", nullable=False)
    scope_revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)


class AssessmentConcept(Base):
    __tablename__ = "assessment_concepts"
    __table_args__ = (UniqueConstraint("assessment_id", "concept_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    assessment_id: Mapped[str] = mapped_column(
        ForeignKey("assessments.id", ondelete="CASCADE"), nullable=False
    )
    # FK to catalog `concepts` added by C1 once the catalog module exists.
    concept_id: Mapped[str] = mapped_column(String(36), nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Task(TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "origin", "external_key", name="uq_task_external"),
        Index("ix_tasks_user_deadline", "user_id", "deadline_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    parent_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), nullable=True
    )
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    estimated_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remaining_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="todo", nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    splittable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)


class Plan(TimestampMixin, Base):
    """A plan preview (status=preview) or the accepted plan (status=accepted)."""

    __tablename__ = "plans"
    __table_args__ = (Index("ix_plans_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # preview|accepted|superseded|discarded
    horizon_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    horizon_end: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    input_revision: Mapped[str] = mapped_column(String(255), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(32), nullable=False)
    preview: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    warnings: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class StudySession(TimestampMixin, Base):
    __tablename__ = "study_sessions"
    __table_args__ = (Index("ix_sessions_user_start", "user_id", "start_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), nullable=False)
    need_key: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assessments.id", ondelete="SET NULL"), nullable=True
    )
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # session_concepts table in DATA_MODEL; JSON until the catalog owns concepts (C1).
    concept_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    activity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    estimated_min: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="planned", nullable=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    priority: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    objective: Mapped[str] = mapped_column(Text, default="", nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, default="", nullable=False)
    generation_spec: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reflection: Mapped[dict | None] = mapped_column(JSON, nullable=True)
