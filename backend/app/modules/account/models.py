"""Learning profile tables: ``profiles`` and ``preferences`` (docs/DATA_MODEL.md)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, new_id


class Profile(TimestampMixin, Base):
    __tablename__ = "profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    cohort: Mapped[str | None] = mapped_column(String(4), nullable=True)
    track: Mapped[str | None] = mapped_column(String(120), nullable=True)
    student_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    academic_year: Mapped[str | None] = mapped_column(String(9), nullable=True)
    locale: Mapped[str] = mapped_column(String(5), default="fr", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Paris", nullable=False)
    goals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    # R01: declared confidence per subject/concept (1–5). Ranking prior only, never mastery.
    self_assessment: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, server_default="{}", nullable=False)


class Preferences(TimestampMixin, Base):
    __tablename__ = "preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    work_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    rest_windows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    daily_max_min: Mapped[int] = mapped_column(Integer, default=240, nullable=False)
    weekly_max_min: Mapped[int] = mapped_column(Integer, default=1200, nullable=False)
    min_session_min: Mapped[int] = mapped_column(Integer, default=25, nullable=False)
    max_session_min: Mapped[int] = mapped_column(Integer, default=90, nullable=False)
    preferred_formats: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    # P1 (T26/T25): algorithm choices and notification settings.
    repetition_algorithm: Mapped[str] = mapped_column(
        String(16), default="heuristic", server_default="heuristic", nullable=False
    )
    desired_retention: Mapped[float] = mapped_column(Float, default=0.9, server_default="0.9", nullable=False)
    planner_algorithm: Mapped[str] = mapped_column(
        String(16), default="greedy", server_default="greedy", nullable=False
    )
    notification_settings: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default="{}", nullable=False
    )
