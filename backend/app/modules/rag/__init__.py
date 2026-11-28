"""Ensora+ modular local-first Retrieval-Augmented Generation (RAG) engine.

Exposes:
- Contracts & protocols: ``QueryScope``, ``Citation``, ``RetrievedChunk``,
  ``RetrievalResult``, ``IndexReceipt``, ``RagAnswer``, ``ArtifactGenerationSpec``,
  ``GeneratedArtifact``, ``EmbeddingKind``, ``EmbeddingProvider``, ``Reranker``,
  ``TextGenerationPort``.
- Embeddings: ``LocalMultilingualHashEmbedder`` (``feature_hash``),
  ``OllamaEmbeddingAdapter``, ``SentenceTransformersEmbeddingAdapter``,
  ``OpenAICompatibleEmbeddingAdapter`` (``neural_dense``), ``build_embedding_provider``.
- Local derived store: ``LocalRagIndexStore`` (SQLite FTS5 + multi-space float32 vectors,
  strictly read-only on Codex's P1 SQLite database with live consent & Drive scope guards).
- Persistent versioned artifacts & P3 export adapters: ``LocalArtifactStore``,
  ``StoredArtifactRecord``, ``compute_artifact_cache_key``,
  ``to_p3_flashcard_deck_payload``, ``to_p3_quiz_payload``, ``to_p3_note_payload``.
- Hybrid retrieval & reranking: ``HybridRetriever``, ``AcademicCrossFeatureReranker``,
  ``SentenceTransformersCrossEncoderReranker``, ``HttpRerankerAdapter``.
- Grounded engine & evaluation: ``RagEngine``, ``evaluate_rag_engine``.
"""

from .artifacts import (
    LocalArtifactStore,
    StoredArtifactRecord,
    compute_artifact_cache_key,
    to_p3_flashcard_deck_payload,
    to_p3_quiz_payload,
    to_p3_note_payload,
)
from .context import (
    AssembledContext,
    SectionCoverageResult,
    build_context,
    build_tutor_prompts,
    collect_full_document_sections,
    format_locator_label,
    sanitize_untrusted_text,
    validate_and_filter_citations,
)
from .contracts import (
    ArtifactGenerationSpec,
    ArtifactKind,
    Citation,
    EmbeddingKind,
    EmbeddingProvider,
    EvidenceStatus,
    GeneratedArtifact,
    GenerationRequest,
    GenerationResponse,
    IndexReceipt,
    QueryScope,
    RagAnswer,
    Reranker,
    RetrievalMode,
    RetrievalResult,
    RetrievedChunk,
    TextGenerationPort,
    TutorMode,
)
from .embeddings import (
    LocalMultilingualHashEmbedder,
    OllamaEmbeddingAdapter,
    OpenAICompatibleEmbeddingAdapter,
    SentenceTransformersEmbeddingAdapter,
    build_embedding_provider,
    cosine_similarity_normalized,
    pack_vector,
    unpack_vector,
)
from .engine import RagEngine
from .evaluation import EvalQueryCase, build_synthetic_ensai_corpus, evaluate_rag_engine
from .reranking import HttpRerankerAdapter, SentenceTransformersCrossEncoderReranker
from .retrieval import (
    AcademicCrossFeatureReranker,
    HybridRetriever,
    build_fts5_query,
    extract_query_terms,
    normalize_query,
)
from .store import IndexableChunk, IndexableDocument, LocalRagIndexStore

__all__ = [
    "AcademicCrossFeatureReranker",
    "ArtifactGenerationSpec",
    "ArtifactKind",
    "AssembledContext",
    "Citation",
    "EmbeddingKind",
    "EmbeddingProvider",
    "EvalQueryCase",
    "EvidenceStatus",
    "GeneratedArtifact",
    "GenerationRequest",
    "GenerationResponse",
    "HttpRerankerAdapter",
    "HybridRetriever",
    "IndexReceipt",
    "IndexableChunk",
    "IndexableDocument",
    "LocalArtifactStore",
    "LocalMultilingualHashEmbedder",
    "LocalRagIndexStore",
    "OllamaEmbeddingAdapter",
    "OpenAICompatibleEmbeddingAdapter",
    "QueryScope",
    "RagAnswer",
    "RagEngine",
    "Reranker",
    "RetrievalMode",
    "RetrievalResult",
    "RetrievedChunk",
    "SectionCoverageResult",
    "SentenceTransformersCrossEncoderReranker",
    "SentenceTransformersEmbeddingAdapter",
    "StoredArtifactRecord",
    "TextGenerationPort",
    "TutorMode",
    "build_context",
    "build_embedding_provider",
    "build_fts5_query",
    "build_synthetic_ensai_corpus",
    "build_tutor_prompts",
    "collect_full_document_sections",
    "compute_artifact_cache_key",
    "cosine_similarity_normalized",
    "evaluate_rag_engine",
    "extract_query_terms",
    "format_locator_label",
    "normalize_query",
    "pack_vector",
    "sanitize_untrusted_text",
    "to_p3_flashcard_deck_payload",
    "to_p3_note_payload",
    "to_p3_quiz_payload",
    "unpack_vector",
    "validate_and_filter_citations",
]
