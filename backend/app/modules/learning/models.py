"""Learning tables: decks/cards/reviews, quizzes/questions/attempts, evidence and mastery."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UTCDateTime, new_id, utcnow


class Deck(TimestampMixin, Base):
    __tablename__ = "decks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class Flashcard(TimestampMixin, Base):
    __tablename__ = "flashcards"
    __table_args__ = (Index("ix_cards_user_due", "user_id", "due_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    deck_id: Mapped[str] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    front: Mapped[str] = mapped_column(Text, nullable=False)
    back: Mapped[str] = mapped_column(Text, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # None = new
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_rating: Mapped[str | None] = mapped_column(String(8), nullable=True)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    fsrs_stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    fsrs_difficulty: Mapped[float | None] = mapped_column(Float, nullable=True)


class CardReview(Base):
    __tablename__ = "card_reviews"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(ForeignKey("flashcards.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    rating: Mapped[str] = mapped_column(String(8), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, nullable=False)
    due_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)


class Quiz(TimestampMixin, Base):
    __tablename__ = "quizzes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    type: Mapped[str] = mapped_column(String(16), default="practice", nullable=False)
    time_limit_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    private_answer: Mapped[Any] = mapped_column(JSON, nullable=True)
    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)
    points: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    concept_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    dimension: Mapped[str] = mapped_column(String(16), default="theory", nullable=False)
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    partial_credit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0", nullable=False)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    quiz_id: Mapped[str] = mapped_column(ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    quiz_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    pending_review: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class AttemptItem(Base):
    __tablename__ = "attempt_items"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    response: Mapped[Any] = mapped_column(JSON, nullable=True)
    assisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    grader: Mapped[str | None] = mapped_column(String(16), nullable=True)  # auto|self|teacher|ai_provisional
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class Evidence(Base):
    """Append-only objective evidence; one row per activity/item/concept/dimension."""

    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint("activity_id", "item_id", "concept_id", "dimension", name="uq_evidence"),
        Index("ix_evidence_user_concept", "user_id", "concept_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    activity_kind: Mapped[str] = mapped_column(String(16), nullable=False)  # quiz | card
    activity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    assisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    difficulty: Mapped[str | None] = mapped_column(String(8), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    # T33: model prediction just before the observation (calibration), and derivation source.
    predicted: Mapped[float | None] = mapped_column(Float, nullable=True)
    derived_from: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Mastery(Base):
    __tablename__ = "mastery"
    __table_args__ = (UniqueConstraint("user_id", "concept_id", "dimension"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept_id: Mapped[str] = mapped_column(String(36), nullable=False)
    # Denormalized until the catalog owns concept → subject (C1).
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dimension: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    n_effective: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    interval_days: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_review_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class ConceptEdge(Base):
    """Confirmed prerequisite A → B. INTERIM: the catalog module (C1) owns concept_edges;
    this table lets the planner honour prerequisites until the catalog exists."""

    __tablename__ = "concept_prerequisites"
    __table_args__ = (UniqueConstraint("user_id", "prerequisite_id", "dependent_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    prerequisite_id: Mapped[str] = mapped_column(String(36), nullable=False)
    dependent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
