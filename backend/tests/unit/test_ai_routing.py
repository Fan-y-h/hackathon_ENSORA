"""Synthetic tests for AI routing precedence and privacy boundaries."""

import pytest

from backend.app.contracts.ai import (
    AISelectionPolicy,
    AITask,
    GenerationRequest,
    GenerationResult,
    ModelCapabilities,
    ModelDescriptor,
    ModelSelection,
    ProviderKind,
    ProviderPreference,
    ProviderStatus,
)
from backend.app.core.ai import (
    AIModelRouter,
    AIProviderRegistry,
    AISelectionError,
    GatewayUnavailable,
    RagTextGenerationGateway,
)
from backend.app.core.ai import OllamaProviderAdapter


class FakeProvider:
    def __init__(self, provider_id, *, local, models):
        self._status = ProviderStatus(provider_id=provider_id,
                                      kind=ProviderKind.OLLAMA if local else ProviderKind.OPENAI,
                                      local=local, configured=True, available=True, models=models)

    def status(self):
        return self._status

    def generate(self, request, model):
        raise AssertionError("Routing tests must not invoke an external provider")


@pytest.fixture
def router():
    registry = AIProviderRegistry()
    local = ModelDescriptor(
        selection=ModelSelection(provider_id="local", model_id="small"), local=True,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER, AITask.QUIZ},
                                       structured_output=True, streaming=True, max_output_tokens=4096),
    )
    remote = ModelDescriptor(
        selection=ModelSelection(provider_id="cloud", model_id="general"), local=False,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER, AITask.TUTOR}, streaming=True),
    )
    registry.register("local", FakeProvider("local", local=True, models=[local]))
    registry.register("cloud", FakeProvider("cloud", local=False, models=[remote]))
    return AIModelRouter(registry), local, remote


def test_request_override_precedes_task_and_global_without_mutating_preferences(router):
    model_router, local, remote = router
    preference = ProviderPreference(default=remote.selection,
                                    tasks={AITask.RAG_ANSWER: remote.selection})
    request = GenerationRequest(task=AITask.RAG_ANSWER,
                                messages=[{"role": "user", "content": "synthetic question"}],
                                override=local.selection)
    policy = AISelectionPolicy(allowed_providers={"local"})

    _adapter, selected = model_router.select(request, preference, policy)

    assert selected == local
    assert preference.default == remote.selection


def test_remote_model_requires_server_authorized_transfer_policy(router):
    model_router, _local, remote = router
    request = GenerationRequest(task=AITask.RAG_ANSWER,
                                messages=[{"role": "user", "content": "synthetic question"}],
                                override=remote.selection)
    with pytest.raises(AISelectionError, match="remote_transfer_not_authorized"):
        model_router.select(request, ProviderPreference(),
                            AISelectionPolicy(allowed_providers={"cloud"}, remote_allowed=False))


def test_incompatible_override_does_not_fallback_silently(router):
    model_router, local, remote = router
    request = GenerationRequest(task=AITask.QUIZ,
                                messages=[{"role": "user", "content": "synthetic quiz"}],
                                override=remote.selection)
    policy = AISelectionPolicy(allowed_providers={"local", "cloud"}, remote_allowed=True,
                               fallback_candidates=[local.selection])
    with pytest.raises(AISelectionError, match="task_capability_missing"):
        model_router.select(request, ProviderPreference(), policy)


def test_fallback_requires_request_opt_in_and_policy_allowlist(router):
    model_router, local, _remote = router
    unavailable = ModelSelection(provider_id="missing", model_id="model")
    request = GenerationRequest(task=AITask.QUIZ,
                                messages=[{"role": "user", "content": "synthetic quiz"}],
                                override=unavailable, allow_fallback=True)
    policy = AISelectionPolicy(allowed_providers={"local"}, fallback_candidates=[local.selection])

    _adapter, selected = model_router.select(request, ProviderPreference(), policy)

    assert selected == local


def test_disabled_ai_and_capability_mismatch_are_explicit(router):
    model_router, _local, remote = router
    request = GenerationRequest(task=AITask.QUIZ,
                                messages=[{"role": "user", "content": "synthetic quiz"}],
                                override=remote.selection, structured_output=True)
    with pytest.raises(AISelectionError, match="ai_disabled"):
        model_router.select(request, ProviderPreference(), AISelectionPolicy(enabled=False))
    with pytest.raises(AISelectionError, match="task_capability_missing"):
        model_router.select(request, ProviderPreference(),
                            AISelectionPolicy(allowed_providers={"cloud"}, remote_allowed=True))


def test_provider_readiness_uses_ttl_and_explicit_invalidation(monkeypatch):
    import backend.app.core.ai as ai_module

    selection = ModelSelection(provider_id="cached-local", model_id="fixture")
    descriptor = ModelDescriptor(
        selection=selection, local=True,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER}),
    )
    adapter = FakeProvider("cached-local", local=True, models=[descriptor])
    calls = 0
    original_status = adapter.status

    def counted_status():
        nonlocal calls
        calls += 1
        return original_status()

    adapter.status = counted_status
    now = [100.0]
    monkeypatch.setattr(ai_module, "monotonic", lambda: now[0])
    registry = AIProviderRegistry(status_ttl_seconds=15)
    registry.register("cached-local", adapter)

    status = registry.status()
    status[0].available = False
    assert registry.models()[("cached-local", "fixture")][1] == descriptor
    assert calls == 1

    now[0] = 115.0
    assert registry.status()[0].available is True
    assert calls == 2
    registry.invalidate_status()
    assert registry.status()[0].available is True
    assert calls == 3
    registry.status(force_refresh=True)
    assert calls == 4


def test_router_rejects_prompt_that_exceeds_model_context_window():
    selection = ModelSelection(provider_id="local", model_id="tiny-context")
    model = ModelDescriptor(
        selection=selection, local=True,
        capabilities=ModelCapabilities(tasks={AITask.TUTOR}, context_tokens=16, max_output_tokens=4),
    )
    registry = AIProviderRegistry()
    registry.register("local", FakeProvider("local", local=True, models=[model]))
    router = AIModelRouter(registry)
    request = GenerationRequest(task=AITask.TUTOR,
                                messages=[{"role": "user", "content": "word " * 100}],
                                override=selection, max_output_tokens=4)
    with pytest.raises(AISelectionError, match="context_limit_exceeded"):
        router.select(request, ProviderPreference(), AISelectionPolicy(allowed_providers={"local"}))


def test_registry_contains_probe_errors_and_rejects_inconsistent_locality(monkeypatch):
    registry = AIProviderRegistry()
    broken = FakeProvider("broken", local=True, models=[])

    def fail_probe():
        raise RuntimeError("private provider diagnostic")

    monkeypatch.setattr(broken, "status", fail_probe)
    registry.register("broken", broken)
    inconsistent = ModelDescriptor(
        selection=ModelSelection(provider_id="remote", model_id="fixture"),
        local=True,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER}),
    )
    registry.register("remote", FakeProvider("remote", local=False, models=[inconsistent]))
    states = {state.provider_id: state for state in registry.status()}

    assert states["broken"].error_code == "provider_status_failed"
    assert not states["broken"].available
    assert "private provider diagnostic" not in states["broken"].model_dump_json()
    assert registry.models() == {}


@pytest.mark.parametrize("response", ["invalid JSON", '["array"]', '{"answer": "ok"}'])
def test_ollama_validates_structured_response(monkeypatch, response):
    adapter = OllamaProviderAdapter()
    model = ModelDescriptor(
        selection=ModelSelection(provider_id=adapter.provider_id, model_id="fixture"),
        local=True,
        capabilities=ModelCapabilities(tasks={AITask.QUIZ}, structured_output=True),
    )
    request = GenerationRequest(
        task=AITask.QUIZ,
        messages=[{"role": "user", "content": "Synthetic quiz"}],
        structured_output=True,
    )

    def respond(method, path, payload):
        if path == "/api/show":
            assert payload == {"model": "fixture"}
            return {"model_info": {"general.architecture": "fixture"}}
        assert (method, path, payload["format"]) == ("POST", "/api/chat", "json")
        return {"message": {"content": response}}

    monkeypatch.setattr(adapter, "_request", respond)
    if response.startswith("{"):
        assert adapter.generate(request, model).content == response
    else:
        with pytest.raises(AISelectionError, match="provider_invalid_structured_response"):
            adapter.generate(request, model)


def test_ollama_rechecks_model_locality_before_dispatch(monkeypatch):
    adapter = OllamaProviderAdapter()
    model = ModelDescriptor(
        selection=ModelSelection(provider_id=adapter.provider_id, model_id="fixture"),
        local=True,
        capabilities=ModelCapabilities(tasks={AITask.TUTOR}),
    )
    request = GenerationRequest(
        task=AITask.TUTOR, messages=[{"role": "user", "content": "Synthetic private prompt"}],
    )

    def respond(method, path, payload):
        assert (method, path, payload) == ("POST", "/api/show", {"model": "fixture"})
        return {"remote_model": "fixture-upstream"}

    monkeypatch.setattr(adapter, "_request", respond)
    with pytest.raises(AISelectionError, match="local_model_required"):
        adapter.generate(request, model)


def test_ollama_adapter_is_local_only_and_never_downloads_models(monkeypatch):
    with pytest.raises(ValueError, match="loopback"):
        OllamaProviderAdapter("http://provider.example:11434")
    adapter = OllamaProviderAdapter()
    monkeypatch.setattr(adapter, "_request", lambda method, path, body=None: {
        "models": [
            {"name": "synthetic-small"},
            {"name": "synthetic:cloud"},
            {"name": "synthetic-alias", "remote_host": "https://example.invalid"},
        ],
    } if method == "GET" else {
        "message": {"content": "Synthetic local answer."}, "done_reason": "stop",
        "prompt_eval_count": 3, "eval_count": 4,
    })
    state = adapter.status()
    assert state.available and [item.selection.model_id for item in state.models] == ["synthetic-small"]
    assert state.models[0].local


def test_ollama_adapter_rejects_redirect_without_forwarding_prompt():
    """A loopback model service must not redirect prompt data to another host."""
    from urllib.error import HTTPError
    from urllib.request import Request
    import backend.app.core.ai as ai_module

    seen_redirect_handler = []

    class SyntheticOpener:
        def open(self, request, timeout):
            assert request.data == b'{"prompt": "synthetic private prompt"}'
            assert timeout == 2
            handler = seen_redirect_handler[0]
            assert handler.redirect_request(
                request, None, 307, "temporary redirect", {}, "http://example.invalid/capture"
            ) is None
            raise HTTPError(request.full_url, 307, "redirect rejected", {}, None)

    def build_synthetic_opener(*handlers):
        from backend.app.core.ai import _RejectRedirects

        assert any(isinstance(handler, _RejectRedirects) for handler in handlers)
        seen_redirect_handler.extend(handler for handler in handlers if isinstance(handler, _RejectRedirects))
        return SyntheticOpener()

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(ai_module, "build_opener", build_synthetic_opener)
    try:
        adapter = OllamaProviderAdapter(timeout_seconds=2)
        with pytest.raises(HTTPError) as error:
            adapter._request("POST", "/api/chat", {"prompt": "synthetic private prompt"})
        assert error.value.code == 307
        assert len(seen_redirect_handler) == 1
    finally:
        monkeypatch.undo()


def test_rag_gateway_maps_task_override_and_effective_provider_without_persisting_prompt(tmp_path):
    from types import SimpleNamespace
    from backend.app.contracts.ai import ProviderPreference
    from backend.app.core.database import LOCAL_USER, users
    from backend.app.core.runtime import Components, Runtime
    from backend.app.core.settings import Settings
    from sqlalchemy import select, update

    selection = ModelSelection(provider_id="fake-local", model_id="fixture-v1")
    descriptor = ModelDescriptor(
        selection=selection, local=True,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER}, max_output_tokens=4096),
    )

    class GeneratingProvider(FakeProvider):
        def generate(self, request, model):
            assert request.task == AITask.RAG_ANSWER
            assert request.messages[1].content == "Synthetic user prompt"
            return GenerationResult(content="Synthetic grounded response", effective_model=model.selection,
                                    input_tokens=4, output_tokens=3)

    provider = GeneratingProvider("fake-local", local=True, models=[descriptor])
    settings = Settings(config_dir=tmp_path / "config", local_data_dir=tmp_path / "data",
                        ensai_profile_dir=tmp_path / "config/sessions/ensai")
    exam_active = False
    with Runtime(settings, Components()) as base_runtime:
        registry = AIProviderRegistry()
        registry.register("fake-local", provider)
        gateway_runtime = SimpleNamespace(
            engine=base_runtime.engine,
            ai_router=AIModelRouter(registry),
            exam_mode_active=lambda _user_id: exam_active,
        )
        preference = ProviderPreference(enabled=True, default=selection)
        with base_runtime.engine.begin() as db:
            raw = db.scalar(select(users.c.preferences).where(users.c.id == LOCAL_USER))
            db.execute(update(users).where(users.c.id == LOCAL_USER).values(
                preferences={**raw, "ai": preference.model_dump(mode="json")}))
        from backend.app.modules.rag.contracts import GenerationRequest as RagRequest
        request = RagRequest(
            task_type="rag_answer", system_prompt="Synthetic system prompt",
            user_prompt="Synthetic user prompt", provider_override="fake-local",
            model_override="fixture-v1",
        )
        result = RagTextGenerationGateway(gateway_runtime).generate(request)
        assert result.content == "Synthetic grounded response"
        assert result.provider == "fake-local" and result.model == "fixture-v1"
        with base_runtime.engine.connect() as db:
            stored = db.scalar(select(users.c.preferences).where(users.c.id == LOCAL_USER))
        assert stored["ai"] == preference.model_dump(mode="json")
        exam_active = True
        with pytest.raises(GatewayUnavailable, match="exam_mode_active"):
            RagTextGenerationGateway(gateway_runtime).generate(request)


def test_openai_compatible_adapter_security_retry_and_honest_unconfigured(monkeypatch):
    from urllib.error import HTTPError
    import backend.app.core.ai as ai_module
    from backend.app.core.ai import OpenAICompatibleProviderAdapter, discover_default_adapters

    # Remote HTTP rejected; HTTPS required
    with pytest.raises(ValueError, match="HTTPS"):
        OpenAICompatibleProviderAdapter("cloud", ProviderKind.OPENAI, base_url="http://api.example.com/v1")

    # Unconfigured cloud adapter honestly reports configured=False, available=False, zero secrets
    unconf = OpenAICompatibleProviderAdapter("openai", ProviderKind.OPENAI, base_url="https://api.openai.com/v1")
    st = unconf.status()
    assert st.configured is False and st.available is False and st.error_code == "provider_not_configured"

    # Transient 503 retries once and succeeds, without leaking API key
    model = ModelDescriptor(
        selection=ModelSelection(provider_id="groq", model_id="llama-3.3-70b-versatile"),
        local=False,
        capabilities=ModelCapabilities(tasks={AITask.RAG_ANSWER}, structured_output=True),
        estimated_cost_per_1k_input=0.001,
        estimated_cost_per_1k_output=0.002,
    )
    adapter = OpenAICompatibleProviderAdapter(
        "groq",
        ProviderKind.GROQ,
        base_url="https://api.groq.com/openai/v1",
        api_key="gsk_super_secret_do_not_leak",
        models=[model],
        max_retries=1,
    )
    assert "gsk_super_secret_do_not_leak" not in adapter.status().model_dump_json()

    attempts = {"n": 0}

    class _FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, _n):
            return (
                b'{"choices":[{"message":{"content":"{\\"ok\\":true}"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":100,"completion_tokens":50}}'
            )

    class _FakeOpener:
        def open(self, req, timeout):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise HTTPError(req.full_url, 503, "transient", {}, None)
            return _FakeResp()

    monkeypatch.setattr(ai_module, "build_opener", lambda *h: _FakeOpener())
    monkeypatch.setattr(ai_module, "sleep", lambda _s: None)
    res = adapter.generate(
        GenerationRequest(
            task=AITask.RAG_ANSWER,
            messages=[{"role": "user", "content": "test"}],
            structured_output=True,
        ),
        model,
    )
    assert attempts["n"] == 2
    assert res.content == '{"ok":true}'
    assert res.input_tokens == 100 and res.output_tokens == 50
    assert res.estimated_cost == pytest.approx(0.0002)

    adapters = discover_default_adapters()
    assert "ollama-local" in adapters and "openai" in adapters
