"""Academic tables (docs/DATA_MODEL.md § Academic records). Decimals stored as strings."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, UTCDateTime, new_id, utcnow


class AcademicProgram(TimestampMixin, Base):
    __tablename__ = "academic_programs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    academic_year: Mapped[str | None] = mapped_column(String(9), nullable=True)
    cohort: Mapped[str | None] = mapped_column(String(4), nullable=True)
    specialization: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)


class AcademicNode(Base):
    __tablename__ = "academic_nodes"
    __table_args__ = (UniqueConstraint("program_id", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("academic_programs.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    weight: Mapped[str] = mapped_column(String(40), nullable=False)
    weight_mode: Mapped[str] = mapped_column(String(12), nullable=False)
    scale_min: Mapped[str] = mapped_column(String(40), nullable=False)
    scale_max: Mapped[str] = mapped_column(String(40), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class GradeRecord(TimestampMixin, Base):
    __tablename__ = "grade_records"
    __table_args__ = (UniqueConstraint("node_id", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("academic_programs.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(ForeignKey("academic_nodes.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # actual | estimated
    value: Mapped[str] = mapped_column(String(40), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    observed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class AcademicObjective(TimestampMixin, Base):
    __tablename__ = "academic_objectives"
    __table_args__ = (UniqueConstraint("node_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("academic_programs.id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[str] = mapped_column(ForeignKey("academic_nodes.id", ondelete="CASCADE"), nullable=False)
    target: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---- P1: validation rules (T44), saved scenarios (T39), grade feedback (T45 / R42) ----------
class AcademicRuleSet(Base):
    """Immutable published version of a program's validation rules."""

    __tablename__ = "academic_rule_sets"
    __table_args__ = (UniqueConstraint("program_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("academic_programs.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(16), nullable=False)  # unverified|verified
    source_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    rules: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class GradeScenario(TimestampMixin, Base):
    __tablename__ = "grade_scenarios"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    program_id: Mapped[str] = mapped_column(
        ForeignKey("academic_programs.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    base_revision: Mapped[int] = mapped_column(Integer, nullable=False)


class GradeScenarioValue(Base):
    """Overlay value — never an actual grade."""

    __tablename__ = "grade_scenario_values"
    __table_args__ = (UniqueConstraint("scenario_id", "node_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scenario_id: Mapped[str] = mapped_column(ForeignKey("grade_scenarios.id", ondelete="CASCADE"), nullable=False)
    node_id: Mapped[str] = mapped_column(ForeignKey("academic_nodes.id", ondelete="CASCADE"), nullable=False)
    value: Mapped[str] = mapped_column(String(40), nullable=False)


class AcademicFeedback(TimestampMixin, Base):
    """Student-reported difficulties on a graded paper → diagnostic proposal (no mastery write)."""

    __tablename__ = "academic_feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    grade_record_id: Mapped[str] = mapped_column(ForeignKey("grade_records.id", ondelete="CASCADE"), nullable=False)
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    flags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    concept_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    comments: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrected_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    external_ai_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False)  # proposed|confirmed|dismissed


# ---- P2: corrected papers itemized by the student (T46) -----------------------------------
class CorrectedPaper(TimestampMixin, Base):
    __tablename__ = "corrected_papers"
    __table_args__ = (UniqueConstraint("grade_record_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    grade_record_id: Mapped[str] = mapped_column(ForeignKey("grade_records.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    external_ai_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    items: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
