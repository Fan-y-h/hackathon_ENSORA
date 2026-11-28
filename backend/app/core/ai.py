"""Provider-neutral model registry and policy-first task router.

The router does not read credentials, source text, or the database. Its caller
must build ``AISelectionPolicy`` from authenticated user preferences, source
visibility, exam policy, remote-transfer consent, and budget limits. This keeps
provider selection deterministic and makes a local/disabled deployment safe.
"""

import json
import os
import random
from threading import Lock
from time import monotonic, sleep
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from ..contracts.ai import (
    AISelectionPolicy,
    AITask,
    GenerationResult,
    GenerationRequest,
    ModelCapabilities,
    ModelDescriptor,
    ModelSelection,
    ProviderKind,
    ProviderPreference,
    ProviderStatus,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})


def _call_with_bounded_retry(fn, *, max_retries: int = 1, base_delay: float = 0.25):
    """Execute a provider HTTP call with bounded retries on transient errors only."""
    attempts = max(1, int(max_retries) + 1)
    for attempt in range(attempts):
        try:
            return fn()
        except HTTPError as exc:
            if exc.code in _TRANSIENT_HTTP_CODES and attempt + 1 < attempts:
                sleep(base_delay * (2 ** attempt) + random.uniform(0.0, 0.1))
                continue
            raise
        except (TimeoutError, URLError):
            if attempt + 1 < attempts:
                sleep(base_delay * (2 ** attempt) + random.uniform(0.0, 0.1))
                continue
            raise


class AISelectionError(ValueError):
    """A safe, machine-readable routing error without provider response data."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _RejectRedirects(HTTPRedirectHandler):
    """Keep loopback inference requests from forwarding prompts elsewhere."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A local inference service has no reason to redirect a generation
        # request. Refusing all redirects also prevents prompt exfiltration if
        # a local process, port forward, or compromised runtime returns one.
        return None


class AIProviderAdapter(Protocol):
    """Adapter contract implemented by local and authorized cloud providers."""

    def status(self) -> ProviderStatus:
        """Return non-secret configuration and availability metadata."""

    def generate(self, request: GenerationRequest, model: ModelDescriptor) -> GenerationResult:
        """Generate content using the selected model, subject to request timeout."""


class OllamaProviderAdapter:
    """Optional local Ollama adapter; it never downloads models automatically.

    The endpoint is restricted to loopback so local prompts cannot be forwarded
    to another host through a modified preference. Model discovery occurs only
    when the provider status is requested, not during application startup.
    """

    provider_id = "ollama-local"

    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout_seconds: float = 90):
        from urllib.parse import urlparse

        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Local inference must use an HTTP loopback endpoint")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = max(1.0, min(timeout_seconds, 180.0))

    def status(self) -> ProviderStatus:
        """Discover installed local models without initiating downloads."""
        from ..contracts.ai import ModelCapabilities

        try:
            payload = self._request("GET", "/api/tags")
            entries = payload.get("models", [])
            if not isinstance(entries, list):
                raise ValueError("invalid_model_list")
            models = []
            for item in entries:
                name = item.get("name") if isinstance(item, dict) else None
                if (not isinstance(name, str) or not name or self._cloud_tag(name)
                        or item.get("remote_host") or item.get("remote_model")):
                    continue
                models.append(ModelDescriptor(
                    selection=ModelSelection(provider_id=self.provider_id, model_id=name),
                    local=True,
                    capabilities=ModelCapabilities(
                        tasks=set(AITask), structured_output=True, streaming=False,
                    ),
                ))
            return ProviderStatus(provider_id=self.provider_id, kind=ProviderKind.OLLAMA, local=True,
                                  configured=True, available=True, models=models)
        except Exception:
            return ProviderStatus(provider_id=self.provider_id, kind=ProviderKind.OLLAMA, local=True,
                                  configured=True, available=False,
                                  error_code="local_runtime_unavailable")

    def generate(self, request: GenerationRequest, model: ModelDescriptor) -> GenerationResult:
        """Generate a response after checking current model metadata.

        Args:
            request: Messages and output controls for the local chat endpoint.
            model: Selected Ollama model descriptor from provider discovery.

        Returns:
            Generated content and usage metadata.

        Raises:
            AISelectionError: The model or response fails local provider checks.
        """
        if request.stream:
            raise AISelectionError("streaming_unavailable")
        name = model.selection.model_id
        if not model.local or model.selection.provider_id != self.provider_id or self._cloud_tag(name):
            raise AISelectionError("local_model_required")
        try:
            metadata = self._request("POST", "/api/show", {"model": name})
        except Exception:
            raise AISelectionError("local_model_verification_failed") from None
        if metadata.get("remote_host") or metadata.get("remote_model"):
            raise AISelectionError("local_model_required")
        if not isinstance(metadata.get("model_info"), dict) or not metadata["model_info"]:
            raise AISelectionError("local_model_verification_failed")
        payload = {
            "model": name,
            "messages": [message.model_dump() for message in request.messages],
            "stream": False,
            "options": {"num_predict": request.max_output_tokens, "temperature": request.temperature},
        }
        if request.structured_output:
            payload["format"] = "json"
        try:
            response = self._request("POST", "/api/chat", payload)
        except TimeoutError:
            raise AISelectionError("provider_timeout") from None
        except Exception:
            raise AISelectionError("provider_request_failed") from None
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content:
            raise AISelectionError("provider_invalid_response")
        if request.structured_output:
            try:
                structured_content = json.loads(content)
            except (ValueError, RecursionError):
                raise AISelectionError("provider_invalid_structured_response") from None
            if not isinstance(structured_content, dict):
                raise AISelectionError("provider_invalid_structured_response")
        return GenerationResult(
            content=content, effective_model=model.selection,
            finish_reason=response.get("done_reason"),
            input_tokens=response.get("prompt_eval_count") if isinstance(response.get("prompt_eval_count"), int) else None,
            output_tokens=response.get("eval_count") if isinstance(response.get("eval_count"), int) else None,
        )

    @staticmethod
    def _cloud_tag(name: str) -> bool:
        return name.casefold().endswith((":cloud", "-cloud"))

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(self.base_url + path, data=data, method=method,
                          headers={"Content-Type": "application/json"})
        timeout = self.timeout_seconds if path == "/api/chat" else min(2.0, self.timeout_seconds)
        # Ignore proxy environment variables: this adapter handles local prompts
        # and must not forward them to an ambient HTTP proxy.
        with build_opener(ProxyHandler({}), _RejectRedirects()).open(request, timeout=timeout) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError("provider_response_too_large")
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("invalid_provider_response")
        return value


class GatewayUnavailable(RuntimeError):
    """Safe generation failure suitable for mapping to an API reason code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class RagTextGenerationGateway:
    """Map Gemini's RAG request DTO onto the canonical P1 provider router.

    Preferences are fetched per call so changes take effect without restarting
    the backend. This adapter returns only effective model and usage metadata;
    it does not persist prompts or generated text.
    """

    _TASK_ALIASES = {
        "tutor_conversation": "tutor",
        "rag_answer": "rag_answer",
        "course_summary": "course_summary",
        "revision_sheet": "revision_sheet",
        "quiz": "quiz",
        "quiz_generation": "quiz",
        "flashcards": "flashcards",
        "flashcard_generation": "flashcards",
        "exercises": "exercise",
        "exercise_generation": "exercise",
        "exercise_explanation": "correction",
        "concept_extraction": "concept_extraction",
        "prerequisite_suggestions": "concept_extraction",
        "study_recommendation": "study_recommendation",
    }

    def __init__(self, runtime):
        self.runtime = runtime

    def generate(self, request):
        """Select an authorized model and execute one RAG generation request."""
        from ..contracts.ai import AISelectionPolicy, AITask, GenerationRequest as GatewayRequest
        from ..contracts.ai import ModelSelection, ProviderPreference
        from .database import LOCAL_USER, users
        from sqlalchemy import select
        from ..modules.rag.contracts import GenerationResponse as RagResponse

        if self.runtime.exam_mode_active(LOCAL_USER):
            raise GatewayUnavailable("exam_mode_active")
        task_key = self._TASK_ALIASES.get(request.task_type, "rag_answer")
        task = AITask(task_key)
        with self.runtime.engine.connect() as db:
            raw = db.scalar(select(users.c.preferences).where(users.c.id == LOCAL_USER)) or {}
        preference = ProviderPreference.model_validate((raw or {}).get("ai") or {})
        base = preference.tasks.get(task) or preference.default
        override = None
        if request.provider_override or request.model_override:
            if base is None and not (request.provider_override and request.model_override):
                raise GatewayUnavailable("partial_model_override_without_default")
            override = ModelSelection(
                provider_id=request.provider_override or base.provider_id,
                model_id=request.model_override or base.model_id,
            )
        gateway_request = GatewayRequest(
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
        allowed = set()
        if base is not None:
            allowed.add(base.provider_id)
        if request.provider_override:
            allowed.add(request.provider_override)
        if preference.automatic:
            allowed.update(item.provider_id for item in preference.fallback_candidates)
        policy = AISelectionPolicy(
            enabled=preference.enabled,
            remote_allowed=preference.cloud_data_consent,
            allowed_providers=allowed,
            fallback_candidates=preference.fallback_candidates if preference.automatic else [],
        )
        try:
            adapter, model = self.runtime.ai_router.select(gateway_request, preference, policy)
            if self.runtime.exam_mode_active(LOCAL_USER):
                raise AISelectionError("exam_mode_active")
            result = adapter.generate(gateway_request, model)
        except AISelectionError as exc:
            raise GatewayUnavailable(exc.code) from None
        except Exception:
            raise GatewayUnavailable("provider_request_failed") from None
        return RagResponse(
            content=result.content,
            provider=result.effective_model.provider_id,
            model=result.effective_model.model_id,
            finish_reason=result.finish_reason or "stop",
            input_tokens=result.input_tokens or 0,
            output_tokens=result.output_tokens or 0,
            estimated_cost_usd=result.estimated_cost,
        )


class AIProviderRegistry:
    """In-memory adapter registry with a short cache for provider readiness.

    Status discovery may perform local I/O or a provider connectivity check. A
    short TTL prevents each generation request from repeating that work; callers
    can explicitly invalidate the snapshot after configuration changes.
    """

    def __init__(self, status_ttl_seconds: float = 20.0):
        if status_ttl_seconds < 0:
            raise ValueError("Provider status TTL cannot be negative")
        self._adapters: dict[str, AIProviderAdapter] = {}
        self._status_ttl_seconds = status_ttl_seconds
        self._status_lock = Lock()
        self._status_snapshot: tuple[float, dict[str, ProviderStatus]] | None = None

    def register(self, provider_id: str, adapter: AIProviderAdapter) -> None:
        """Register an adapter and invalidate readiness in the same critical section.

        Args:
            provider_id: Stable identifier used in model selections.
            adapter: Provider implementation responsible for model discovery.

        Raises:
            ValueError: The provider ID is empty or already registered.
        """
        with self._status_lock:
            if not provider_id or provider_id in self._adapters:
                raise ValueError("Provider IDs must be non-empty and unique")
            self._adapters[provider_id] = adapter
            self._status_snapshot = None

    def invalidate_status(self) -> None:
        """Force the next status/model lookup to refresh adapter state."""
        with self._status_lock:
            self._status_snapshot = None

    def status(self, *, force_refresh: bool = False) -> list[ProviderStatus]:
        """Return a fresh-enough safe provider snapshot without probing per request."""
        with self._status_lock:
            cached = self._status_snapshot
            if (not force_refresh and cached is not None
                    and monotonic() - cached[0] < self._status_ttl_seconds):
                return [cached[1][key].model_copy(deep=True) for key in sorted(cached[1])]

            states: dict[str, ProviderStatus] = {}
            for provider_id, adapter in sorted(self._adapters.items()):
                try:
                    state = adapter.status()
                    if state.provider_id != provider_id:
                        state = ProviderStatus(
                            provider_id=provider_id, kind=state.kind, local=state.local,
                            configured=state.configured, available=False, error_code="provider_identity_mismatch",
                        )
                except Exception:
                    state = ProviderStatus(
                        provider_id=provider_id,
                        kind=ProviderKind.COMPATIBLE,
                        local=False,
                        configured=False,
                        available=False,
                        error_code="provider_status_failed",
                    )
                states[provider_id] = state.model_copy(deep=True)
            self._status_snapshot = (monotonic(), states)
            return [states[key].model_copy(deep=True) for key in sorted(states)]

    def models(self) -> dict[tuple[str, str], tuple[AIProviderAdapter, ModelDescriptor]]:
        """Build a current model index from adapters' advertised capabilities."""
        result = {}
        states = {state.provider_id: state for state in self.status()}
        for provider_id, state in states.items():
            if not state.configured or not state.available:
                continue
            adapter = self._adapters[provider_id]
            for model in state.models:
                if (model.enabled and model.selection.provider_id == provider_id
                        and model.local == state.local):
                    result[(model.selection.provider_id, model.selection.model_id)] = (adapter, model)
        return result


class AIModelRouter:
    """Resolve per-request provider selection using the documented precedence.

    Authorization, privacy and capability policy always wins. A request-level
    override does not mutate persistent preferences. Remote providers are
    eligible only when the caller supplies server-verified remote consent.
    Fallback is opt-in and restricted to server-approved candidates.
    """

    def __init__(self, registry: AIProviderRegistry):
        self.registry = registry

    def select(self, request: GenerationRequest, preference: ProviderPreference,
               policy: AISelectionPolicy) -> tuple[AIProviderAdapter, ModelDescriptor]:
        if not policy.enabled:
            raise AISelectionError("ai_disabled")

        index = self.registry.models()
        explicit = request.override or preference.tasks.get(request.task) or preference.default
        candidates = [explicit] if explicit else []
        if preference.automatic and explicit is None:
            candidates.extend(policy.fallback_candidates)

        eligible = self._eligible(candidates, index, request, policy)
        if eligible:
            return eligible[0]

        if request.override is not None or preference.tasks.get(request.task) or preference.default:
            failure = self._selection_failure(explicit, index, request, policy)
            if not request.allow_fallback:
                raise AISelectionError(failure)

        if request.allow_fallback:
            eligible = self._eligible(policy.fallback_candidates, index, request, policy)
            if eligible:
                return eligible[0]

        if not index:
            raise AISelectionError("no_provider_available")
        raise AISelectionError("no_compatible_authorized_model")

    @staticmethod
    def _eligible(candidates, index, request, policy):
        selected = []
        for candidate in candidates:
            if candidate is None:
                continue
            pair = index.get((candidate.provider_id, candidate.model_id))
            if pair is None or candidate.provider_id not in policy.allowed_providers:
                continue
            adapter, model = pair
            if not model.local and not policy.remote_allowed:
                continue
            if request.task not in model.capabilities.tasks:
                continue
            if request.structured_output and not model.capabilities.structured_output:
                continue
            if request.stream and not model.capabilities.streaming:
                continue
            if model.capabilities.max_output_tokens is not None and (
                request.max_output_tokens > model.capabilities.max_output_tokens
            ):
                continue
            estimated_input_tokens = sum(max(1, (len(message.content) + 3) // 4)
                                         for message in request.messages)
            if (model.capabilities.context_tokens is not None
                    and estimated_input_tokens + request.max_output_tokens > model.capabilities.context_tokens):
                continue
            selected.append((adapter, model))
        return selected

    @classmethod
    def _selection_failure(cls, selection, index, request, policy) -> str:
        if selection is None or (selection.provider_id, selection.model_id) not in index:
            return "selected_model_unavailable"
        _, model = index[(selection.provider_id, selection.model_id)]
        if selection.provider_id not in policy.allowed_providers:
            return "provider_not_authorized"
        if not model.local and not policy.remote_allowed:
            return "remote_transfer_not_authorized"
        if request.task not in model.capabilities.tasks:
            return "task_capability_missing"
        if request.structured_output and not model.capabilities.structured_output:
            return "structured_output_unavailable"
        if request.stream and not model.capabilities.streaming:
            return "streaming_unavailable"
        if (model.capabilities.max_output_tokens is not None
                and request.max_output_tokens > model.capabilities.max_output_tokens):
            return "output_limit_exceeded"
        estimated_input_tokens = sum(max(1, (len(message.content) + 3) // 4)
                                     for message in request.messages)
        if (model.capabilities.context_tokens is not None
                and estimated_input_tokens + request.max_output_tokens > model.capabilities.context_tokens):
            return "context_limit_exceeded"
        return "no_compatible_authorized_model"


class OpenAICompatibleProviderAdapter:
    """Opt-in OpenAI-compatible chat adapter (OpenAI, Groq, OpenRouter, Mistral, llama.cpp).

    Enforces HTTPS for remote hosts, refuses HTTP redirects, never leaks credentials in
    status responses or errors, and uses bounded timeouts and transient retries.
    """

    def __init__(
        self,
        provider_id: str,
        kind: ProviderKind,
        *,
        base_url: str,
        api_key: str | None = None,
        models: list[ModelDescriptor] | None = None,
        timeout_seconds: float = 45.0,
        max_retries: int = 1,
    ) -> None:
        parsed = urlparse(base_url)
        host = (parsed.hostname or "").lower()
        self.local = host in _LOOPBACK_HOSTS
        if self.local:
            if parsed.scheme != "http":
                raise ValueError("Loopback inference must use HTTP")
        else:
            if parsed.scheme != "https":
                raise ValueError("Remote AI providers must use HTTPS")
        self.provider_id = provider_id
        self.kind = kind
        self.base_url = base_url.rstrip("/")
        self._api_key = (api_key or "").strip() or None
        self._models = list(models or [])
        self.timeout_seconds = max(1.0, min(float(timeout_seconds), 120.0))
        self.max_retries = max(0, min(int(max_retries), 2))

    def status(self) -> ProviderStatus:
        configured = self.local or bool(self._api_key)
        if not configured:
            return ProviderStatus(
                provider_id=self.provider_id,
                kind=self.kind,
                local=self.local,
                configured=False,
                available=False,
                models=[],
                error_code="provider_not_configured",
            )
        return ProviderStatus(
            provider_id=self.provider_id,
            kind=self.kind,
            local=self.local,
            configured=True,
            available=bool(self._models),
            models=[m.model_copy(deep=True) for m in self._models],
            error_code=None if self._models else "no_models_configured",
        )

    def generate(self, request: GenerationRequest, model: ModelDescriptor) -> GenerationResult:
        if request.stream:
            raise AISelectionError("streaming_unavailable")
        if not self.local and not self._api_key:
            raise AISelectionError("provider_not_configured")
        if model.selection.provider_id != self.provider_id:
            raise AISelectionError("provider_not_authorized")

        payload: dict = {
            "model": model.selection.model_id,
            "messages": [m.model_dump() for m in request.messages],
            "max_tokens": request.max_output_tokens,
            "temperature": request.temperature,
        }
        if request.structured_output:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        endpoint = (
            self.base_url
            if self.base_url.endswith("/chat/completions")
            else f"{self.base_url}/chat/completions"
        )

        def _do_post() -> dict:
            req = Request(endpoint, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers)
            handlers = [_RejectRedirects()]
            if self.local:
                handlers.insert(0, ProxyHandler({}))
            with build_opener(*handlers).open(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("provider_response_too_large")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("invalid_provider_response")
            return data

        try:
            response = _call_with_bounded_retry(_do_post, max_retries=self.max_retries)
        except TimeoutError:
            raise AISelectionError("provider_timeout") from None
        except HTTPError as exc:
            if exc.code == 429:
                raise AISelectionError("provider_rate_limited") from None
            if exc.code in (401, 403):
                raise AISelectionError("provider_auth_failed") from None
            raise AISelectionError("provider_request_failed") from None
        except Exception:
            raise AISelectionError("provider_request_failed") from None

        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise AISelectionError("provider_invalid_response")
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise AISelectionError("provider_invalid_response")
        if request.structured_output:
            try:
                parsed = json.loads(content)
            except (ValueError, RecursionError):
                raise AISelectionError("provider_invalid_structured_response") from None
            if not isinstance(parsed, dict):
                raise AISelectionError("provider_invalid_structured_response")

        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        in_tok = usage.get("prompt_tokens") if isinstance(usage.get("prompt_tokens"), int) else None
        out_tok = usage.get("completion_tokens") if isinstance(usage.get("completion_tokens"), int) else None
        est_cost = None
        if in_tok is not None and model.estimated_cost_per_1k_input is not None:
            est_cost = (in_tok / 1000.0) * model.estimated_cost_per_1k_input
        if out_tok is not None and model.estimated_cost_per_1k_output is not None:
            est_cost = (est_cost or 0.0) + (out_tok / 1000.0) * model.estimated_cost_per_1k_output
        return GenerationResult(
            content=content,
            effective_model=model.selection,
            finish_reason=first.get("finish_reason"),
            input_tokens=in_tok,
            output_tokens=out_tok,
            estimated_cost=round(est_cost, 6) if est_cost is not None else None,
        )


def _default_cloud_model(
    provider_id: str,
    model_id: str,
    *,
    context_tokens: int = 32768,
    max_output_tokens: int = 4096,
    vision: bool = False,
) -> ModelDescriptor:
    return ModelDescriptor(
        selection=ModelSelection(provider_id=provider_id, model_id=model_id),
        local=False,
        capabilities=ModelCapabilities(
            tasks=set(AITask),
            structured_output=True,
            vision=vision,
            streaming=False,
            context_tokens=context_tokens,
            max_output_tokens=max_output_tokens,
        ),
    )


def discover_default_adapters() -> dict[str, AIProviderAdapter]:
    """Build local Ollama + honestly reported opt-in OpenAI-compatible cloud adapters.

    Never fabricates availability: cloud adapters report ``configured=False, available=False``
    unless the corresponding API key environment variable is present, and still require
    explicit ``cloud_data_consent=True`` at routing time.
    """
    ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    adapters: dict[str, AIProviderAdapter] = {
        OllamaProviderAdapter.provider_id: OllamaProviderAdapter(base_url=ollama_url),
    }
    cloud_specs = [
        (
            "openai",
            ProviderKind.OPENAI,
            "OPENAI_API_KEY",
            os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            128000,
        ),
        (
            "mistral",
            ProviderKind.MISTRAL,
            "MISTRAL_API_KEY",
            os.environ.get("MISTRAL_BASE_URL", "https://api.mistral.ai/v1"),
            os.environ.get("MISTRAL_MODEL", "mistral-small-latest"),
            65536,
        ),
        (
            "groq",
            ProviderKind.GROQ,
            "GROQ_API_KEY",
            os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
            os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
            32768,
        ),
        (
            "openrouter",
            ProviderKind.OPENROUTER,
            "OPENROUTER_API_KEY",
            os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            os.environ.get("OPENROUTER_MODEL", "google/gemma-3-12b-it:free"),
            32768,
        ),
    ]
    for provider_id, kind, env_var, base_url, model_id, ctx_tokens in cloud_specs:
        key = os.environ.get(env_var, "").strip() or None
        models = [_default_cloud_model(provider_id, model_id, context_tokens=ctx_tokens)] if key else []
        try:
            adapters[provider_id] = OpenAICompatibleProviderAdapter(
                provider_id,
                kind,
                base_url=base_url,
                api_key=key,
                models=models,
            )
        except ValueError:
            continue
    return adapters
