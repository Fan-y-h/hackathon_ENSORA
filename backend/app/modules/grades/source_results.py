"""Academic results synced from Pamplemousse (P1) for the "Résultats" screen.

Read-only view assembled from P1 source records: semesters -> teaching units (UE) ->
subjects with their official coefficient -> assessments. Official values published by
Pamplemousse (subject final mark, UE and semester averages, general average) are returned
as-is and flagged ``official``; nothing is invented: a missing coefficient or mark stays
``null`` (never 0). Provisional averages for what-if simulation are computed client-side
and labelled as such.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.core.errors import AppError

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
    maximum: float


class SubjectOut(BaseModel):
    key: str
    code: str | None
    title: str
    coefficient: float | None
    ects: float | None
    official_grade: float | None
    assessments: list[AssessmentOut]


class UnitOut(BaseModel):
    key: str
    title: str
    official_average: float | None
    subjects: list[SubjectOut]


class SemesterOut(BaseModel):
    key: str
    title: str
    official_average: float | None
    units: list[UnitOut]


class ResultsOut(BaseModel):
    source: str = "pamplemousse"
    academic_year: str | None
    general_average: float | None
    semesters: list[SemesterOut]


def _num(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value).replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _clean_title(title: str) -> str:
    """'- EXAMEN' -> 'Examen', 'APPRENTISSAGE SUPERVISÉ — Note finale' stays readable."""
    text = re.sub(r"^\s*-\s*", "", title or "").strip()
    return text[:1].upper() + text[1:].lower() if text.isupper() else text


def read_records(engine, source: str = "pamplemousse") -> dict[str, dict[str, Any]]:
    from backend.app.core.database import connections, records

    query = (
        select(records.c.external_id, records.c.payload)
        .join(connections, records.c.connection_id == connections.c.id)
        .where(connections.c.source == source, records.c.kind.in_(KINDS))
    )
    with engine.connect() as db:
        return {external_id: payload or {} for external_id, payload in db.execute(query)}


def build_results(records: dict[str, dict[str, Any]]) -> ResultsOut:
    children: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for key, payload in records.items():
        parent = payload.get("parent_key")
        if parent:
            children.setdefault(parent, []).append((key, payload))

    def kids(key: str, kind: str) -> list[tuple[str, dict[str, Any]]]:
        return [(k, p) for k, p in children.get(key, []) if p.get("kind") == kind]

    def official(key: str) -> float | None:
        for _, p in kids(key, "grade"):
            if p.get("grade_kind") == "official_aggregate":
                return _num(p.get("value"))
        return None

    def coefficient(key: str, level: str) -> float | None:
        values = [_num(p.get("weight")) for _, p in kids(key, "coefficient") if p.get("level") == level]
        known = [v for v in values if v is not None]
        return max(known) if known else None

    def assessments(parent: str) -> list[AssessmentOut]:
        out: list[AssessmentOut] = []
        for key, p in sorted(kids(parent, "assessment"), key=lambda kv: kv[0]):
            grade = next((g for _, g in kids(key, "grade")), None)
            weight = next((_num(w.get("weight")) for _, w in kids(key, "assessment_weight")), None)
            out.append(AssessmentOut(
                key=key,
                title=_clean_title(p.get("title", "")),
                coefficient=coefficient(key, "assessment"),
                weight=weight,
                grade=_num(grade.get("value")) if grade else None,
                maximum=_num(grade.get("maximum")) if grade and _num(grade.get("maximum")) else 20.0,
            ))
            out.extend(assessments(key))  # nested parts (e.g. an intermediate report of a project)
        return out

    def subject(key: str, p: dict[str, Any]) -> SubjectOut:
        return SubjectOut(
            key=key,
            code=p.get("code") or p.get("source_code"),
            title=p.get("title", ""),
            coefficient=coefficient(key, "subject"),
            ects=_num(p.get("ects")),
            official_grade=official(key),
            assessments=assessments(key),
        )

    year_key, year = next(((k, p) for k, p in records.items() if p.get("kind") == "academic_year"), (None, {}))
    semesters: list[SemesterOut] = []
    for sem_key, sem in sorted(((k, p) for k, p in records.items() if p.get("kind") == "semester"), key=lambda kv: kv[1].get("title", "")):
        units = []
        for ue_key, ue in sorted(kids(sem_key, "teaching_unit"), key=lambda kv: kv[1].get("title", "")):
            subjects = [subject(k, p) for k, p in sorted(kids(ue_key, "subject"), key=lambda kv: kv[1].get("title", ""))]
            units.append(UnitOut(key=ue_key, title=ue.get("title", ""), official_average=official(ue_key), subjects=subjects))
        semesters.append(SemesterOut(key=sem_key, title=sem.get("title", ""), official_average=official(sem_key), units=units))

    general = None
    if year_key:
        for _, p in kids(year_key, "grade"):
            if p.get("grade_kind") == "official_aggregate":
                general = _num(p.get("value"))
    return ResultsOut(academic_year=year.get("title"), general_average=general, semesters=semesters)


@router.get("/results", response_model=ResultsOut, tags=["grades"])
def results_from_sources(request: Request):
    """UE / subject / coefficient / mark tree synced from Pamplemousse."""
    sources_app = getattr(request.app.state, "sources_app", None)
    runtime = getattr(getattr(sources_app, "state", None), "runtime", None)
    if runtime is None:
        raise SourcesUnavailable("Source synchronization is not running")
    return build_results(read_records(runtime.engine))
