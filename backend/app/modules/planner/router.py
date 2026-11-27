"""Agenda endpoints (docs/API_CONTRACTS.md: /events, /assessments, /tasks)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.core.common import DEFAULT_LIMIT, MAX_LIMIT, Clock, Page, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.planner import decompose as _decompose
from app.modules.planner.plan_service import PlannerService
from app.modules.planner.schemas import (
    AcceptRequest,
    AssessmentOut,
    AssessmentPatch,
    AvailabilityOut,
    DecomposeOut,
    DecomposeRequest,
    EventConfirm,
    EventCreate,
    EventOut,
    EventPatch,
    EventType,
    PlannerStatusOut,
    PlanOut,
    PlanPreviewOut,
    PreviewRequest,
    ReplanRequest,
    ScopeStatus,
    SessionFinish,
    SessionOut,
    SessionPatch,
    SubtasksCreate,
    TaskCreate,
    TaskOut,
    TaskPatch,
    TaskStatus,
)
from app.modules.planner.service import AgendaService

router = APIRouter()

Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


def service(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> AgendaService:
    return AgendaService(db, clock)


User = Annotated[str, Depends(get_current_user_id)]
Svc = Annotated[AgendaService, Depends(service)]


# --- events ---------------------------------------------------------------------------
@router.get("/events", response_model=Page[EventOut], tags=["agenda"])
def list_events(
    svc: Svc,
    user_id: User,
    start: AwareDatetime,
    end: AwareDatetime,
    type: Annotated[list[EventType] | None, Query()] = None,
    include_cancelled: bool = False,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = svc.list_events(
        user_id, start, end, types=type, include_cancelled=include_cancelled, limit=limit, cursor=cursor
    )
    return Page(items=items, next_cursor=nxt)


@router.post("/events", response_model=EventOut, status_code=201, tags=["agenda"])
def create_event(body: EventCreate, svc: Svc, user_id: User):
    return svc.create_event(user_id, body)


@router.get("/events/{event_id}", response_model=EventOut, tags=["agenda"])
def get_event(event_id: str, svc: Svc, user_id: User):
    return svc.get_event(user_id, event_id)


@router.patch("/events/{event_id}", response_model=EventOut, tags=["agenda"])
def patch_event(event_id: str, body: EventPatch, svc: Svc, user_id: User):
    return svc.patch_event(user_id, event_id, body)


@router.delete("/events/{event_id}", status_code=204, tags=["agenda"])
def delete_event(event_id: str, svc: Svc, user_id: User, expected_revision: int | None = None):
    svc.delete_event(user_id, event_id, expected_revision)
    return Response(status_code=204)


@router.post("/events/{event_id}/confirm", response_model=EventOut, tags=["agenda"])
def confirm_event(event_id: str, body: EventConfirm, svc: Svc, user_id: User):
    return svc.confirm_event(user_id, event_id, body)


# --- assessments ----------------------------------------------------------------------
@router.get("/assessments", response_model=Page[AssessmentOut], tags=["agenda"])
def list_assessments(
    svc: Svc,
    user_id: User,
    status: str | None = Query(default="active", pattern="^(active|cancelled)$"),
    scope_status: ScopeStatus | None = None,
    after: AwareDatetime | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = svc.list_assessments(
        user_id, status=status, scope_status=scope_status, after=after, limit=limit, cursor=cursor
    )
    return Page(items=items, next_cursor=nxt)


@router.patch("/assessments/{assessment_id}", response_model=AssessmentOut, tags=["agenda"])
def patch_assessment(assessment_id: str, body: AssessmentPatch, svc: Svc, user_id: User):
    return svc.patch_assessment(user_id, assessment_id, body)


# --- tasks ----------------------------------------------------------------------------
@router.get("/tasks", response_model=Page[TaskOut], tags=["tasks"])
def list_tasks(
    svc: Svc,
    user_id: User,
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    subject_id: str | None = None,
    due_before: AwareDatetime | None = None,
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    items, nxt = svc.list_tasks(
        user_id, status=status, subject_id=subject_id, due_before=due_before, limit=limit, cursor=cursor
    )
    return Page(items=items, next_cursor=nxt)


@router.post("/tasks", response_model=TaskOut, status_code=201, tags=["tasks"])
def create_task(body: TaskCreate, svc: Svc, user_id: User):
    return svc.create_task(user_id, body)


@router.patch("/tasks/{task_id}", response_model=TaskOut, tags=["tasks"])
def patch_task(task_id: str, body: TaskPatch, svc: Svc, user_id: User):
    return svc.patch_task(user_id, task_id, body)


@router.delete("/tasks/{task_id}", status_code=204, tags=["tasks"])
def delete_task(task_id: str, svc: Svc, user_id: User, expected_revision: int | None = None):
    svc.delete_task(user_id, task_id, expected_revision)
    return Response(status_code=204)


# --- availability (proposed contract addition, see backend/README.md) -----------------
@router.get("/availability", response_model=AvailabilityOut, tags=["agenda"])
def availability(svc: Svc, user_id: User, start: AwareDatetime, end: AwareDatetime):
    return svc.availability(user_id, start, end)


# --- smart planner ------------------------------------------------------------------------


def planner(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> PlannerService:
    return PlannerService(db, clock)


Plnr = Annotated[PlannerService, Depends(planner)]


@router.post("/planner/preview", response_model=PlanPreviewOut, tags=["planner"])
def planner_preview(body: PreviewRequest, svc: Plnr, user_id: User):
    return svc.preview(user_id, body.horizon_days, algorithm=body.algorithm)


@router.post("/planner/accept", response_model=PlanOut, tags=["planner"])
def planner_accept(body: AcceptRequest, svc: Plnr, user_id: User):
    return svc.accept(user_id, body)


@router.post("/planner/replan", response_model=PlanPreviewOut, tags=["planner"])
def planner_replan(body: ReplanRequest, svc: Plnr, user_id: User):
    return svc.replan(user_id, body)


@router.get("/planner/status", response_model=PlannerStatusOut, tags=["planner"])
def planner_status(svc: Plnr, user_id: User):
    return svc.status(user_id)


@router.get("/plans/current", response_model=PlanOut, tags=["planner"])
def current_plan(svc: Plnr, user_id: User):
    return svc.current(user_id)


@router.patch("/sessions/{session_id}", response_model=SessionOut, tags=["planner"])
def patch_session(session_id: str, body: SessionPatch, svc: Plnr, user_id: User):
    return svc.patch_session(user_id, session_id, body)


@router.post("/sessions/{session_id}/start", response_model=SessionOut, tags=["planner"])
def start_session(session_id: str, svc: Plnr, user_id: User):
    return svc.start_session(user_id, session_id)


@router.post("/sessions/{session_id}/finish", response_model=SessionOut, tags=["planner"])
def finish_session(session_id: str, body: SessionFinish, svc: Plnr, user_id: User):
    return svc.finish_session(user_id, session_id, body)


# --- T21: project decomposition (heuristic, no AI) -------------------------------------------


@router.post("/tasks/decompose", response_model=DecomposeOut, tags=["tasks"])
def decompose_tasks(body: DecomposeRequest, user_id: User):
    steps = _decompose.decompose(body.text, body.total_estimated_min)
    return DecomposeOut(method=_decompose.METHOD, steps=[s.__dict__ for s in steps], notes=[
        "Editable proposal: nothing is saved until you POST /tasks/{id}/subtasks.",
        "Heuristic split of the text structure; the AI variant (G) can refine it later.",
    ])


@router.post("/tasks/{task_id}/subtasks", response_model=list[TaskOut], status_code=201, tags=["tasks"])
def create_subtasks(task_id: str, body: SubtasksCreate, svc: Svc, user_id: User):
    return [svc.create_task(user_id, item.model_copy(update={"parent_task_id": task_id})) for item in body.items]
