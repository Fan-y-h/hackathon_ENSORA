"""Optional neural cross-encoder and HTTP rerankers for Ensora+ RAG retrieval.

Provides:
- ``SentenceTransformersCrossEncoderReranker``: opt-in local neural cross-encoder
  (e.g. ``BAAI/bge-reranker-v2-m3``) with ``local_files_only=True`` default and
  deterministic fallback to ``AcademicCrossFeatureReranker`` if unavailable.
- ``HttpRerankerAdapter``: opt-in loopback or explicitly consented remote ``/rerank``
  HTTP endpoint adapter (TEI / Jina / Cohere-compatible) with strict redirect rejection
  and proxy isolation.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence
from urllib.parse import urlparse

import requests

from .contracts import QueryScope, Reranker, RetrievedChunk

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class SentenceTransformersCrossEncoderReranker:
    """Optional local neural cross-encoder reranker with deterministic fallback.

    Never downloads weights automatically (``local_files_only=True`` by default).
    Preserves deterministic ``chunk_id`` tie-breaking and falls back gracefully to
    ``fallback_reranker`` if the optional ML package or local checkpoint is absent.
    """

    reranker_kind = "neural_cross_encoder"

    def __init__(
        self,
        model_id: str = "BAAI/bge-reranker-v2-m3",
        *,
        device: str = "cpu",
        local_files_only: bool = True,
        fallback_reranker: Reranker | None = None,
        cross_encoder_instance: Any | None = None,
    ) -> None:
        self._model_id = model_id.strip()
        self._device = device
        self._local_files_only = bool(local_files_only)
        self._fallback = fallback_reranker
        self._model = cross_encoder_instance

    @property
    def model_id(self) -> str:
        return self._model_id

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        from sentence_transformers import CrossEncoder  # type: ignore

        self._model = CrossEncoder(
            self._model_id,
            device=self._device,
            local_files_only=self._local_files_only,
        )
        return self._model

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        scope: QueryScope,
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        try:
            model = self._load_model()
            pairs = [
                (query, f"{c.title} {' / '.join(c.section)}\n{c.text[:1500]}")
                for c in candidates
            ]
            raw_scores = model.predict(pairs)
            scored = [
                (round(float(sc), 6), ch.chunk_id, replace(ch, rerank_score=round(float(sc), 6)))
                for ch, sc in zip(candidates, raw_scores)
            ]
            scored.sort(key=lambda item: (-item[0], item[1]))
            return [item[2] for item in scored[:top_k]]
        except Exception:
            if self._fallback is not None:
                return self._fallback.rerank(query, candidates, scope=scope, top_k=top_k)
            return list(candidates[:top_k])


class HttpRerankerAdapter:
    """Optional loopback or explicitly consented remote ``POST /rerank`` neural reranker.

    Rejects HTTP redirects, disables ambient proxies, and requires
    ``remote_consent_granted=True`` for any non-loopback HTTPS endpoint.
    """

    reranker_kind = "neural_http_reranker"

    def __init__(
        self,
        *,
        base_url: str,
        model_id: str,
        api_key: str | None = None,
        remote_consent_granted: bool = False,
        timeout_sec: float = 20.0,
        fallback_reranker: Reranker | None = None,
        session: requests.Session | None = None,
    ) -> None:
        cleaned = base_url.rstrip("/")
        parsed = urlparse(cleaned)
        is_local = parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS
        if not is_local:
            if parsed.scheme != "https":
                raise PermissionError("Remote reranker endpoints must use HTTPS.")
            if not remote_consent_granted:
                raise PermissionError(
                    "Remote reranking transfer blocked: explicit cloud_data_consent is required."
                )
        self._base_url = cleaned
        self._model_id = model_id
        self._api_key = api_key
        self._timeout_sec = max(1.0, min(float(timeout_sec), 60.0))
        self._fallback = fallback_reranker
        if session is not None:
            self._session = session
        else:
            sess = requests.Session()
            sess.trust_env = False
            self._session = sess

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievedChunk],
        *,
        scope: QueryScope,
        top_k: int,
    ) -> list[RetrievedChunk]:
        if not candidates:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        endpoint = self._base_url if self._base_url.endswith("/rerank") else f"{self._base_url}/rerank"
        docs = [f"{c.title}\n{c.text[:1500]}" for c in candidates]
        try:
            resp = self._session.post(
                endpoint,
                headers=headers,
                json={"model": self._model_id, "query": query, "documents": docs, "top_n": int(top_k)},
                timeout=self._timeout_sec,
                allow_redirects=False,
            )
            if 300 <= resp.status_code < 400:
                raise RuntimeError("Blocked unexpected HTTP redirect from reranker endpoint.")
            resp.raise_for_status()
            payload = resp.json()
            results = payload.get("results") if isinstance(payload, dict) else payload
            if not isinstance(results, list):
                raise RuntimeError("Unexpected reranker response shape.")
            scored: list[tuple[float, str, RetrievedChunk]] = []
            for entry in results:
                idx = int(entry["index"])
                sc = round(float(entry.get("relevance_score", entry.get("score", 0.0))), 6)
                if 0 <= idx < len(candidates):
                    ch = candidates[idx]
                    scored.append((sc, ch.chunk_id, replace(ch, rerank_score=sc)))
            scored.sort(key=lambda item: (-item[0], item[1]))
            return [item[2] for item in scored[:top_k]]
        except Exception:
            if self._fallback is not None:
                return self._fallback.rerank(query, candidates, scope=scope, top_k=top_k)
            return list(candidates[:top_k])
