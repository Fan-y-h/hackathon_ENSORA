"""Academic results synced from Pamplemousse (P1) + persistent bridge into Grade Tracker.

Provides:
1. ``GET /api/v1/results``: read-only hierarchical view assembled from P1 source records
   (year -> semesters -> teaching units (UE) with coefficient/ECTS -> subjects with official
   coefficient & ECTS -> hierarchical assessments). Missing coefficients/marks stay ``null``
   (never silently replaced by 0 or 1).
2. ``POST /api/v1/grades/import-from-sources``: idempotent bridge syncing Pamplemousse
   curriculum, coefficients, ECTS, and official grades into an ``AcademicProgram`` so
   Grade Tracker (objectives, required marks, simulations, validation rules) and Academic
   Optimizer (planner signals) operate directly on real Pamplemousse data while preserving
   all persistent manual overrides and custom assessment subdivisions.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import AppError, ValidationFailed
from app.core.events import record_event
from app.core.identity import get_current_user_id
from app.modules.account.models import Profile
from app.modules.grades.models import AcademicNode, AcademicProgram, GradeRecord
from app.modules.grades.schemas import CurriculumPut, NodeIn, ProgramCreate, RecordCreate
from app.modules.grades.service import GradesService

router = APIRouter()

KINDS = ("academic_year", "semester", "teaching_unit", "subject", "coefficient", "assessment", "assessment_weight", "grade")


class SourcesUnavailable(AppError):
    status_code = 503
    code = "sources_unavailable"


class AssessmentOut(BaseModel):
    key: str
    title: str
    coefficient: float | None
    weight: float | None
    grade: float | None
    maximum: float | None
    minimum: float | None = None
    status: str | None = None
    children: list[AssessmentOut] = Field(default_factory=list)


AssessmentOut.model_rebuild()


class SubjectOut(BaseModel):
    key: str
    code: str | None
    title: str
    coefficient: float | None
    ects: float | None
    official_grade: float | None
    official_grade_maximum: float | None = None
    official_grade_minimum: float | None = None
    official_grade_status: str | None = None
    assessments: list[AssessmentOut]


class UnitOut(BaseModel):
    key: str
    code: str | None = None
    title: str
    coefficient: float | None = None
    ects: float | None = None
    official_average: float | None
    official_average_maximum: float | None = None
    unassigned: bool = False
    subjects: list[SubjectOut]


class SemesterOut(BaseModel):
    key: str
    title: str
    coefficient: float | None = None
    official_average: float | None
    official_average_maximum: float | None = None
    unassigned: bool = False
    units: list[UnitOut]


class ResultsOut(BaseModel):
    source: str = "pamplemousse"
    academic_year: str | None
    general_average: float | None
    general_average_maximum: float | None = None
    academic_years: list[str] = Field(default_factory=list)
    semesters: list[SemesterOut]


class GradesImportOut(BaseModel):
    program_id: str
    academic_year: str | None
    created_nodes: int
    updated_nodes: int
    preserved_manual_nodes: int
    created_records: int
    updated_records: int
    unknown_coefficient_nodes: list[str]


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = Decimal(str(value).replace(",", "."))
        return float(number) if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _dec_str(value: float | Decimal | None) -> str | None:
    if value is None:
        return None
    d = value if isinstance(value, Decimal) else Decimal(str(value))
    text = format(d.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _clean_title(title: str) -> str:
    """'- EXAMEN' -> 'Examen', 'APPRENTISSAGE SUPERVISÉ — Note finale' stays readable."""
    text = re.sub(r"^\s*-\s*", "", title or "").strip()
    return text[:1].upper() + text[1:].lower() if text.isupper() else text


def _stable_key(prefix: str, source_key: str) -> str:
    """Deterministic KEY_PATTERN-compliant node key (<= 64 chars) for any P1 external_id."""
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", source_key).strip("_")
    digest = hashlib.sha1(source_key.encode("utf-8")).hexdigest()[:10]
    head = slug[: max(1, 50 - len(prefix))]
    return f"{prefix}_{head}_{digest}"[:64]


def read_records(engine, source: str = "pamplemousse") -> dict[str, dict[str, Any]]:
    from backend.app.core.database import LOCAL_USER, connections, latest_consent_granted, records

    query = (
        select(records.c.external_id, records.c.payload)
        .join(connections, records.c.connection_id == connections.c.id)
        .where(
            connections.c.user_id == LOCAL_USER,
            connections.c.source == source,
            records.c.kind.in_(KINDS),
            latest_consent_granted(connections.c.id, "read").is_(True),
        )
    )
    with engine.connect() as db:
        return {external_id: payload or {} for external_id, payload in db.execute(query)}


def build_results(records: dict[str, dict[str, Any]], academic_year: str | None = None) -> ResultsOut:
    children: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, payload in records.items():
        parent = payload.get("parent_key")
        if parent:
            children.setdefault(parent, []).append((key, payload))

    def kids(key: str, kind: str) -> list[tuple[str, dict[str, Any]]]:
        return [(k, p) for k, p in children.get(key, []) if p.get("kind") == kind]

    def official(key: str) -> dict[str, Any]:
        return next((
            p for _, p in kids(key, "grade")
            if p.get("grade_kind") == "official_aggregate" and p.get("record_status") != "ambiguous"
        ), {})

    def coefficient(key: str, level: str, node_payload: dict[str, Any] | None = None) -> float | None:
        values = (
            _num(p.get("weight")) for _, p in kids(key, "coefficient")
            if p.get("level") == level and p.get("record_status") not in ("missing", "ambiguous")
        )
        known = {v for v in values if v is not None and v >= 0}
        if known:
            return next(iter(known)) if len(known) == 1 else None
        if node_payload is not None:
            direct = _num(node_payload.get("coefficient"))
            if direct is not None and direct >= 0:
                return direct
        return None

    def _grade_status(grade_payload: dict[str, Any] | None) -> str | None:
        if not grade_payload:
            return None
        raw = str(grade_payload.get("record_status") or "").strip().lower()
        if raw in ("absent_unjustified", "abi", "absent"):
            return "absent_unjustified"
        if raw in ("dispensed", "abj", "excused", "dispense"):
            return "dispensed"
        if _num(grade_payload.get("value")) is not None:
            return "graded"
        return raw or None

    def assessments(parent: str) -> list[AssessmentOut]:
        out: list[AssessmentOut] = []
        for key, p in sorted(kids(parent, "assessment"), key=lambda kv: kv[0]):
            grades = [g for _, g in kids(key, "grade") if g.get("record_status") != "ambiguous"]
            grade = next((g for g in grades if g.get("grade_kind") == "assessment"), next(iter(grades), {}))
            weight = next((_num(w.get("weight")) for _, w in kids(key, "assessment_weight")), None)
            sub_items = assessments(key)
            out.append(AssessmentOut(
                key=key,
                title=_clean_title(p.get("title", "")),
                coefficient=coefficient(key, "assessment", p),
                weight=weight,
                grade=_num(grade.get("value")),
                maximum=_num(grade.get("maximum")),
                minimum=_num(grade.get("minimum")),
                status=_grade_status(grade),
                children=sub_items,
            ))
        return out

    def subject(key: str, p: dict[str, Any]) -> SubjectOut:
        grade = official(key)
        return SubjectOut(
            key=key,
            code=p.get("code") or p.get("source_code"),
            title=p.get("title", ""),
            coefficient=coefficient(key, "subject", p),
            ects=_num(p.get("ects")),
            official_grade=_num(grade.get("value")),
            official_grade_maximum=_num(grade.get("maximum")),
            official_grade_minimum=_num(grade.get("minimum")),
            official_grade_status=_grade_status(grade),
            assessments=assessments(key),
        )

    year_keys: dict[str, str] = {}
    for key, payload in sorted(records.items()):
        if payload.get("kind") == "academic_year":
            provenance_year = (payload.get("provenance") or {}).get("academic_year") or ""
            year = re.search(r"(?<!\d)(\d{4})\s*[-–_/]\s*(\d{4})(?!\d)",
                             f"{provenance_year} {payload.get('title') or ''}")
            if year:
                year_keys.setdefault(f"{year[1]}-{year[2]}", key)
    academic_years = sorted(year_keys, reverse=True)
    selected_year = academic_year or next(iter(academic_years), None)
    year_key = year_keys.get(selected_year) if selected_year else None
    semesters: list[SemesterOut] = []
    for sem_key, sem in sorted(kids(year_key, "semester") if year_key else [], key=lambda kv: kv[1].get("title", "")):
        units = []
        for ue_key, ue in sorted(kids(sem_key, "teaching_unit"), key=lambda kv: kv[1].get("title", "")):
            subjects = [subject(k, p) for k, p in sorted(kids(ue_key, "subject"), key=lambda kv: kv[1].get("title", ""))]
            units.append(UnitOut(
                key=ue_key,
                code=ue.get("source_code") or ue.get("code"),
                title=ue.get("title", ""),
                coefficient=coefficient(ue_key, "teaching_unit", ue),
                ects=_num(ue.get("ects")),
                official_average=_num(official(ue_key).get("value")),
                official_average_maximum=_num(official(ue_key).get("maximum")),
                subjects=subjects,
            ))
        standalone = sorted(kids(sem_key, "subject"), key=lambda kv: kv[1].get("title", ""))
        if standalone:
            units.append(UnitOut(key=f"{sem_key}:unassigned", title="Sans UE", official_average=None, unassigned=True,
                                 subjects=[subject(k, p) for k, p in standalone]))
        semesters.append(SemesterOut(
            key=sem_key, title=sem.get("title", ""), coefficient=coefficient(sem_key, "semester", sem),
            official_average=_num(official(sem_key).get("value")),
            official_average_maximum=_num(official(sem_key).get("maximum")), units=units,
        ))

    general = official(year_key) if year_key else {}
    if year_key:
        standalone = sorted(kids(year_key, "subject"), key=lambda kv: kv[1].get("title", ""))
        if standalone:
            semesters.append(SemesterOut(
                key=f"{year_key}:unassigned", title="Sans semestre", official_average=None, unassigned=True,
                units=[UnitOut(key=f"{year_key}:unassigned:unit", title="Sans UE", official_average=None, unassigned=True,
                               subjects=[subject(k, p) for k, p in standalone])],
            ))
    return ResultsOut(
        academic_year=selected_year, academic_years=academic_years, semesters=semesters,
        general_average=_num(general.get("value")), general_average_maximum=_num(general.get("maximum")),
    )


def import_results_into_program(
    db: Session,
    user_id: str,
    results: ResultsOut,
    clock: Clock | None = None,
) -> GradesImportOut:
    """Idempotently sync Pamplemousse ``ResultsOut`` into an ``AcademicProgram``.

    Preserves all manual overrides (``weight_provenance == 'manual'``, custom subdivisions,
    and manual ``GradeRecord``s). Unknown coefficients remain ``weight_status='unknown'``
    and are NEVER silently replaced by 0 or 1.
    """
    year_label = results.academic_year
    program = db.scalar(
        select(AcademicProgram)
        .where(
            AcademicProgram.user_id == user_id,
            AcademicProgram.provenance == "pamplemousse",
            AcademicProgram.academic_year == year_label,
        )
        .order_by(AcademicProgram.created_at)
    )
    if program is None:
        title = f"Pamplemousse {year_label}" if year_label else "Pamplemousse"
        program = AcademicProgram(
            user_id=user_id,
            title=title,
            academic_year=year_label if year_label and re.match(r"^\d{4}-\d{4}$", year_label) else None,
            provenance="pamplemousse",
        )
        db.add(program)
        db.flush()

    existing_nodes = list(
        db.scalars(select(AcademicNode).where(AcademicNode.program_id == program.id).order_by(AcademicNode.ordinal))
    )
    by_source = {n.source_key: n for n in existing_nodes if getattr(n, "source_key", None)}
    by_key = {n.key: n for n in existing_nodes}

    created_nodes = updated_nodes = preserved_manual = 0
    created_records = updated_records = 0
    unknown_coef_keys: list[str] = []
    ordinal = max((n.ordinal for n in existing_nodes), default=-1) + 1

    def upsert_node(
        *,
        source_key: str,
        key_prefix: str,
        parent_key: str | None,
        kind: str,
        title: str,
        weight: float | None,
        ects: float | None = None,
        scale_max: float | None = None,
        scale_min: float | None = None,
        subject_id: str | None = None,
        is_root: bool = False,
    ) -> AcademicNode:
        nonlocal created_nodes, updated_nodes, preserved_manual, ordinal
        row = by_source.get(source_key)
        node_key = row.key if row is not None else _stable_key(key_prefix, source_key)
        if row is None and node_key in by_key:
            row = by_key[node_key]
        if row is None:
            w_str = _dec_str(1) if is_root else (_dec_str(weight) if weight is not None and weight >= 0 else "")
            w_status = "known" if (is_root or (weight is not None and weight >= 0)) else "unknown"
            w_prov = "official" if w_status == "known" else "unknown"
            row = AcademicNode(
                program_id=program.id,
                key=node_key,
                parent_key=parent_key,
                ordinal=ordinal,
                kind=kind,
                title=(title or kind)[:200],
                weight=w_str or "",
                weight_mode="coefficient",
                weight_status=w_status,
                weight_provenance=w_prov,
                ects=_dec_str(ects),
                scale_min=_dec_str(scale_min) or "0",
                scale_max=_dec_str(scale_max) or "20",
                subject_id=subject_id,
                source_key=source_key,
            )
            ordinal += 1
            db.add(row)
            db.flush()
            by_source[source_key] = row
            by_key[row.key] = row
            created_nodes += 1
            if w_status == "unknown":
                unknown_coef_keys.append(row.key)
            return row

        # Preserve manual overrides on coefficients/provenance!
        changed = False
        if row.source_key != source_key:
            row.source_key = source_key
            changed = True
        if row.weight_provenance == "manual":
            preserved_manual += 1
        elif not is_root:
            if weight is not None and weight >= 0:
                new_w = _dec_str(weight) or ""
                if row.weight != new_w or row.weight_status != "known" or row.weight_provenance != "official":
                    row.weight = new_w
                    row.weight_status = "known"
                    row.weight_provenance = "official"
                    changed = True
            else:
                if row.weight_status != "unknown":
                    row.weight = ""
                    row.weight_status = "unknown"
                    row.weight_provenance = "unknown"
                    changed = True
                unknown_coef_keys.append(row.key)
        new_ects = _dec_str(ects)
        if new_ects is not None and row.ects != new_ects and row.weight_provenance != "manual":
            row.ects = new_ects
            changed = True
        if scale_max is not None and not db.scalar(select(GradeRecord.id).where(
            GradeRecord.node_id == row.id,
            GradeRecord.provenance != "pamplemousse",
        ).limit(1)):
            new_scale_min = _dec_str(scale_min) or "0"
            new_scale_max = _dec_str(scale_max)
            if (row.scale_min, row.scale_max) != (new_scale_min, new_scale_max):
                row.scale_min, row.scale_max = new_scale_min, new_scale_max
                changed = True
        if subject_id and not row.subject_id:
            row.subject_id = subject_id
            changed = True
        if changed:
            updated_nodes += 1
        return row

    def upsert_official_grade(
        node: AcademicNode, value: float | None, status: str | None,
        maximum: float | None, minimum: float | None,
    ) -> None:
        nonlocal created_records, updated_records
        if status != "dispensed":
            if maximum is None or value is None:
                return
            if Decimal(node.scale_max) != Decimal(str(maximum)) or Decimal(node.scale_min) != Decimal(str(minimum or 0)):
                return
        # Never overwrite a leaf that gained custom children (subdivisions).
        has_children = any(n.parent_key == node.key for n in by_key.values())
        if has_children:
            return
        rec = db.scalar(
            select(GradeRecord).where(GradeRecord.node_id == node.id, GradeRecord.kind == "actual")
        )
        g_status = status if status in ("absent_unjustified", "dispensed") else "graded"
        val_str = "0" if g_status in ("absent_unjustified", "dispensed") else (_dec_str(value) or "0")
        if rec is None:
            db.add(
                GradeRecord(
                    user_id=user_id,
                    program_id=program.id,
                    node_id=node.id,
                    kind="actual",
                    value=val_str,
                    grade_status=g_status,
                    provenance="pamplemousse",
                    observed_at=clock.now() if clock else None,
                )
            )
            created_records += 1
        elif rec.provenance == "pamplemousse" and (rec.value != val_str or rec.grade_status != g_status):
            rec.value = val_str
            rec.grade_status = g_status
            rec.revision += 1
            updated_records += 1

    root_source = f"pamplemousse:year:{year_label or 'default'}"
    root = upsert_node(
        source_key=root_source,
        key_prefix="yr",
        parent_key=None,
        kind="year",
        title=year_label or "Année académique",
        weight=1.0,
        is_root=True,
    )

    def sync_assessments(parent_node: AcademicNode, items: list[AssessmentOut]) -> None:
        for a in items:
            a_weight = a.coefficient if a.coefficient is not None else a.weight
            kind = "assessment_group" if a.children else "assessment"
            node = upsert_node(
                source_key=a.key,
                key_prefix="asmt",
                parent_key=parent_node.key,
                kind=kind,
                title=a.title or "Évaluation",
                weight=a_weight,
                scale_max=a.maximum,
                scale_min=a.minimum,
                subject_id=parent_node.subject_id,
            )
            if a.children:
                sync_assessments(node, a.children)
            else:
                upsert_official_grade(node, a.grade, a.status, a.maximum, a.minimum)

    for sem in results.semesters:
        if sem.unassigned:
            continue
        sem_node = upsert_node(
            source_key=sem.key,
            key_prefix="sem",
            parent_key=root.key,
            kind="semester",
            title=sem.title or "Semestre",
            weight=sem.coefficient,
        )
        for unit in sem.units:
            if unit.unassigned:
                continue
            # Never invent a UE coefficient: if Pamplemousse provides none, keep weight_status="unknown"
            # unless the user already entered a manual value.
            unit_node = upsert_node(
                source_key=unit.key,
                key_prefix="ue",
                parent_key=sem_node.key,
                kind="unit",
                title=unit.title or "UE",
                weight=unit.coefficient,
                ects=unit.ects,
            )
            for subj in unit.subjects:
                subj_node = upsert_node(
                    source_key=subj.key,
                    key_prefix="subj",
                    parent_key=unit_node.key,
                    kind="subject",
                    title=subj.title or "Matière",
                    weight=subj.coefficient,
                    ects=subj.ects,
                    subject_id=subj.code,
                    scale_max=subj.official_grade_maximum if not subj.assessments else None,
                    scale_min=subj.official_grade_minimum if not subj.assessments else None,
                )
                if subj.assessments:
                    sync_assessments(subj_node, subj.assessments)
                else:
                    upsert_official_grade(
                        subj_node, subj.official_grade, subj.official_grade_status,
                        subj.official_grade_maximum, subj.official_grade_minimum,
                    )

    if created_nodes or updated_nodes or created_records or updated_records:
        program.revision += 1
        record_event(
            db,
            user_id=user_id,
            type="curriculum.synced_from_sources",
            entity_id=program.id,
            revision=program.revision,
            payload={
                "program_id": program.id,
                "created_nodes": created_nodes,
                "updated_nodes": updated_nodes,
                "created_records": created_records,
                "updated_records": updated_records,
            },
        )
    db.flush()
    return GradesImportOut(
        program_id=program.id,
        academic_year=results.academic_year,
        created_nodes=created_nodes,
        updated_nodes=updated_nodes,
        preserved_manual_nodes=preserved_manual,
        created_records=created_records,
        updated_records=updated_records,
        unknown_coefficient_nodes=sorted(set(unknown_coef_keys)),
    )


AcademicYearQuery = Annotated[str | None, Query(pattern=r"^\d{4}-\d{4}$")]


def _source_results(request: Request, db: Session, user_id: str, academic_year: str | None) -> ResultsOut:
    sources_app = getattr(request.app.state, "sources_app", None)
    runtime = getattr(getattr(sources_app, "state", None), "runtime", None)
    if runtime is None:
        raise SourcesUnavailable("Source synchronization is not running")
    active_year = academic_year or db.scalar(select(Profile.academic_year).where(Profile.user_id == user_id))
    return build_results(read_records(runtime.engine), active_year)


@router.get("/results", response_model=ResultsOut, tags=["grades"])
def results_from_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    user_id: Annotated[str, Depends(get_current_user_id)],
    academic_year: AcademicYearQuery = None,
):
    """Read one academic year's authorized Pamplemousse results."""
    return _source_results(request, db, user_id, academic_year)


def _import_source_results(
    request: Request, db: Session, clock: Clock, user_id: str, academic_year: str | None,
) -> GradesImportOut:
    results = _source_results(request, db, user_id, academic_year)
    if not any(not semester.unassigned and any(not unit.unassigned for unit in semester.units)
               for semester in results.semesters):
        raise ValidationFailed("No authorized Pamplemousse curriculum is available for this year", code="source_results_empty")
    return import_results_into_program(db, user_id, results, clock)


@router.post("/grades/import-from-sources", response_model=GradesImportOut, tags=["grades"])
def import_grades_from_sources(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    user_id: Annotated[str, Depends(get_current_user_id)],
    academic_year: AcademicYearQuery = None,
):
    """Import the selected academic year into the local Grade Tracker."""
    return _import_source_results(request, db, clock, user_id, academic_year)


class ManualGradeImport(ProgramCreate):
    nodes: list[NodeIn] = Field(default_factory=list, max_length=2000)
    marks: dict[str, Decimal] = Field(default_factory=dict)


class AcademicImportRequest(BaseModel):
    source: Literal["pamplemousse", "manual_json"]
    dry_run: bool = False
    program: ManualGradeImport | None = None
    academic_year: str | None = Field(default=None, pattern=r"^\d{4}-\d{4}$")


class AcademicImportOut(BaseModel):
    program_id: str | None
    program_title: str
    dry_run: bool
    nodes_imported: int
    grades_imported: int
    conflicts: list[str]
    revision: int


@router.post("/grades/import", response_model=AcademicImportOut, tags=["grades"])
def import_grades(
    body: AcademicImportRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    user_id: Annotated[str, Depends(get_current_user_id)],
):
    """Validate or apply an academic import in a single transaction."""
    transaction = db.begin_nested()
    try:
        if body.source == "pamplemousse":
            imported = _import_source_results(request, db, clock, user_id, body.academic_year)
            program = db.get(AcademicProgram, imported.program_id)
            nodes = imported.created_nodes + imported.updated_nodes
            marks = imported.created_records + imported.updated_records
            conflicts = imported.unknown_coefficient_nodes
        else:
            if body.program is None:
                raise ValidationFailed("A manual import requires a program")
            manual = body.program
            service = GradesService(db, clock)
            fields = manual.model_dump(exclude={"nodes", "marks"})
            existing = db.scalar(select(AcademicProgram).where(
                AcademicProgram.user_id == user_id,
                AcademicProgram.provenance == "manual_json",
                *(getattr(AcademicProgram, key) == value for key, value in fields.items()),
            ).limit(1))
            if existing is not None:
                return AcademicImportOut(program_id=existing.id, program_title=existing.title,
                                         dry_run=body.dry_run, nodes_imported=0, grades_imported=0,
                                         conflicts=["PROGRAM_ALREADY_IMPORTED"], revision=existing.revision)
            program = service.create_program(user_id, ProgramCreate(**fields), provenance="manual_json")
            node_ids = {}
            if manual.nodes:
                curriculum = service.put_curriculum(user_id, program.id, CurriculumPut(nodes=manual.nodes))
                node_ids = {node.key: node.id for node in curriculum.nodes}
            for key, value in manual.marks.items():
                if key not in node_ids:
                    raise ValidationFailed("Grade references an unknown node", {"node_key": key})
                service.create_record(user_id, RecordCreate(node_id=node_ids[key], value=value))
            program = db.get(AcademicProgram, program.id)
            nodes, marks, conflicts = len(node_ids), len(manual.marks), []

        result = AcademicImportOut(
            program_id=None if body.dry_run else program.id, program_title=program.title,
            dry_run=body.dry_run, nodes_imported=nodes, grades_imported=marks,
            conflicts=conflicts, revision=program.revision,
        )
        if not body.dry_run:
            transaction.commit()
        return result
    finally:
        if transaction.is_active:
            transaction.rollback()
