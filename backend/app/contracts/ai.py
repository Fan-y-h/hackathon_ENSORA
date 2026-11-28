"""Typed request and provider contracts for the shared AI Gateway.

These contracts carry model identifiers and policy decisions only. Provider
credentials are stored separately in the operating-system keyring and are
never part of an API response or generation request.
"""

from enum import StrEnum
from typing import Literal

from pydantic import Field

from .sources import Contract, Key


class AITask(StrEnum):
    """Generation task keys used for independent model preferences."""

    RAG_ANSWER = "rag_answer"
    TUTOR = "tutor"
    COURSE_SUMMARY = "course_summary"
    REVISION_SHEET = "revision_sheet"
    QUIZ = "quiz"
    FLASHCARDS = "flashcards"
    EXERCISE = "exercise"
    CORRECTION = "correction"
    CONCEPT_EXTRACTION = "concept_extraction"
    STRUCTURED_EXTRACTION = "structured_extraction"
    CLASSIFICATION_ASSISTANCE = "classification_assistance"
    ASSOCIATION_ASSISTANCE = "association_assistance"
    MULTIMODAL = "multimodal"
    PEDAGOGICAL_EXPLANATION = "pedagogical_explanation"
    STUDY_RECOMMENDATION = "study_recommendation"


class ProviderKind(StrEnum):
    """Stable provider families; availability is determined at runtime."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GOOGLE = "google"
    MISTRAL = "mistral"
    OPENROUTER = "openrouter"
    GROQ = "groq"
    HUGGINGFACE = "huggingface"
    COMPATIBLE = "compatible"


class ModelSelection(Contract):
    """A provider-qualified model identifier, never a credential."""

    provider_id: Key
    model_id: Key


class ModelCapabilities(Contract):
    """Capabilities declared by an adapter for one installed/available model."""

    tasks: set[AITask] = Field(default_factory=set)
    structured_output: bool = False
    vision: bool = False
    streaming: bool = False
    context_tokens: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)


class ModelDescriptor(Contract):
    """Public model metadata. It intentionally has no credential fields."""

    selection: ModelSelection
    capabilities: ModelCapabilities
    local: bool
    enabled: bool = True
    estimated_cost_per_1k_input: float | None = Field(default=None, ge=0)
    estimated_cost_per_1k_output: float | None = Field(default=None, ge=0)


class ProviderPreference(Contract):
    """Persistent non-secret global and task-specific model preferences."""

    enabled: bool = False
    cloud_data_consent: bool = False
    automatic: bool = False
    default: ModelSelection | None = None
    tasks: dict[AITask, ModelSelection] = Field(default_factory=dict)
    embeddings: ModelSelection | None = None
    reranker: ModelSelection | None = None
    fallback_candidates: list[ModelSelection] = Field(default_factory=list, max_length=8)


class ChatMessage(Contract):
    """A bounded role/content pair supplied to an adapter."""

    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class GenerationRequest(Contract):
    """Provider-independent generation input with a non-persistent override."""

    task: AITask
    messages: list[ChatMessage] = Field(min_length=1, max_length=128)
    override: ModelSelection | None = None
    max_output_tokens: int = Field(default=2048, ge=1, le=32768)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    structured_output: bool = False
    stream: bool = False
    allow_fallback: bool = False


class GenerationResult(Contract):
    """Adapter result with the effective model recorded for provenance."""

    content: str
    effective_model: ModelSelection
    finish_reason: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    estimated_cost: float | None = Field(default=None, ge=0)
    fallback_reason: str | None = None


class ProviderStatus(Contract):
    """Safe provider health summary suitable for the frontend."""

    provider_id: Key
    kind: ProviderKind
    local: bool
    configured: bool
    available: bool
    models: list[ModelDescriptor] = Field(default_factory=list)
    error_code: str | None = None


class AISelectionPolicy(Contract):
    """Per-request policy input assembled after authorization and consent checks."""

    enabled: bool = True
    remote_allowed: bool = False
    allowed_providers: set[str] = Field(default_factory=set)
    fallback_candidates: list[ModelSelection] = Field(default_factory=list)
