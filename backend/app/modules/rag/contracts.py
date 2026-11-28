"""Typed contracts, DTOs, and provider protocols for the Ensora+ RAG engine.

Follows ``docs/RAG_ARCHITECTURE.md``, ``docs/AI_ENGINE.md``, and ``docs/API_CONTRACTS.md``:
- Preserves exact P1 ``document_id``, ``version_id``, ``chunk_id``, and 1-based locators.
- Never exposes local filesystem paths or revoked document excerpts.
- Decouples retrieval and generation from concrete AI provider SDKs so Codex's
  AI Gateway, local Ollama runtimes, or deterministic extractive fallbacks plug in cleanly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, Sequence, runtime_checkable

RetrievalMode = Literal["lexical", "semantic", "hybrid"]
EmbeddingKind = Literal["feature_hash", "neural_dense"]
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
ArtifactKind = Literal[
    "course_summary",
    "revision_sheet",
    "quiz",
    "flashcards",
    "exercise",
    "exercises",
    "exercise_explanation",
    "mind_map",
    "annale_search",
    "concept_extraction",
    "prerequisite_suggestions",
    "study_recommendation",
]


@dataclass(frozen=True)
class QueryScope:
    """Authorization, academic filter, and policy scope for a RAG query.

    Attributes:
        user_id: Authenticated local user identifier (matches P1 ``LOCAL_USER``).
        subject_id: Optional canonical subject external ID filter.
        chapter_id: Optional chapter label/ID filter.
        concept_ids: Optional concept IDs guiding retrieval emphasis.
        document_ids: Optional explicit allowlist of P1 document UUIDs.
        academic_year: Optional ``YYYY-YYYY`` academic year filter.
        resource_types: Optional allowlist of document classification categories
            (e.g. ``("lecture", "tutorial")``).
        sources: Optional allowlist of P1 source kinds (e.g. ``("moodle", "ensora_drive")``).
        purpose: Requested educational activity (e.g. ``"tutor"``, ``"quiz"``, ``"exam_prep"``).
        locale: Preferred output language (``"fr"`` or ``"en"``).
        exam_mode: When ``True``, strictly blocks ``examination_correction`` documents and
            solution disclosure per R18 / ``docs/RAG_ARCHITECTURE.md``.
        allow_general_fallback: When ``True``, permits an explicitly labeled general
            explanation if retrieved evidence is insufficient.
        mode: Retrieval strategy (``"lexical"``, ``"semantic"``, or ``"hybrid"``).
        top_k: Target number of final context passages (default 6).
        token_budget: Maximum prompt context token budget for retrieved passages.
        subject_year: Optional curriculum year used only to disambiguate official subject codes.
        expand_neighbors: When ``True``, fetches adjacent chunks around proof/code hits.
        rerank: When ``True``, applies the configured reranker after candidate fusion.
    """

    user_id: str = "e0aa03f9-37fc-455b-97bb-648a35868d19"
    subject_id: str | None = None
    chapter_id: str | None = None
    concept_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    academic_year: str | None = None
    resource_types: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    purpose: str = "tutor"
    locale: str = "fr"
    exam_mode: bool = False
    allow_general_fallback: bool = False
    mode: RetrievalMode = "hybrid"
    top_k: int = 6
    token_budget: int = 2800
    expand_neighbors: bool = True
    rerank: bool = True
    subject_year: str | None = None


@dataclass(frozen=True)
class Citation:
    """Verifiable source-grounded citation aligned with P1 chunks and P3 contracts.

    Never contains local filesystem paths. If the underlying source is later revoked
    or tombstoned, ``resolve_citation()`` returns ``revoked=True`` with an empty excerpt.
    """

    citation_id: str
    document_id: str
    version_id: str
    chunk_id: str
    title: str
    locator: dict[str, str | int]
    excerpt: str
    source_url: str | None = None
    subject_id: str | None = None
    academic_year: str | None = None
    resource_type: str | None = None
    revoked: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize citation into the canonical JSON API payload shape."""
        payload: dict[str, Any] = {
            "citation_id": self.citation_id,
            "document_id": self.document_id,
            "version_id": self.version_id,
            "chunk_id": self.chunk_id,
            "title": self.title,
            "locator": dict(self.locator),
            "excerpt": "" if self.revoked else self.excerpt,
            "revoked": self.revoked,
        }
        if self.source_url and not self.revoked:
            payload["source_url"] = self.source_url
        if self.subject_id:
            payload["subject_id"] = self.subject_id
        if self.academic_year:
            payload["academic_year"] = self.academic_year
        if self.resource_type:
            payload["resource_type"] = self.resource_type
        return payload


@dataclass(frozen=True)
class RetrievedChunk:
    """Scored document chunk candidate returned by lexical, semantic, or hybrid search."""

    chunk_id: str
    document_id: str
    version_id: str
    ordinal: int
    title: str
    text: str
    section: tuple[str, ...]
    locator: dict[str, str | int]
    block_kind: str = "paragraph"
    structured: dict[str, Any] = field(default_factory=dict)
    source: str = ""
    subject_id: str | None = None
    chapter_id: str | None = None
    academic_year: str | None = None
    resource_type: str | None = None
    source_url: str | None = None
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    fused_score: float = 0.0
    rerank_score: float = 0.0
    rank: int = 0
    is_neighbor_expansion: bool = False

    def to_citation(self, citation_id: str, *, max_excerpt_chars: int = 420) -> Citation:
        """Convert this retrieved chunk into a bounded, verifiable ``Citation``."""
        excerpt = " ".join(self.text.split())
        if len(excerpt) > max_excerpt_chars:
            excerpt = excerpt[: max_excerpt_chars - 1].rstrip() + "…"
        return Citation(
            citation_id=citation_id,
            document_id=self.document_id,
            version_id=self.version_id,
            chunk_id=self.chunk_id,
            title=self.title,
            locator=dict(self.locator),
            excerpt=excerpt,
            source_url=self.source_url,
            subject_id=self.subject_id,
            academic_year=self.academic_year,
            resource_type=self.resource_type,
            revoked=False,
        )


@dataclass(frozen=True)
class RetrievalResult:
    """Result of executing a scoped RAG retrieval query."""

    query: str
    normalized_query: str
    mode: RetrievalMode
    hits: tuple[RetrievedChunk, ...]
    citations: tuple[Citation, ...]
    evidence_status: EvidenceStatus
    total_candidates: int
    embedding_space_id: str
    embedding_kind: EmbeddingKind = "feature_hash"
    index_stale: bool = False
    warnings: tuple[str, ...] = ()
    elapsed_ms: float = 0.0

    def citation_map(self) -> dict[str, Citation]:
        """Return citations keyed by ``citation_id`` (e.g. ``'C1'``)."""
        return {c.citation_id: c for c in self.citations}


@dataclass(frozen=True)
class IndexReceipt:
    """Auditable receipt returned after an incremental or full RAG index sync."""

    indexed_documents: int
    skipped_unchanged: int
    removed_documents: int
    indexed_chunks: int
    embedding_space_id: str
    index_version: str
    elapsed_ms: float
    embedding_kind: EmbeddingKind = "feature_hash"
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "indexed_documents": self.indexed_documents,
            "skipped_unchanged": self.skipped_unchanged,
            "removed_documents": self.removed_documents,
            "indexed_chunks": self.indexed_chunks,
            "embedding_space_id": self.embedding_space_id,
            "embedding_kind": self.embedding_kind,
            "index_version": self.index_version,
            "elapsed_ms": round(self.elapsed_ms, 2),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class GenerationRequest:
    """Provider-independent text generation request sent to Codex's AI Gateway."""

    task_type: str
    system_prompt: str
    user_prompt: str
    locale: str = "fr"
    provider_override: str | None = None
    model_override: str | None = None
    max_tokens: int = 1200
    temperature: float = 0.2
    json_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResponse:
    """Normalized response returned by an AI Gateway adapter or deterministic fallback."""

    content: str
    provider: str
    model: str
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost_usd: float | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class RagAnswer:
    """Complete grounded RAG answer with verified citations and provenance metadata."""

    question: str
    answer_markdown: str
    citations: tuple[Citation, ...]
    evidence_status: EvidenceStatus
    provider: str
    model: str
    mode: TutorMode
    embedding_space_id: str
    source_snapshot_hash: str
    usage: dict[str, Any] = field(default_factory=dict)
    follow_up_questions: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    abstained: bool = False
    prompt_injection_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer_markdown": self.answer_markdown,
            "citations": [c.to_dict() for c in self.citations],
            "evidence_status": self.evidence_status,
            "provider": self.provider,
            "model": self.model,
            "mode": self.mode,
            "embedding_space_id": self.embedding_space_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "usage": dict(self.usage),
            "follow_up_questions": list(self.follow_up_questions),
            "warnings": list(self.warnings),
            "abstained": self.abstained,
            "prompt_injection_detected": self.prompt_injection_detected,
        }


@dataclass(frozen=True)
class ArtifactGenerationSpec:
    """Specification for generating a grounded educational artifact from authorized sources."""

    kind: ArtifactKind
    scope: QueryScope
    topic: str = ""
    difficulty: Literal["beginner", "intermediate", "advanced"] = "intermediate"
    item_count: int = 6
    detail_level: Literal["concise", "standard", "detailed"] = "standard"
    user_instructions: str = ""
    provider_override: str | None = None
    model_override: str | None = None
    force_regenerate: bool = False


@dataclass(frozen=True)
class GeneratedArtifact:
    """Reproducible, citation-grounded educational artifact ready for P3/Studio persistence."""

    artifact_kind: ArtifactKind
    title: str
    content_markdown: str
    structured_payload: dict[str, Any]
    citations: tuple[Citation, ...]
    evidence_status: EvidenceStatus
    provider: str
    model: str
    embedding_space_id: str
    source_snapshot_hash: str
    coverage_ratio: float
    omitted_sections: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    ai_generated: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_kind": self.artifact_kind,
            "title": self.title,
            "content_markdown": self.content_markdown,
            "structured_payload": dict(self.structured_payload),
            "citations": [c.to_dict() for c in self.citations],
            "evidence_status": self.evidence_status,
            "provider": self.provider,
            "model": self.model,
            "embedding_space_id": self.embedding_space_id,
            "source_snapshot_hash": self.source_snapshot_hash,
            "coverage_ratio": round(self.coverage_ratio, 3),
            "omitted_sections": list(self.omitted_sections),
            "warnings": list(self.warnings),
            "ai_generated": self.ai_generated,
        }


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol implemented by deterministic feature-hash or trained neural embedding providers."""

    @property
    def space_id(self) -> str:
        """Unique immutable identifier pinning model ID, revision, and dimension."""
        ...

    @property
    def dimension(self) -> int:
        """Output embedding vector length."""
        ...

    @property
    def embedding_kind(self) -> EmbeddingKind:
        """Distinguish ``'feature_hash'`` from trained ``'neural_dense'`` models explicitly."""
        ...

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts into unit-normalized float vectors."""
        ...


@runtime_checkable
class Reranker(Protocol):
    """Protocol for optional second-stage retrieval rerankers."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        scope: QueryScope,
        top_k: int,
    ) -> list[RetrievedChunk]:
        """Rerank retrieved candidates and return up to ``top_k`` ordered items."""
        ...


@runtime_checkable
class TextGenerationPort(Protocol):
    """Protocol decoupling RAG generation from Codex's AI Gateway implementation."""

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        """Execute a generation request via the configured provider/model router."""
        ...
