"""FastAPI router for RAG search, citations, AI providers/preferences, Tutor, and Learning Studio.

Implements ``/home/ensai/ensora-coordination/contracts/AI_RAG.md``.
Mounted under ``/api/v1`` by Agent 00 in ``backend/app/main.py``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.exam_guard import require_not_exam_mode
from app.core.identity import get_current_user_id
from app.modules.ai.schemas import (
    AIPreferencesOut,
    AIPreferencesPatch,
    AIProvidersOut,
    ArtifactGenerateRequest,
    ArtifactOut,
    ArtifactPatchRequest,
    CitationOut,
    ConversationCreate,
    ConversationMessageOut,
    ConversationOut,
    RagStatusOut,
    ReindexOut,
    RetrievalMode,
    SearchResponseOut,
    TutorAnswerOut,
    TutorAnswerRequest,
)
from app.modules.ai.service import AIService, resolve_ai_runtime
from app.modules.learning.service import learning_signals_for_planner


router = APIRouter()


def get_ai_service(
    request: Request,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> AIService:
    return AIService(resolve_ai_runtime(request.app), db, clock)


User = Annotated[str, Depends(get_current_user_id)]
Svc = Annotated[AIService, Depends(get_ai_service)]
ExamGuard = Depends(require_not_exam_mode)


# --- AI Providers & Preferences (accessible regardless of exam mode) ----------------------
@router.get("/ai/providers", response_model=AIProvidersOut, tags=["ai"])
def list_ai_providers(svc: Svc, _user_id: User):
    return svc.list_providers(force_refresh=False)


@router.post("/ai/providers/refresh", response_model=AIProvidersOut, tags=["ai"])
def refresh_ai_providers(svc: Svc, _user_id: User):
    return svc.list_providers(force_refresh=True)


@router.get("/ai/preferences", response_model=AIPreferencesOut, tags=["ai"])
def get_ai_preferences(svc: Svc, user_id: User):
    return svc.get_preferences(user_id)


@router.patch("/ai/preferences", response_model=AIPreferencesOut, tags=["ai"])
def patch_ai_preferences(body: AIPreferencesPatch, svc: Svc, user_id: User):
    return svc.patch_preferences(user_id, body)


# --- RAG Search, Citations, Index Status & Reindex (exam-guarded) -------------------------
@router.get("/search", response_model=SearchResponseOut, tags=["rag"], dependencies=[ExamGuard])
def rag_search(
    svc: Svc,
    user_id: User,
    q: Annotated[str, Query(min_length=1, max_length=1000)],
    mode: RetrievalMode = "hybrid",
    subject_id: str | None = None,
    document_ids: Annotated[list[str] | None, Query(max_length=50)] = None,
    resource_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=30)] = 8,
):
    return svc.search(
        user_id,
        q=q,
        mode=mode,
        subject_id=subject_id,
        document_ids=document_ids,
        resource_type=resource_type,
        limit=limit,
    )


@router.get("/citations/{chunk_id}", response_model=CitationOut, tags=["rag"], dependencies=[ExamGuard])
def get_citation(chunk_id: str, svc: Svc, user_id: User):
    return svc.resolve_citation(user_id, chunk_id)


@router.get("/rag/status", response_model=RagStatusOut, tags=["rag"], dependencies=[ExamGuard])
def get_rag_status(svc: Svc, user_id: User, limit: Annotated[int, Query(ge=1, le=500)] = 200):
    return svc.rag_status(user_id, limit=limit)


@router.post("/rag/reindex", response_model=ReindexOut, tags=["rag"], dependencies=[ExamGuard])
def reindex_rag(svc: Svc, user_id: User):
    return svc.reindex(user_id)


# --- AI Tutor & Conversations (exam-guarded) ----------------------------------------------
@router.get("/conversations", response_model=list[ConversationOut], tags=["tutor"], dependencies=[ExamGuard])
def list_conversations(svc: Svc, user_id: User):
    return svc.list_conversations(user_id)


@router.post("/conversations", response_model=ConversationOut, status_code=201, tags=["tutor"], dependencies=[ExamGuard])
def create_conversation(body: ConversationCreate, svc: Svc, user_id: User):
    return svc.create_conversation(user_id, body)


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=list[ConversationMessageOut],
    tags=["tutor"],
    dependencies=[ExamGuard],
)
def list_conversation_messages(conversation_id: str, svc: Svc, user_id: User):
    return svc.list_messages(user_id, conversation_id)


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=TutorAnswerOut,
    tags=["tutor"],
    dependencies=[ExamGuard],
)
def post_conversation_message(conversation_id: str, body: TutorAnswerRequest, svc: Svc, user_id: User):
    return svc.tutor_answer(user_id, body, conversation_id=conversation_id)


@router.post("/tutor/answer", response_model=TutorAnswerOut, tags=["tutor"], dependencies=[ExamGuard])
def tutor_answer(body: TutorAnswerRequest, svc: Svc, user_id: User):
    return svc.tutor_answer(user_id, body)


# --- Learning Studio Artifacts (exam-guarded) ---------------------------------------------
@router.post("/artifacts/generate", response_model=ArtifactOut, tags=["studio"], dependencies=[ExamGuard])
def generate_artifact(body: ArtifactGenerateRequest, svc: Svc, user_id: User):
    return svc.generate_artifact(user_id, body)


@router.get("/artifacts", response_model=list[ArtifactOut], tags=["studio"], dependencies=[ExamGuard])
def list_artifacts(
    svc: Svc,
    user_id: User,
    type: str | None = None,
    subject_id: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return svc.list_artifacts(user_id, artifact_type=type, subject_id=subject_id, limit=limit)


@router.get("/artifacts/{artifact_id}", response_model=ArtifactOut, tags=["studio"], dependencies=[ExamGuard])
def get_artifact(artifact_id: str, svc: Svc, user_id: User):
    return svc.get_artifact(user_id, artifact_id)


@router.patch("/artifacts/{artifact_id}", response_model=ArtifactOut, tags=["studio"], dependencies=[ExamGuard])
def patch_artifact(artifact_id: str, body: ArtifactPatchRequest, svc: Svc, user_id: User):
    return svc.patch_artifact(user_id, artifact_id, body)


@router.get(
    "/artifacts/{artifact_id}/versions",
    response_model=list[ArtifactOut],
    tags=["studio"],
    dependencies=[ExamGuard],
)
def list_artifact_versions(artifact_id: str, svc: Svc, user_id: User):
    return svc.list_artifact_versions(user_id, artifact_id)


# --- Deterministic Learning Intelligence Signals (for Smart Planner & UI) -----------------
@router.get("/learning/signals", tags=["learning"])
def get_learning_signals(
    user_id: User,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    horizon_days: Annotated[int, Query(ge=1, le=31)] = 7,
):
    return learning_signals_for_planner(db, clock, user_id, horizon_days=horizon_days)
