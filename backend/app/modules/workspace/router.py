"""Workspace endpoints: /focus, /notebooks, /notes, /errors."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.workspace.schemas import (
    ErrorCreate,
    ErrorOut,
    ErrorPatch,
    FocusFinish,
    FocusOut,
    FocusStart,
    FocusTransition,
    NotebookCreate,
    NotebookOut,
    NoteCreate,
    NoteOut,
    NotePatch,
    NoteVersionOut,
)
from app.modules.workspace.service import WorkspaceService

router = APIRouter()


def service(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> WorkspaceService:
    return WorkspaceService(db, clock)


User = Annotated[str, Depends(get_current_user_id)]
Svc = Annotated[WorkspaceService, Depends(service)]


@router.post("/focus/start", response_model=FocusOut, status_code=201, tags=["focus"])
def start_focus(body: FocusStart, svc: Svc, user_id: User):
    return svc.start_focus(user_id, body)


@router.get("/focus/active", response_model=FocusOut | None, tags=["focus"])
def active_focus(svc: Svc, user_id: User):
    return svc.active_focus(user_id)


@router.get("/focus/{focus_id}", response_model=FocusOut, tags=["focus"])
def get_focus(focus_id: str, svc: Svc, user_id: User):
    return svc.get_focus(user_id, focus_id)


@router.post("/focus/{focus_id}/transition", response_model=FocusOut, tags=["focus"])
def transition(focus_id: str, body: FocusTransition, svc: Svc, user_id: User):
    return svc.transition(user_id, focus_id, body)


@router.post("/focus/{focus_id}/finish", response_model=FocusOut, tags=["focus"])
def finish_focus(focus_id: str, body: FocusFinish, svc: Svc, user_id: User):
    return svc.finish_focus(user_id, focus_id, body)


@router.get("/notebooks", response_model=list[NotebookOut], tags=["notes"])
def list_notebooks(svc: Svc, user_id: User):
    return svc.list_notebooks(user_id)


@router.post("/notebooks", response_model=NotebookOut, status_code=201, tags=["notes"])
def create_notebook(body: NotebookCreate, svc: Svc, user_id: User):
    return svc.create_notebook(user_id, body)


@router.get("/notes", response_model=list[NoteOut], tags=["notes"])
def list_notes(svc: Svc, user_id: User, notebook_id: str | None = None, concept_id: str | None = None,
               q: str | None = None):
    return svc.list_notes(user_id, notebook_id, concept_id, q)


@router.post("/notes", response_model=NoteOut, status_code=201, tags=["notes"])
def create_note(body: NoteCreate, svc: Svc, user_id: User):
    return svc.create_note(user_id, body)


@router.get("/notes/{note_id}", response_model=NoteOut, tags=["notes"])
def get_note(note_id: str, svc: Svc, user_id: User):
    return svc.get_note(user_id, note_id)


@router.patch("/notes/{note_id}", response_model=NoteOut, tags=["notes"])
def patch_note(note_id: str, body: NotePatch, svc: Svc, user_id: User):
    return svc.patch_note(user_id, note_id, body)


@router.delete("/notes/{note_id}", status_code=204, tags=["notes"])
def delete_note(note_id: str, svc: Svc, user_id: User):
    svc.delete_note(user_id, note_id)
    return Response(status_code=204)


@router.get("/notes/{note_id}/versions", response_model=list[NoteVersionOut], tags=["notes"])
def note_versions(note_id: str, svc: Svc, user_id: User):
    return svc.note_versions(user_id, note_id)


@router.get("/errors", response_model=list[ErrorOut], tags=["errors"])
def list_errors(svc: Svc, user_id: User, resolved: bool | None = None, concept_id: str | None = None):
    return svc.list_errors(user_id, resolved, concept_id)


@router.post("/errors", response_model=ErrorOut, status_code=201, tags=["errors"])
def create_error(body: ErrorCreate, svc: Svc, user_id: User):
    return svc.create_error(user_id, body)


@router.patch("/errors/{error_id}", response_model=ErrorOut, tags=["errors"])
def patch_error(error_id: str, body: ErrorPatch, svc: Svc, user_id: User):
    return svc.patch_error(user_id, error_id, body)
