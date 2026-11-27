"""Workspace tables: Focus Room (T23), Smart Notes and Error Notebook (T22)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UTCDateTime, new_id, utcnow


class FocusSession(TimestampMixin, Base):
    __tablename__ = "focus_sessions"
    __table_args__ = (Index("ix_focus_user_status", "user_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    study_session_id: Mapped[str | None] = mapped_column(
        ForeignKey("study_sessions.id", ondelete="SET NULL"), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text, nullable=True)
    work_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    break_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # working|paused|break|finished
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    reflection: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class WorkInterval(Base):
    __tablename__ = "work_intervals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    focus_id: Mapped[str] = mapped_column(ForeignKey("focus_sessions.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)  # work | break
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Notebook(TimestampMixin, Base):
    __tablename__ = "notebooks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)


class Note(TimestampMixin, Base):
    __tablename__ = "notes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    notebook_id: Mapped[str] = mapped_column(ForeignKey("notebooks.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    chapter_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    concept_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, default="", nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    origin: Mapped[str] = mapped_column(String(16), default="manual", nullable=False)


class NoteVersion(Base):
    """Immutable snapshot of every saved state of a note (user edits are never lost)."""

    __tablename__ = "note_versions"
    __table_args__ = (UniqueConstraint("note_id", "revision"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    note_id: Mapped[str] = mapped_column(ForeignKey("notes.id", ondelete="CASCADE"), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ErrorEntry(TimestampMixin, Base):
    __tablename__ = "error_entries"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    concept_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # quiz|exam|manual|grade
    source_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    prompt_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    remediation: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(128), nullable=False)
