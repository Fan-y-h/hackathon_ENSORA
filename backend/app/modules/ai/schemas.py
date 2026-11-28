"""Strict Pydantic v2 DTOs for the unified AI, RAG, Tutor & Learning Studio API.

Aligned with ``/home/ensai/ensora-coordination/contracts/AI_RAG.md``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


RetrievalMode = Literal["hybrid", "lexical", "semantic"]
EvidenceStatus = Literal["grounded", "partial", "insufficient"]
TutorMode = Literal[
    "explain",
    "derivation",
    "code_explain",
    "correction",
    "socratic",
    "exam_prep",
    "memorize",
]
StudioArtifactType = Literal[
    "revision_sheet",
    "course_summary",
    "flashcards",
    "quiz",
    "exercise",
    "exercises",
    "mind_map",
    "concept_extraction",
    "study_recommendation",
]


# --- Search & Citations -------------------------------------------------------------------
class SearchHitOut(BaseModel):
    chunk_id: str
    document_id: str
    version_id: str
    title: str
    subject_id: str | None = None
    resource_type: str | None = None
    score: float
    excerpt: str
    locator: dict[str, str | int] = Field(default_factory=dict)
    citation_id: str


class SearchResponseOut(BaseModel):
    query: str
    mode: RetrievalMode
    indexed_chunks: int
    evidence_status: EvidenceStatus
    index_stale: bool = False
    warnings: list[str] = Field(default_factory=list)
    hits: list[SearchHitOut] = Field(default_factory=list)


class CitationOut(BaseModel):
    citation_id: str
    document_id: str
    version_id: str
    chunk_id: str
    title: str
    locator: dict[str, str | int] = Field(default_factory=dict)
    excerpt: str
    content_url: str | None = None
    source_url: str | None = None
    subject_id: str | None = None
    academic_year: str | None = None
    resource_type: str | None = None
    revoked: bool = False


class ReindexOut(BaseModel):
    documents_indexed: int
    chunks_indexed: int
    skipped_unchanged: int = 0
    removed_documents: int = 0
    embedding_model: str
    embedding_kind: str = "feature_hash"
    duration_ms: int
    warnings: list[str] = Field(default_factory=list)


class DocumentIndexStateOut(BaseModel):
    document_id: str
    version_id: str | None = None
    title: str
    source: str
    subject_id: str | None = None
    resource_type: str | None = None
    parse_status: str
    p1_chunks: int
    indexed_chunks: int
    index_state: str


class RagStatusOut(BaseModel):
    p1_connected: bool
    summary: dict[str, int]
    index_stats: dict[str, Any]
    documents: list[DocumentIndexStateOut] = Field(default_factory=list)


# --- AI Providers & Preferences -----------------------------------------------------------
class AIProviderModelOut(BaseModel):
    provider_id: str
    model_id: str
    local: bool = True
    structured_output: bool = False
    vision: bool = False
    context_tokens: int | None = None


class AIProviderStatusOut(BaseModel):
    provider_id: str
    kind: str
    local: bool
    configured: bool
    available: bool
    error_code: str | None = None
    models: list[AIProviderModelOut] = Field(default_factory=list)


class AIProvidersOut(BaseModel):
    items: list[AIProviderStatusOut] = Field(default_factory=list)


class AIPreferencesOut(BaseModel):
    enabled: bool = True
    cloud_data_consent: bool = False
    allow_extractive_fallback: bool = True
    preferred_provider: str | None = None
    preferred_model: str | None = None
    daily_token_budget: int = Field(default=100_000, ge=0, le=10_000_000)
    tokens_used_today: int = 0


class AIPreferencesPatch(_Strict):
    enabled: bool | None = None
    cloud_data_consent: bool | None = None
    allow_extractive_fallback: bool | None = None
    preferred_provider: str | None = Field(default=None, max_length=128)
    preferred_model: str | None = Field(default=None, max_length=256)
    daily_token_budget: int | None = Field(default=None, ge=0, le=10_000_000)


# --- Tutor Conversations & Answers --------------------------------------------------------
class ConversationCreate(_Strict):
    subject_id: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=200)


class ConversationOut(BaseModel):
    id: str
    title: str
    subject_id: str | None = None
    message_count: int = 0
    created_at: str
    updated_at: str


class UsageOut(BaseModel):
    tokens_in: int = 0
    tokens_out: int = 0
    estimated_cost_usd: float | None = None


class ConversationMessageOut(BaseModel):
    id: str
    conversation_id: str
    role: Literal["user", "assistant"]
    content_markdown: str
    citations: list[CitationOut] = Field(default_factory=list)
    evidence_status: EvidenceStatus | None = None
    provider: str | None = None
    model: str | None = None
    generation_mode: str | None = None
    is_fallback: bool = False
    fallback_reason: str | None = None
    created_at: str


class TutorAnswerRequest(_Strict):
    question: str = Field(min_length=1, max_length=5000)
    conversation_id: str | None = Field(default=None, max_length=64)
    subject_id: str | None = Field(default=None, max_length=64)
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    mode: TutorMode = "explain"
    locale: Literal["fr", "en"] = "fr"
    provider_override: str | None = Field(default=None, max_length=128)
    model_override: str | None = Field(default=None, max_length=256)


class TutorAnswerOut(BaseModel):
    conversation_id: str
    message_id: str
    answer_markdown: str
    citations: list[CitationOut] = Field(default_factory=list)
    evidence_status: EvidenceStatus
    provider: str
    model: str
    generation_mode: Literal["llm", "extractive_fallback", "abstention", "policy_guard"]
    is_fallback: bool = False
    fallback_reason: str | None = None
    usage: UsageOut = Field(default_factory=UsageOut)
    follow_up_questions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    abstained: bool = False
    prompt_injection_detected: bool = False


# --- Learning Studio Artifacts ------------------------------------------------------------
class ArtifactGenerateRequest(_Strict):
    type: StudioArtifactType
    subject_id: str | None = Field(default=None, max_length=64)
    document_ids: list[str] = Field(default_factory=list, max_length=50)
    concept_ids: list[str] = Field(default_factory=list, max_length=50)
    topic: str | None = Field(default=None, max_length=300)
    objective: str = Field(default="understand", max_length=64)
    locale: Literal["fr", "en"] = "fr"
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    duration_min: int = Field(default=20, ge=5, le=240)
    item_count: int | None = Field(default=None, ge=1, le=30)
    detail_level: Literal["concise", "standard", "detailed"] = "detailed"
    user_instructions: str | None = Field(default=None, max_length=2000)
    force_regenerate: bool = False
    persist_to_learning: bool = True
    provider_override: str | None = Field(default=None, max_length=128)
    model_override: str | None = Field(default=None, max_length=256)


class ArtifactPatchRequest(_Strict):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    content_markdown: str | None = Field(default=None, max_length=200_000)
    structured_payload: dict[str, Any] | None = None


class ArtifactOut(BaseModel):
    id: str
    type: str
    title: str
    subject_id: str | None = None
    version: int
    parent_artifact_id: str | None = None
    cache_hit: bool = False
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    user_edited: bool = False
    provider: str
    model: str
    is_fallback: bool = False
    fallback_reason: str | None = None
    evidence_status: EvidenceStatus
    coverage_ratio: float = 0.0
    omitted_sections: list[str] = Field(default_factory=list)
    content_markdown: str
    structured_payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[CitationOut] = Field(default_factory=list)
    linked_deck_id: str | None = None
    linked_quiz_id: str | None = None
    created_at: str
    updated_at: str
