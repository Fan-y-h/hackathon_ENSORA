"""Unified subject catalog router (/api/v1/subjects)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.catalog.schemas import SubjectDetailOut, SubjectListOut
from app.modules.catalog.service import CatalogService

router = APIRouter(prefix="/subjects", tags=["catalog"])


def _p1_engine(request: Request):
    sources_app = getattr(request.app.state, "sources_app", None)
    runtime = getattr(getattr(sources_app, "state", None), "runtime", None)
    return getattr(runtime, "engine", None)


@router.get("", response_model=SubjectListOut)
def list_subjects(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    user_id: Annotated[str, Depends(get_current_user_id)],
):
    return CatalogService(db, clock, _p1_engine(request)).list_subjects(user_id)


@router.get("/{subject_id}", response_model=SubjectDetailOut)
def get_subject(
    subject_id: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    clock: Annotated[Clock, Depends(get_clock)],
    user_id: Annotated[str, Depends(get_current_user_id)],
):
    return CatalogService(db, clock, _p1_engine(request)).get_subject(user_id, subject_id)
