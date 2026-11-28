"""Unified AI, RAG, Tutor & Learning Studio service layer.

Bridges:
- P1 read-only documents/chunks (``sources_app.state.runtime`` when mounted) → local
  derived ``LocalRagIndexStore`` (SQLite FTS5 + dense vectors).
- Provider-neutral ``AIProviderRegistry`` + ``AIModelRouter`` with explicit local/cloud
  consent, daily token budgets, honest unavailability, and transparent extractive fallback
  (``provider="extractive-local"``, ``model="extractive-rag-v1"``, ``is_fallback=True``).
- P3 ``LearningService`` for exam-mode guards and persisting generated flashcards/quizzes.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import time
from typing import Any

from sqlalchemy.orm import Session

from app.contracts.ai import (
    AISelectionPolicy,
    AITask,
    GenerationRequest as GatewayRequest,
    ModelSelection,
    ProviderPreference,
)
from app.core.ai import (
    AIModelRouter,
    AIProviderAdapter,
    AIProviderRegistry,
    AISelectionError,
    GatewayUnavailable,
    discover_default_adapters,
)
from app.core.common import Clock
from app.core.database import LOCAL_USER as P1_LOCAL_USER
from app.core.errors import AppError, Conflict, NotFound
from app.modules.ai.schemas import (
    AIPreferencesOut,
    AIPreferencesPatch,
    AIProviderModelOut,
    AIProvidersOut,
    AIProviderStatusOut,
    ArtifactGenerateRequest,
    ArtifactOut,
    ArtifactPatchRequest,
    CitationOut,
    ConversationCreate,
    ConversationMessageOut,
    ConversationOut,
    DocumentIndexStateOut,
    RagStatusOut,
    ReindexOut,
    SearchHitOut,
    SearchResponseOut,
    TutorAnswerOut,
    TutorAnswerRequest,
    UsageOut,
)
from app.modules.ai.store import LocalAIStateStore
from app.modules.learning.schemas import CardCreate, DeckCreate, QuestionIn, QuizCreate
from app.modules.learning.service import LearningService
from app.modules.rag.artifacts import (
    LocalArtifactStore,
    StoredArtifactRecord,
    to_p3_flashcard_deck_payload,
    to_p3_quiz_payload,
)
from app.modules.rag.contracts import (
    ArtifactGenerationSpec,
    Citation,
    GenerationRequest as RagGenerationRequest,
    GenerationResponse as RagGenerationResponse,
    QueryScope,
)
from app.modules.rag.embeddings import LocalMultilingualHashEmbedder
from app.modules.rag.engine import RagEngine
from app.modules.rag.retrieval import HybridRetriever
from app.modules.rag.store import LocalRagIndexStore

logger = logging.getLogger(__name__)

EXTRACTIVE_PROVIDER = "extractive-local"
EXTRACTIVE_MODEL = "extractive-rag-v1"


class AIProviderUnavailable(AppError):
    status_code = 503
    code = "ai_provider_unavailable"


class _UnifiedTextGenerationPort:
    """Adapter bridging ``RagEngine`` onto ``AIModelRouter`` with per-user preferences & budget."""

    _TASK_ALIASES = {
        "tutor_conversation": AITask.TUTOR,
        "rag_answer": AITask.RAG_ANSWER,
        "course_summary": AITask.COURSE_SUMMARY,
        "revision_sheet": AITask.REVISION_SHEET,
        "quiz": AITask.QUIZ,
        "flashcards": AITask.FLASHCARDS,
        "exercise": AITask.EXERCISE,
        "exercises": AITask.EXERCISE,
        "correction": AITask.CORRECTION,
        "exercise_explanation": AITask.CORRECTION,
        "concept_extraction": AITask.CONCEPT_EXTRACTION,
        "prerequisite_suggestions": AITask.CONCEPT_EXTRACTION,
        "study_recommendation": AITask.STUDY_RECOMMENDATION,
    }

    def __init__(
        self,
        *,
        router: AIModelRouter,
        state_store: LocalAIStateStore,
        user_id: str,
        prefs: dict[str, Any],
    ) -> None:
        self._router = router
        self._state_store = state_store
        self._user_id = user_id
        self._prefs = prefs

    def generate(self, request: RagGenerationRequest) -> RagGenerationResponse:
        if not self._prefs.get("enabled", True):
            raise GatewayUnavailable("ai_disabled")

        budget = int(self._prefs.get("daily_token_budget", 100_000))
        used_today = int(self._prefs.get("tokens_used_today", 0))
        if budget > 0 and used_today >= budget:
            raise GatewayUnavailable("daily_token_budget_exceeded")

        task = self._TASK_ALIASES.get(request.task_type, AITask.RAG_ANSWER)
        pref_provider = self._prefs.get("preferred_provider")
        pref_model = self._prefs.get("preferred_model")
        default_sel = (
            ModelSelection(provider_id=pref_provider, model_id=pref_model)
            if pref_provider and pref_model
            else None
        )

        # Build authorized fallback list from currently available models matching locality policy
        remote_allowed = bool(self._prefs.get("cloud_data_consent", False))
        available_models = self._router.registry.models()
        auto_candidates: list[ModelSelection] = []
        allowed_providers: set[str] = set()
        for (pid, _mid), (_adapter, desc) in sorted(
            available_models.items(),
            key=lambda item: (not item[1][1].local, item[0][0], item[0][1]),
        ):
            if desc.local or remote_allowed:
                allowed_providers.add(pid)
                auto_candidates.append(desc.selection)

        if pref_provider:
            allowed_providers.add(pref_provider)
        if request.provider_override:
            allowed_providers.add(request.provider_override)

        override = None
        if request.provider_override or request.model_override:
            if not (request.provider_override and request.model_override) and default_sel is None:
                raise GatewayUnavailable("partial_model_override_without_default")
            override = ModelSelection(
                provider_id=request.provider_override or default_sel.provider_id,  # type: ignore[union-attr]
                model_id=request.model_override or default_sel.model_id,  # type: ignore[union-attr]
            )

        provider_pref = ProviderPreference(
            enabled=True,
            cloud_data_consent=remote_allowed,
            automatic=default_sel is None and override is None,
            default=default_sel,
            fallback_candidates=auto_candidates[:8],
        )
        policy = AISelectionPolicy(
            enabled=True,
            remote_allowed=remote_allowed,
            allowed_providers=allowed_providers,
            fallback_candidates=auto_candidates[:8],
        )
        gw_req = GatewayRequest(
            task=task,
            messages=[
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            override=override,
            max_output_tokens=request.max_tokens,
            temperature=request.temperature,
            structured_output=request.json_mode,
            allow_fallback=False,
        )
        try:
            adapter, model = self._router.select(gw_req, provider_pref, policy)
            result = adapter.generate(gw_req, model)
        except AISelectionError as exc:
            raise GatewayUnavailable(exc.code) from None
        except Exception:
            raise GatewayUnavailable("provider_request_failed") from None

        in_tok = int(result.input_tokens or 0)
        out_tok = int(result.output_tokens or 0)
        self._state_store.record_token_usage(self._user_id, in_tok + out_tok)
        return RagGenerationResponse(
            content=result.content,
            provider=result.effective_model.provider_id,
            model=result.effective_model.model_id,
            finish_reason=result.finish_reason or "stop",
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost_usd=result.estimated_cost,
            fallback_reason=result.fallback_reason,
        )


class AIRuntimeContext:
    """Lazy per-app container for the RAG index, artifact store, AI state, and provider registry."""

    def __init__(
        self,
        *,
        rag_db_path: Path | str,
        p1_db_path: Path | None = None,
        adapters: dict[str, AIProviderAdapter] | None = None,
    ) -> None:
        self.rag_db_path = rag_db_path
        self.p1_db_path = p1_db_path
        self.embedder = LocalMultilingualHashEmbedder()
        self.rag_store = LocalRagIndexStore(
            rag_db_path,
            embedder=self.embedder,
            p1_db_path=p1_db_path,
        )
        self.retriever = HybridRetriever(self.rag_store)
        self.artifact_store = LocalArtifactStore(rag_db_path, store=self.rag_store)
        self.state_store = LocalAIStateStore(rag_db_path)
        self.registry = AIProviderRegistry()
        for pid, adapter in (adapters if adapters is not None else discover_default_adapters()).items():
            self.registry.register(pid, adapter)
        self.model_router = AIModelRouter(self.registry)

    def close(self) -> None:
        self.rag_store.close()
        self.artifact_store.close()
        self.state_store.close()


def resolve_ai_runtime(app) -> AIRuntimeContext:
    """Return (or lazily initialize) the ``AIRuntimeContext`` bound to ``app.state``."""
    existing = getattr(app.state, "ai_runtime", None)
    if isinstance(existing, AIRuntimeContext):
        # If sources_app started after initial creation, attach its P1 DB path transparently
        p1_path = _p1_db_path_from_app(app)
        if p1_path is not None and existing.p1_db_path is None:
            existing.p1_db_path = p1_path
            existing.rag_store._p1_db_path = p1_path
        return existing

    p1_path = _p1_db_path_from_app(app)
    db_url = str(getattr(getattr(app.state, "db", None), "engine", None).url) if getattr(app.state, "db", None) else ""
    if db_url in ("sqlite://", "sqlite:///:memory:"):
        rag_db: Path | str = ":memory:"
    elif p1_path is not None:
        rag_db = p1_path.parent / "rag_index.sqlite3"
    else:
        data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[4] / "data" / "runtime"))
        rag_db = data_dir / "rag_index.sqlite3"

    ctx = AIRuntimeContext(rag_db_path=rag_db, p1_db_path=p1_path)
    app.state.ai_runtime = ctx
    return ctx


def _p1_db_path_from_app(app) -> Path | None:
    sources_app = getattr(app.state, "sources_app", None)
    runtime = getattr(getattr(sources_app, "state", None), "runtime", None)
    if runtime is None:
        return None
    local_dir = getattr(getattr(runtime, "settings", None), "local_data_dir", None)
    if local_dir is None:
        return None
    candidate = Path(local_dir) / "ensora.sqlite3"
    return candidate if candidate.exists() else None


def _citation_to_out(c: Citation) -> CitationOut:
    content_url = (
        None
        if c.revoked
        else f"/api/v1/sources/documents/{c.document_id}/versions/{c.version_id}/content"
    )
    return CitationOut(
        citation_id=c.citation_id,
        document_id=c.document_id,
        version_id=c.version_id,
        chunk_id=c.chunk_id,
        title=c.title,
        locator=dict(c.locator),
        excerpt="" if c.revoked else c.excerpt,
        content_url=content_url,
        source_url=None if c.revoked else c.source_url,
        subject_id=c.subject_id,
        academic_year=c.academic_year,
        resource_type=c.resource_type,
        revoked=c.revoked,
    )


class AIService:
    """High-level orchestration service for RAG search, AI Tutor, and Learning Studio."""

    def __init__(self, ctx: AIRuntimeContext, db: Session, clock: Clock) -> None:
        self.ctx = ctx
        self.db = db
        self.clock = clock

    def _rag_user_id(self, user_id: str) -> str:
        """Map to P1 ``LOCAL_USER`` when bound to P1's single-user store, else keep ``user_id``."""
        return P1_LOCAL_USER if self.ctx.p1_db_path is not None else user_id

    def _ensure_initial_index(self, user_id: str) -> None:
        """If bound to P1 and the derived index is currently empty, run one incremental sync."""
        if self.ctx.p1_db_path is None or not self.ctx.p1_db_path.is_file():
            return
        rag_uid = self._rag_user_id(user_id)
        if self.ctx.rag_store.stats(user_id=rag_uid)["indexed_documents"] == 0:
            try:
                self.ctx.rag_store.sync_from_p1_db(self.ctx.p1_db_path, user_id=rag_uid)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Lazy RAG initial sync skipped (%s)", type(exc).__name__)

    # --- Providers & Preferences ----------------------------------------------------------
    def list_providers(self, *, force_refresh: bool = False) -> AIProvidersOut:
        statuses = self.ctx.registry.status(force_refresh=force_refresh)
        items = [
            AIProviderStatusOut(
                provider_id=st.provider_id,
                kind=str(st.kind),
                local=st.local,
                configured=st.configured,
                available=st.available,
                error_code=st.error_code,
                models=[
                    AIProviderModelOut(
                        provider_id=m.selection.provider_id,
                        model_id=m.selection.model_id,
                        local=m.local,
                        structured_output=m.capabilities.structured_output,
                        vision=m.capabilities.vision,
                        context_tokens=m.capabilities.context_tokens,
                    )
                    for m in st.models
                ],
            )
            for st in statuses
        ]
        return AIProvidersOut(items=items)

    def get_preferences(self, user_id: str) -> AIPreferencesOut:
        return AIPreferencesOut.model_validate(self.ctx.state_store.get_preferences(user_id))

    def patch_preferences(self, user_id: str, body: AIPreferencesPatch) -> AIPreferencesOut:
        patch = {k: getattr(body, k) for k in body.model_fields_set}
        updated = self.ctx.state_store.update_preferences(user_id, patch)
        self.ctx.registry.invalidate_status()
        return AIPreferencesOut.model_validate(updated)

    # --- Indexing, Status, Search & Citations ---------------------------------------------
    def rag_status(self, user_id: str, *, limit: int = 200) -> RagStatusOut:
        rag_uid = self._rag_user_id(user_id)
        data = self.ctx.rag_store.document_index_status(user_id=rag_uid, limit=limit)
        return RagStatusOut(
            p1_connected=bool(data["p1_connected"]),
            summary=dict(data["summary"]),
            index_stats=dict(data["index_stats"]),
            documents=[DocumentIndexStateOut.model_validate(d) for d in data["documents"]],
        )

    def reindex(self, user_id: str) -> ReindexOut:
        rag_uid = self._rag_user_id(user_id)
        if self.ctx.p1_db_path is None or not self.ctx.p1_db_path.is_file():
            stats = self.ctx.rag_store.stats(user_id=rag_uid)
            return ReindexOut(
                documents_indexed=stats["indexed_documents"],
                chunks_indexed=stats["indexed_chunks"],
                skipped_unchanged=stats["indexed_documents"],
                removed_documents=0,
                embedding_model=stats["embedding_space_id"],
                embedding_kind=stats["embedding_kind"],
                duration_ms=0,
                warnings=["sources_unavailable_local_index_kept"],
            )
        receipt = self.ctx.rag_store.sync_from_p1_db(self.ctx.p1_db_path, user_id=rag_uid)
        return ReindexOut(
            documents_indexed=receipt.indexed_documents,
            chunks_indexed=receipt.indexed_chunks,
            skipped_unchanged=receipt.skipped_unchanged,
            removed_documents=receipt.removed_documents,
            embedding_model=receipt.embedding_space_id,
            embedding_kind=receipt.embedding_kind,
            duration_ms=int(round(receipt.elapsed_ms)),
            warnings=list(receipt.warnings),
        )

    def search(
        self,
        user_id: str,
        *,
        q: str,
        mode: str = "hybrid",
        subject_id: str | None = None,
        document_ids: list[str] | None = None,
        resource_type: str | None = None,
        limit: int = 8,
    ) -> SearchResponseOut:
        self._ensure_initial_index(user_id)
        rag_uid = self._rag_user_id(user_id)
        scope = QueryScope(
            user_id=rag_uid,
            subject_id=subject_id,
            document_ids=tuple(document_ids or ()),
            resource_types=(resource_type,) if resource_type else (),
            exam_mode=False,
            mode=mode,  # type: ignore[arg-type]
            top_k=limit,
        )
        res = self.ctx.retriever.retrieve(scope, q)
        stats = self.ctx.rag_store.stats(user_id=rag_uid)
        hits: list[SearchHitOut] = []
        for chunk, cite in zip(res.hits, res.citations):
            score = chunk.rerank_score or chunk.fused_score or max(chunk.lexical_score, chunk.semantic_score)
            hits.append(
                SearchHitOut(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    version_id=chunk.version_id,
                    title=chunk.title,
                    subject_id=chunk.subject_id,
                    resource_type=chunk.resource_type,
                    score=round(float(score), 4),
                    excerpt=cite.excerpt,
                    locator=dict(chunk.locator),
                    citation_id=cite.citation_id,
                )
            )
        return SearchResponseOut(
            query=q,
            mode=mode,  # type: ignore[arg-type]
            indexed_chunks=int(stats["indexed_chunks"]),
            evidence_status=res.evidence_status,
            index_stale=res.index_stale,
            warnings=list(res.warnings),
            hits=hits,
        )

    def resolve_citation(self, user_id: str, chunk_id: str) -> CitationOut:
        rag_uid = self._rag_user_id(user_id)
        cite = self.ctx.rag_store.resolve_citation(chunk_id, user_id=rag_uid, exam_mode=False)
        if cite is None or cite.revoked:
            raise NotFound("Citation not found or source revoked", code="citation_unavailable")
        return _citation_to_out(cite)

    # --- Conversations & AI Tutor ---------------------------------------------------------
    def list_conversations(self, user_id: str) -> list[ConversationOut]:
        return [ConversationOut.model_validate(c) for c in self.ctx.state_store.list_conversations(user_id)]

    def create_conversation(self, user_id: str, body: ConversationCreate) -> ConversationOut:
        conv = self.ctx.state_store.create_conversation(
            user_id, subject_id=body.subject_id, title=body.title
        )
        return ConversationOut.model_validate(conv)

    def list_messages(self, user_id: str, conversation_id: str) -> list[ConversationMessageOut]:
        if self.ctx.state_store.get_conversation(user_id, conversation_id) is None:
            raise NotFound("Conversation not found")
        rag_uid = self._rag_user_id(user_id)
        rows = self.ctx.state_store.list_messages(user_id, conversation_id)
        out: list[ConversationMessageOut] = []
        for row in rows:
            verified_cites: list[CitationOut] = []
            for raw_c in row.get("citations") or []:
                cid = raw_c.get("chunk_id")
                live = (
                    self.ctx.rag_store.resolve_citation(
                        str(cid), user_id=rag_uid, citation_id=raw_c.get("citation_id"), exam_mode=False
                    )
                    if cid
                    else None
                )
                if live is not None:
                    verified_cites.append(_citation_to_out(live))
                else:
                    verified_cites.append(
                        CitationOut(
                            citation_id=str(raw_c.get("citation_id") or ""),
                            document_id=str(raw_c.get("document_id") or ""),
                            version_id=str(raw_c.get("version_id") or ""),
                            chunk_id=str(cid or ""),
                            title="Unavailable source",
                            locator={},
                            excerpt="",
                            content_url=None,
                            revoked=True,
                        )
                    )
            out.append(
                ConversationMessageOut(
                    id=row["id"],
                    conversation_id=row["conversation_id"],
                    role=row["role"],
                    content_markdown=row["content_markdown"],
                    citations=verified_cites,
                    evidence_status=row["evidence_status"],
                    provider=row["provider"],
                    model=row["model"],
                    generation_mode=row["generation_mode"],
                    is_fallback=bool(row["is_fallback"]),
                    fallback_reason=row["fallback_reason"],
                    created_at=row["created_at"],
                )
            )
        return out

    def tutor_answer(
        self,
        user_id: str,
        body: TutorAnswerRequest,
        *,
        conversation_id: str | None = None,
    ) -> TutorAnswerOut:
        self._ensure_initial_index(user_id)
        conv_id = conversation_id or body.conversation_id
        if conv_id is not None:
            conv = self.ctx.state_store.get_conversation(user_id, conv_id)
            if conv is None:
                raise NotFound("Conversation not found")
            effective_subject = body.subject_id or conv.get("subject_id")
        else:
            conv = self.ctx.state_store.create_conversation(
                user_id,
                subject_id=body.subject_id,
                title=body.question.strip()[:100],
            )
            conv_id = conv["id"]
            effective_subject = body.subject_id

        history_rows = self.ctx.state_store.list_messages(user_id, conv_id)
        history_pairs = [(r["role"], r["content_markdown"]) for r in history_rows[-6:]]

        self.ctx.state_store.append_message(
            user_id=user_id,
            conversation_id=conv_id,
            role="user",
            content_markdown=body.question,
        )

        rag_uid = self._rag_user_id(user_id)
        scope = QueryScope(
            user_id=rag_uid,
            subject_id=effective_subject,
            document_ids=tuple(body.document_ids),
            locale=body.locale,
            exam_mode=False,
        )

        prefs = self.ctx.state_store.get_preferences(user_id)
        port = _UnifiedTextGenerationPort(
            router=self.ctx.model_router,
            state_store=self.ctx.state_store,
            user_id=user_id,
            prefs=prefs,
        )
        engine = RagEngine(self.ctx.rag_store, retriever=self.ctx.retriever, generator=port)

        is_fallback = False
        fallback_reason: str | None = None
        generation_mode: str = "llm"

        explicit_override = bool(body.provider_override or body.model_override)
        try:
            ans = engine.answer(
                scope,
                body.question,
                mode=body.mode,
                conversation_history=history_pairs,
                provider_override=body.provider_override,
                model_override=body.model_override,
                ai_disabled=not prefs.get("enabled", True),
            )
            if ans.abstained:
                generation_mode = "abstention"
            elif ans.provider == "extractive_local":
                is_fallback = True
                fallback_reason = "ai_disabled"
                generation_mode = "extractive_fallback"
        except GatewayUnavailable as exc:
            # Explicit unavailable override or disabled fallback -> honest HTTP 503
            if explicit_override or not prefs.get("allow_extractive_fallback", True):
                raise AIProviderUnavailable(
                    f"AI provider unavailable ({exc.code})",
                    details={"reason": exc.code},
                ) from None
            is_fallback = True
            fallback_reason = exc.code
            generation_mode = "extractive_fallback"
            ans = engine.answer(
                scope,
                body.question,
                mode=body.mode,
                conversation_history=history_pairs,
                ai_disabled=True,
            )

        provider = EXTRACTIVE_PROVIDER if is_fallback else ans.provider
        model = EXTRACTIVE_MODEL if is_fallback else ans.model
        citations_out = [_citation_to_out(c) for c in ans.citations]

        saved_msg = self.ctx.state_store.append_message(
            user_id=user_id,
            conversation_id=conv_id,
            role="assistant",
            content_markdown=ans.answer_markdown,
            citations=[c.model_dump(mode="json") for c in citations_out],
            evidence_status=ans.evidence_status,
            provider=provider,
            model=model,
            generation_mode=generation_mode,
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
        )

        return TutorAnswerOut(
            conversation_id=conv_id,
            message_id=saved_msg["id"],
            answer_markdown=ans.answer_markdown,
            citations=citations_out,
            evidence_status=ans.evidence_status,
            provider=provider,
            model=model,
            generation_mode=generation_mode,  # type: ignore[arg-type]
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
            usage=UsageOut(
                tokens_in=int(ans.usage.get("input_tokens") or 0),
                tokens_out=int(ans.usage.get("output_tokens") or 0),
                estimated_cost_usd=ans.usage.get("estimated_cost_usd"),
            ),
            follow_up_questions=list(ans.follow_up_questions),
            warnings=list(ans.warnings),
            abstained=ans.abstained,
            prompt_injection_detected=ans.prompt_injection_detected,
        )

    # --- Learning Studio Artifacts --------------------------------------------------------
    def _record_to_artifact_out(self, user_id: str, rec: StoredArtifactRecord) -> ArtifactOut:
        link = self.ctx.state_store.get_artifact_link(user_id, rec.artifact_id)
        art = rec.artifact
        is_fb = bool(link["is_fallback"] or art.provider in ("extractive_local", EXTRACTIVE_PROVIDER))
        provider = EXTRACTIVE_PROVIDER if art.provider == "extractive_local" else art.provider
        model = EXTRACTIVE_MODEL if art.model == "deterministic_rag" and is_fb else art.model
        return ArtifactOut(
            id=rec.artifact_id,
            type=art.artifact_kind,
            title=art.title,
            subject_id=link["subject_id"],
            version=rec.version,
            parent_artifact_id=rec.parent_artifact_id,
            cache_hit=rec.cache_hit,
            stale=rec.stale,
            stale_reasons=list(rec.stale_reasons),
            user_edited=rec.user_edited,
            provider=provider,
            model=model,
            is_fallback=is_fb,
            fallback_reason=link["fallback_reason"],
            evidence_status=art.evidence_status,
            coverage_ratio=round(float(art.coverage_ratio), 3),
            omitted_sections=list(art.omitted_sections),
            content_markdown=art.content_markdown,
            structured_payload=dict(art.structured_payload),
            citations=[_citation_to_out(c) for c in art.citations],
            linked_deck_id=link["linked_deck_id"],
            linked_quiz_id=link["linked_quiz_id"],
            created_at=rec.created_at,
            updated_at=rec.updated_at,
        )

    def generate_artifact(self, user_id: str, body: ArtifactGenerateRequest) -> ArtifactOut:
        self._ensure_initial_index(user_id)
        rag_uid = self._rag_user_id(user_id)
        item_count = body.item_count or max(4, min(12, body.duration_min // 3))
        scope = QueryScope(
            user_id=rag_uid,
            subject_id=body.subject_id,
            concept_ids=tuple(body.concept_ids),
            document_ids=tuple(body.document_ids),
            locale=body.locale,
            exam_mode=False,
        )
        spec = ArtifactGenerationSpec(
            kind=body.type,  # type: ignore[arg-type]
            scope=scope,
            topic=(body.topic or "").strip(),
            difficulty=body.difficulty,
            item_count=item_count,
            detail_level=body.detail_level,
            user_instructions=(body.user_instructions or "").strip(),
            provider_override=body.provider_override,
            model_override=body.model_override,
            force_regenerate=body.force_regenerate,
        )

        prefs = self.ctx.state_store.get_preferences(user_id)
        port = _UnifiedTextGenerationPort(
            router=self.ctx.model_router,
            state_store=self.ctx.state_store,
            user_id=user_id,
            prefs=prefs,
        )
        engine = RagEngine(
            self.ctx.rag_store,
            retriever=self.ctx.retriever,
            generator=port,
            artifact_store=self.ctx.artifact_store,
        )

        is_fallback = False
        fallback_reason: str | None = None
        explicit_override = bool(body.provider_override or body.model_override)

        # Store artifacts keyed under P3 user_id for strict user isolation
        scoped_spec = ArtifactGenerationSpec(
            kind=spec.kind,
            scope=QueryScope(
                user_id=rag_uid,
                subject_id=scope.subject_id,
                concept_ids=scope.concept_ids,
                document_ids=scope.document_ids,
                locale=scope.locale,
                exam_mode=False,
            ),
            topic=spec.topic,
            difficulty=spec.difficulty,
            item_count=spec.item_count,
            detail_level=spec.detail_level,
            user_instructions=spec.user_instructions,
            provider_override=spec.provider_override,
            model_override=spec.model_override,
            force_regenerate=spec.force_regenerate,
        )

        # Compute pre-generation per-user cache key over active scope snapshot to avoid redundant LLM calls
        import json as _json
        from app.modules.rag.artifacts import compute_artifact_cache_key

        active_versions = self.ctx.rag_store.active_version_map(user_id=rag_uid)
        if scope.document_ids:
            scoped_pairs = sorted(
                (doc_id, active_versions[doc_id])
                for doc_id in scope.document_ids
                if doc_id in active_versions
            )
        else:
            scoped_pairs = sorted(active_versions.items())
        scope_snapshot_hash = f"{len(scoped_pairs)}:" + _json.dumps(scoped_pairs, separators=(",", ":"))
        cache_spec = ArtifactGenerationSpec(
            kind=spec.kind,
            scope=QueryScope(
                user_id=user_id,
                subject_id=scope.subject_id,
                concept_ids=scope.concept_ids,
                document_ids=scope.document_ids,
                locale=scope.locale,
                exam_mode=False,
            ),
            topic=spec.topic,
            difficulty=spec.difficulty,
            item_count=spec.item_count,
            detail_level=spec.detail_level,
            user_instructions=spec.user_instructions,
            provider_override=spec.provider_override,
            model_override=spec.model_override,
            force_regenerate=spec.force_regenerate,
        )
        ai_disabled = not prefs.get("enabled", True)
        cache_key = compute_artifact_cache_key(
            cache_spec,
            source_snapshot_hash=scope_snapshot_hash,
            embedding_space_id=self.ctx.rag_store.embedding_space_id,
            ai_disabled=ai_disabled,
        )
        if not body.force_regenerate:
            cached = self.ctx.artifact_store.get_latest_by_cache_key(
                cache_key, user_id=user_id, store=self.ctx.rag_store, store_user_id=rag_uid
            )
            if cached is not None and not cached.stale:
                return self._record_to_artifact_out(user_id, cached)

        try:
            generated = engine.generate_artifact(scoped_spec, ai_disabled=ai_disabled)
            if generated.provider == "extractive_local":
                is_fallback = True
                fallback_reason = "ai_disabled"
        except GatewayUnavailable as exc:
            if explicit_override or not prefs.get("allow_extractive_fallback", True):
                raise AIProviderUnavailable(
                    f"AI provider unavailable ({exc.code})",
                    details={"reason": exc.code},
                ) from None
            is_fallback = True
            fallback_reason = exc.code
            generated = engine.generate_artifact(scoped_spec, ai_disabled=True)

        saved = self.ctx.artifact_store.save(generated, user_id=user_id, cache_key=cache_key)

        linked_deck_id: str | None = None
        linked_quiz_id: str | None = None
        if body.persist_to_learning and generated.evidence_status != "insufficient":
            learning_svc = LearningService(self.db, self.clock)
            if body.type == "flashcards":
                payload = to_p3_flashcard_deck_payload(
                    generated,
                    subject_id=body.subject_id,
                    concept_id=body.concept_ids[0] if body.concept_ids else None,
                )
                if payload["cards"]:
                    deck = learning_svc.create_deck(user_id, DeckCreate.model_validate(payload["deck"]))
                    for card in payload["cards"]:
                        learning_svc.create_card(user_id, deck.id, CardCreate.model_validate(card))
                    linked_deck_id = deck.id
            elif body.type == "quiz":
                payload = to_p3_quiz_payload(generated, subject_id=body.subject_id)
                if payload["questions"]:
                    # Ensure concept_ids from request attach when question has none
                    if body.concept_ids:
                        for q_item in payload["questions"]:
                            if not q_item.get("concept_ids"):
                                q_item["concept_ids"] = list(body.concept_ids[:5])
                    quiz = learning_svc.create_quiz(
                        user_id,
                        QuizCreate(
                            title=payload["title"],
                            subject_id=payload["subject_id"],
                            type=payload["type"],
                            time_limit_min=payload["time_limit_min"],
                            origin=payload["origin"],
                            questions=[QuestionIn.model_validate(q) for q in payload["questions"]],
                        ),
                    )
                    linked_quiz_id = quiz.id

        self.ctx.state_store.save_artifact_link(
            artifact_id=saved.artifact_id,
            user_id=user_id,
            subject_id=body.subject_id,
            linked_deck_id=linked_deck_id,
            linked_quiz_id=linked_quiz_id,
            is_fallback=is_fallback,
            fallback_reason=fallback_reason,
        )
        return self._record_to_artifact_out(user_id, saved)

    def list_artifacts(
        self,
        user_id: str,
        *,
        artifact_type: str | None = None,
        subject_id: str | None = None,
        limit: int = 50,
    ) -> list[ArtifactOut]:
        rag_uid = self._rag_user_id(user_id)
        recs = self.ctx.artifact_store.list_artifacts(
            user_id=user_id,
            artifact_kind=artifact_type,  # type: ignore[arg-type]
            limit=limit,
            store=self.ctx.rag_store,
            store_user_id=rag_uid,
        )
        items = [self._record_to_artifact_out(user_id, r) for r in recs]
        if subject_id:
            items = [i for i in items if i.subject_id == subject_id]
        return items

    def get_artifact(self, user_id: str, artifact_id: str) -> ArtifactOut:
        rag_uid = self._rag_user_id(user_id)
        rec = self.ctx.artifact_store.get(
            artifact_id, user_id=user_id, store=self.ctx.rag_store, store_user_id=rag_uid
        )
        if rec is None:
            raise NotFound("Artifact not found")
        return self._record_to_artifact_out(user_id, rec)

    def patch_artifact(self, user_id: str, artifact_id: str, body: ArtifactPatchRequest) -> ArtifactOut:
        rag_uid = self._rag_user_id(user_id)
        prev_link = self.ctx.state_store.get_artifact_link(user_id, artifact_id)
        try:
            edited = self.ctx.artifact_store.save_user_edit(
                artifact_id,
                user_id=user_id,
                title=body.title,
                content_markdown=body.content_markdown,
                structured_payload=body.structured_payload,
                store_user_id=rag_uid,
            )
        except KeyError:
            raise NotFound("Artifact not found") from None
        except ValueError as exc:
            raise Conflict(str(exc), code="artifact_stale") from None

        self.ctx.state_store.save_artifact_link(
            artifact_id=edited.artifact_id,
            user_id=user_id,
            subject_id=prev_link["subject_id"],
            linked_deck_id=prev_link["linked_deck_id"],
            linked_quiz_id=prev_link["linked_quiz_id"],
            is_fallback=prev_link["is_fallback"],
            fallback_reason=prev_link["fallback_reason"],
        )
        return self._record_to_artifact_out(user_id, edited)

    def list_artifact_versions(self, user_id: str, artifact_id: str) -> list[ArtifactOut]:
        rag_uid = self._rag_user_id(user_id)
        versions = self.ctx.artifact_store.list_versions(
            artifact_id, user_id=user_id, store=self.ctx.rag_store, store_user_id=rag_uid
        )
        if not versions:
            raise NotFound("Artifact not found")
        return [self._record_to_artifact_out(user_id, rec) for rec in versions]
