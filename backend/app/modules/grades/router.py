"""Academic endpoints under /api/v1/grades (docs/API_CONTRACTS.md § Academic)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.grades.academic_service import AcademicService
from app.modules.grades.schemas import (
    AllocationOut,
    AllocationRequest,
    CommentAnalysisOut,
    CommentAnalysisRequest,
    CorrectedPaperOut,
    CorrectedPaperPut,
    CurriculumOut,
    CurriculumPut,
    FeedbackCreate,
    FeedbackOut,
    FeedbackPatch,
    ForecastOut,
    ForecastRequest,
    ObjectiveCreate,
    ObjectiveOut,
    ObjectivePatch,
    ProgramCreate,
    ProgramOut,
    ProgramSummaryOut,
    RecordCreate,
    RecordOut,
    RecordPatch,
    RequiredOut,
    RequiredRequest,
    RulesOut,
    RulesPut,
    ScenarioCreate,
    ScenarioEvaluationOut,
    ScenarioOut,
    ScenarioPatch,
    SignalsOut,
    SimulateOut,
    SimulateRequest,
    TrendsOut,
    ValidationOut,
)
from app.modules.grades.service import GradesService

router = APIRouter(prefix="/grades", tags=["grades"])


def service(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> GradesService:
    return GradesService(db, clock)


User = Annotated[str, Depends(get_current_user_id)]
Svc = Annotated[GradesService, Depends(service)]


@router.get("/programs", response_model=list[ProgramOut])
def list_programs(svc: Svc, user_id: User):
    return svc.list_programs(user_id)


@router.post("/programs", response_model=ProgramOut, status_code=201)
def create_program(body: ProgramCreate, svc: Svc, user_id: User):
    return svc.create_program(user_id, body)


@router.get("/programs/{program_id}/curriculum", response_model=CurriculumOut)
def get_curriculum(program_id: str, svc: Svc, user_id: User):
    return svc.get_curriculum(user_id, program_id)


@router.put("/programs/{program_id}/curriculum", response_model=CurriculumOut)
def put_curriculum(program_id: str, body: CurriculumPut, svc: Svc, user_id: User):
    return svc.put_curriculum(user_id, program_id, body)


@router.get("/records", response_model=list[RecordOut])
def list_records(program_id: str, svc: Svc, user_id: User):
    return svc.list_records(user_id, program_id)


@router.post("/records", response_model=RecordOut, status_code=201)
def create_record(body: RecordCreate, svc: Svc, user_id: User):
    return svc.create_record(user_id, body)


@router.patch("/records/{record_id}", response_model=RecordOut)
def patch_record(record_id: str, body: RecordPatch, svc: Svc, user_id: User):
    return svc.patch_record(user_id, record_id, body)


@router.delete("/records/{record_id}", status_code=204)
def delete_record(record_id: str, svc: Svc, user_id: User):
    svc.delete_record(user_id, record_id)
    return Response(status_code=204)


@router.get("/objectives", response_model=list[ObjectiveOut])
def list_objectives(program_id: str, svc: Svc, user_id: User):
    return svc.list_objectives(user_id, program_id)


@router.post("/objectives", response_model=list[ObjectiveOut], status_code=201)
def create_objectives(body: ObjectiveCreate, svc: Svc, user_id: User):
    return svc.create_objectives(user_id, body)


@router.patch("/objectives/{objective_id}", response_model=ObjectiveOut)
def patch_objective(objective_id: str, body: ObjectivePatch, svc: Svc, user_id: User):
    return svc.patch_objective(user_id, objective_id, body)


@router.delete("/objectives/{objective_id}", status_code=204)
def delete_objective(objective_id: str, svc: Svc, user_id: User):
    svc.delete_objective(user_id, objective_id)
    return Response(status_code=204)


@router.get("/summary", response_model=ProgramSummaryOut)
def summary(program_id: str, svc: Svc, user_id: User, node_id: str | None = None):
    return svc.summary(user_id, program_id, node_id)


@router.post("/required", response_model=RequiredOut)
def required(body: RequiredRequest, svc: Svc, user_id: User):
    return svc.required(user_id, body)


@router.post("/simulate", response_model=SimulateOut)
def simulate(body: SimulateRequest, svc: Svc, user_id: User):
    return svc.simulate(user_id, body)


@router.get("/signals", response_model=SignalsOut)
def signals(program_id: str, svc: Svc, user_id: User):
    return svc.signals(user_id, program_id)


# ---- P1 ------------------------------------------------------------------------------------


def academic(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> AcademicService:
    return AcademicService(db, clock)


Acad = Annotated[AcademicService, Depends(academic)]


@router.get("/programs/{program_id}/rules", response_model=RulesOut)
def get_rules(program_id: str, svc: Acad, user_id: User):
    return svc.get_rules(user_id, program_id)


@router.put("/programs/{program_id}/rules", response_model=RulesOut)
def put_rules(program_id: str, body: RulesPut, svc: Acad, user_id: User):
    return svc.put_rules(user_id, program_id, body)


@router.get("/validation", response_model=ValidationOut)
def validation(program_id: str, svc: Acad, user_id: User):
    return svc.validation(user_id, program_id)


@router.get("/scenarios", response_model=list[ScenarioOut])
def list_scenarios(program_id: str, svc: Acad, user_id: User):
    return svc.list_scenarios(user_id, program_id)


@router.post("/scenarios", response_model=ScenarioOut, status_code=201)
def create_scenario(body: ScenarioCreate, svc: Acad, user_id: User):
    return svc.create_scenario(user_id, body)


@router.patch("/scenarios/{scenario_id}", response_model=ScenarioOut)
def patch_scenario(scenario_id: str, body: ScenarioPatch, svc: Acad, user_id: User):
    return svc.patch_scenario(user_id, scenario_id, body)


@router.delete("/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str, svc: Acad, user_id: User):
    svc.delete_scenario(user_id, scenario_id)
    return Response(status_code=204)


@router.get("/scenarios/{scenario_id}/evaluate", response_model=ScenarioEvaluationOut)
def evaluate_scenario(scenario_id: str, svc: Acad, user_id: User):
    return svc.evaluate_scenario(user_id, scenario_id)


@router.post("/records/{record_id}/feedback", response_model=FeedbackOut, status_code=201)
def create_feedback(record_id: str, body: FeedbackCreate, svc: Acad, user_id: User):
    return svc.create_feedback(user_id, record_id, body)


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(program_id: str, svc: Acad, user_id: User):
    return svc.list_feedback(user_id, program_id)


@router.patch("/feedback/{feedback_id}", response_model=FeedbackOut)
def patch_feedback(feedback_id: str, body: FeedbackPatch, svc: Acad, user_id: User):
    return svc.patch_feedback(user_id, feedback_id, body)


@router.get("/trends", response_model=TrendsOut)
def trends(program_id: str, svc: Acad, user_id: User):
    return svc.trends(user_id, program_id)


# ---- P2 ------------------------------------------------------------------------------------


@router.put("/records/{record_id}/corrected-paper", response_model=CorrectedPaperOut)
def put_corrected_paper(record_id: str, body: CorrectedPaperPut, svc: Acad, user_id: User):
    return svc.put_corrected_paper(user_id, record_id, body)


@router.get("/records/{record_id}/corrected-paper", response_model=CorrectedPaperOut)
def get_corrected_paper(record_id: str, svc: Acad, user_id: User):
    return svc.get_corrected_paper(user_id, record_id)


@router.post("/comments/analyze", response_model=CommentAnalysisOut)
def analyze_comments(body: CommentAnalysisRequest, svc: Acad, user_id: User):
    return svc.analyze_comments(body.text)


@router.post("/forecast", response_model=ForecastOut)
def grades_forecast(body: ForecastRequest, svc: Acad, user_id: User):
    return svc.forecast(user_id, body)


@router.post("/time-allocation", response_model=AllocationOut)
def time_allocation(body: AllocationRequest, svc: Acad, user_id: User):
    return svc.allocate_time(user_id, body)
