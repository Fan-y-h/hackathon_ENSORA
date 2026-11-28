"""Pluggable multilingual embedding adapters and vector-space utilities for Ensora+ RAG.

Enforces two non-negotiable epistemic & architectural invariants:
1. **Explicit embedding-kind distinction**: Every provider exposes ``embedding_kind`` as
   either ``"feature_hash"`` (deterministic zero-dependency signed feature hashing) or
   ``"neural_dense"`` (trained neural multilingual embeddings such as ``bge-m3`` or
   ``multilingual-e5-small``). Deterministic feature hashing is never misrepresented as
   a trained neural semantic model.
2. **Strict vector-space isolation**: Every provider exposes an immutable ``space_id``
   pinning ``model_id``, ``revision``, and ``dimension`` so vectors from incompatible
   models are never compared or mixed.

Security invariants:
- Local loopback adapters (``OllamaEmbeddingAdapter``) reject non-loopback hosts, disable
  ambient HTTP proxies (``trust_env = False``), refuse HTTP redirects (``allow_redirects=False``),
  bound response payload sizes, and NEVER auto-download model weights.
- ``SentenceTransformersEmbeddingAdapter`` defaults to ``local_files_only=True`` so weights
  are never fetched silently from Hugging Face without explicit operator approval.
- ``OpenAICompatibleEmbeddingAdapter`` requires explicit ``remote_consent_granted=True``
  for any non-loopback endpoint and refuses redirects to prevent prompt/document exfiltration.
"""

from __future__ import annotations

from array import array
import hashlib
import math
from operator import mul
import re
import unicodedata
from typing import Any, Literal, Sequence
from urllib.parse import urlparse

import requests

from .contracts import EmbeddingKind, EmbeddingProvider


def _stem_token(token: str) -> str:
    """Conservative French/English plural/inflection stemmer for academic text."""
    t = token.strip().lower()
    if len(t) <= 3:
        return t
    if t.endswith("aux") and len(t) >= 6:
        return t[:-3] + "al"
    if t.endswith("ies") and len(t) >= 5:
        return t[:-3] + "y"
    if t.endswith("es") and len(t) >= 6 and t[-3] in ("s", "x", "z", "h"):
        return t[:-2]
    if t.endswith(("s", "x")) and not t.endswith(("ss", "us", "is", "as")):
        return t[:-1]
    return t

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_MAX_EMBEDDING_RESPONSE_BYTES = 16_000_000

_TOKEN_RE = re.compile(r"[a-zA-Z0-9_àâäéèêëîïôöùûüç]+")
_MATH_CODE_RE = re.compile(
    r"(?:\\[a-zA-Z]+|[A-Za-z_][A-Za-z0-9_]*\([^)]*\)|[A-Za-z_]+_[A-Za-z0-9_]+|[∀∃∈∉⊂⊆∪∩∑∏∫√∞≈≠≤≥±×÷→⇒⇔λμσθβαγΩΔ∇])"
)

# Conservative bilingual academic synonym groups projected onto shared latent buckets
# so French queries match English ENSAI course slides and vice versa in feature-hash mode.
_ACADEMIC_CONCEPT_GROUPS: tuple[tuple[str, ...], ...] = (
    ("apprentissage", "machine", "learning", "supervise", "supervised"),
    ("econometrie", "econometrics", "regression", "moindres", "carres", "ols", "mco"),
    ("probabilite", "probability", "aleatoire", "random", "esperance", "expectation", "variance"),
    ("statistique", "statistics", "estimateur", "estimator", "biais", "bias", "vraisemblance", "likelihood"),
    ("serie", "temporelle", "chronologique", "time", "series", "arima", "stationnaire", "stationary"),
    ("sondage", "sampling", "echantillon", "sample", "horvitz", "thompson", "strate", "strata"),
    ("bayesien", "bayesian", "posteriori", "posterior", "priori", "prior"),
    ("optimisation", "optimization", "gradient", "convexe", "convex", "lagrangien", "lagrangian"),
    ("base", "donnees", "database", "sql", "jointure", "join", "requete", "query", "normalisation"),
    ("anonymat", "anonymity", "confidentialite", "privacy", "differentielle", "differential"),
    ("reseau", "neurone", "neural", "network", "profond", "deep", "retropropagation", "backprop"),
    ("algebre", "algebra", "matrice", "matrix", "valeur", "propre", "eigenvalue", "svd"),
    ("python", "pandas", "numpy", "dataframe", "vecteur", "vector"),
)

_CONCEPT_BUCKET_MAP: dict[str, str] = {}
for _idx, _group in enumerate(_ACADEMIC_CONCEPT_GROUPS):
    _tag = f"__concept_{_idx}__"
    for _term in _group:
        _CONCEPT_BUCKET_MAP[_stem_token(_term)] = _tag


def _fold_ascii(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch)).lower()


def _is_loopback_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS


def _create_isolated_session(existing: requests.Session | None = None) -> requests.Session:
    if existing is not None:
        return existing
    session = requests.Session()
    # Disable ambient HTTP_PROXY/HTTPS_PROXY so local/consented document chunks are never rerouted
    session.trust_env = False
    return session


def pack_vector(vec: Sequence[float]) -> bytes:
    """Serialize a float vector into compact little-endian float32 bytes."""
    arr = array("f", vec)
    return arr.tobytes()


def unpack_vector(blob: bytes) -> array:
    """Deserialize float32 bytes back into an ``array('f')``."""
    arr = array("f")
    arr.frombytes(blob)
    return arr


def normalize_l2(vec: list[float]) -> list[float]:
    """L2-normalize a dense vector and return it."""
    norm_sq = sum(x * x for x in vec)
    if norm_sq <= 1e-18:
        return vec
    inv = 1.0 / math.sqrt(norm_sq)
    return [x * inv for x in vec]


def cosine_similarity_normalized(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute exact dot product (cosine similarity) between two unit-normalized vectors.

    Raises:
        ValueError: If vector dimensions do not match (prevents cross-space mixing).
    """
    if len(a) != len(b):
        raise ValueError(
            f"Incompatible embedding dimensions ({len(a)} != {len(b)}); "
            "refusing to compare vectors across distinct embedding spaces."
        )
    return float(sum(map(mul, a, b)))


class LocalMultilingualHashEmbedder:
    """Zero-dependency deterministic feature-hashing embedder (NOT a trained neural model).

    Projects French/English stemmed tokens, bilingual ENSAI concept buckets,
    character 4-grams, and exact math/code symbols into an L2-normalized dense
    vector via signed feature hashing (Weinberger et al., 2009). Ideal for instant
    zero-download offline operation; pair with ``OllamaEmbeddingAdapter`` or
    ``SentenceTransformersEmbeddingAdapter`` for true neural multilingual semantics.
    """

    def __init__(self, *, dimension: int = 256, revision: str = "v1") -> None:
        if dimension < 32 or dimension > 4096:
            raise ValueError("Embedding dimension must be between 32 and 4096.")
        self._dimension = int(dimension)
        self._revision = revision
        self._model_id = "ensora-local-multilingual-hash"

    @property
    def space_id(self) -> str:
        return f"{self._model_id}@{self._revision}:d{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_kind(self) -> EmbeddingKind:
        return "feature_hash"

    def _hash_feature(self, feature: str) -> tuple[int, float]:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "little")
        bucket = raw % self._dimension
        sign = 1.0 if ((raw >> 63) & 1) == 0 else -1.0
        return bucket, sign

    def _embed_one(self, text: str) -> list[float]:
        vec = [0.0] * self._dimension
        if not text or not text.strip():
            return vec

        folded = _fold_ascii(text)
        raw_tokens = _TOKEN_RE.findall(folded)
        stemmed: list[str] = []
        for tok in raw_tokens:
            if len(tok) < 2:
                continue
            st = _stem_token(tok)
            stemmed.append(st)
            b, s = self._hash_feature(f"w:{st}")
            vec[b] += s * 2.0
            concept = _CONCEPT_BUCKET_MAP.get(st)
            if concept is not None:
                cb, cs = self._hash_feature(f"c:{concept}")
                vec[cb] += cs * 2.2
            if len(st) >= 4:
                padded = f"^{st}$"
                for i in range(len(padded) - 3):
                    gb, gs = self._hash_feature(f"g4:{padded[i : i + 4]}")
                    vec[gb] += gs * 0.45

        for i in range(len(stemmed) - 1):
            bb, bs = self._hash_feature(f"bi:{stemmed[i]}_{stemmed[i + 1]}")
            vec[bb] += bs * 1.4

        for m in _MATH_CODE_RE.finditer(text):
            sym = m.group(0).strip()
            if sym:
                mb, ms = self._hash_feature(f"sym:{sym}")
                vec[mb] += ms * 2.5

        return normalize_l2(vec)

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed a batch of texts into unit-normalized feature-hash vectors."""
        return [self._embed_one(t) for t in texts]


class OllamaEmbeddingAdapter:
    """Loopback-only local Ollama neural embedding provider (``POST /api/embed``).

    Security guarantees:
    - Accepts HTTP loopback URLs ONLY (``127.0.0.1``, ``localhost``, ``::1``).
    - Rejects HTTP redirects (``allow_redirects=False``) and disables environment proxies.
    - Enforces bounded batch size and response byte limit.
    - Never auto-downloads models; expects operator-installed models (e.g. ``bge-m3``,
      ``nomic-embed-text``, ``qwen3-embedding``).
    """

    def __init__(
        self,
        model_id: str,
        *,
        dimension: int,
        base_url: str = "http://127.0.0.1:11434",
        revision: str = "local",
        timeout_sec: float = 30.0,
        batch_size: int = 32,
        session: requests.Session | None = None,
    ) -> None:
        self._model_id = model_id.strip()
        if not self._model_id:
            raise ValueError("Ollama embedding model_id must be non-empty.")
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
            raise ValueError("OllamaEmbeddingAdapter accepts HTTP loopback endpoints only.")
        self._dimension = int(dimension)
        self._base_url = base_url.rstrip("/")
        self._revision = revision
        self._timeout_sec = max(1.0, min(float(timeout_sec), 180.0))
        self._batch_size = max(1, min(int(batch_size), 128))
        self._session = _create_isolated_session(session)

    @property
    def space_id(self) -> str:
        return f"ollama/{self._model_id}@{self._revision}:d{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_kind(self) -> EmbeddingKind:
        return "neural_dense"

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Call local Ollama ``/api/embed`` in bounded batches and L2-normalize vectors."""
        if not texts:
            return []
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            resp = self._session.post(
                f"{self._base_url}/api/embed",
                json={"model": self._model_id, "input": batch},
                timeout=self._timeout_sec,
                allow_redirects=False,
            )
            if 300 <= resp.status_code < 400:
                raise RuntimeError("Blocked unexpected HTTP redirect from local Ollama embedding endpoint.")
            resp.raise_for_status()
            if len(resp.content) > _MAX_EMBEDDING_RESPONSE_BYTES:
                raise RuntimeError("Ollama embedding response exceeded safety size limit.")
            data = resp.json()
            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise RuntimeError("Ollama /api/embed returned unexpected embedding count.")
            for raw_vec in embeddings:
                vec = [float(x) for x in raw_vec]
                if len(vec) != self._dimension:
                    raise ValueError(
                        f"Ollama model '{self._model_id}' returned dimension {len(vec)}, expected {self._dimension}."
                    )
                out.append(normalize_l2(vec))
        return out


class SentenceTransformersEmbeddingAdapter:
    """Optional local ``sentence-transformers`` neural multilingual embedding adapter.

    Lazily imports ``sentence_transformers`` only when instantiated/invoked so the base
    Ensora+ backend keeps zero heavyweight ML dependencies. Defaults to
    ``local_files_only=True`` so model weights are NEVER downloaded automatically without
    explicit operator approval.
    """

    def __init__(
        self,
        model_id: str,
        *,
        dimension: int,
        revision: str = "local",
        device: str = "cpu",
        local_files_only: bool = True,
        query_prefix: str = "",
        document_prefix: str = "",
        model_instance: Any | None = None,
    ) -> None:
        self._model_id = model_id.strip()
        if not self._model_id:
            raise ValueError("model_id must be non-empty.")
        self._dimension = int(dimension)
        self._revision = revision
        self._device = device
        self._local_files_only = bool(local_files_only)
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._model = model_instance

    @property
    def space_id(self) -> str:
        return f"st/{self._model_id}@{self._revision}:d{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_kind(self) -> EmbeddingKind:
        return "neural_dense"

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers is not installed; install it explicitly or select "
                "LocalMultilingualHashEmbedder / OllamaEmbeddingAdapter."
            ) from exc
        self._model = SentenceTransformer(
            self._model_id,
            device=self._device,
            local_files_only=self._local_files_only,
        )
        return self._model

    def embed_texts(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        """Encode ``texts`` with the local neural transformer and L2-normalize outputs."""
        if not texts:
            return []
        model = self._load_model()
        prefix = self._query_prefix if is_query else self._document_prefix
        inputs = [f"{prefix}{t}" if prefix else t for t in texts]
        encoded = model.encode(inputs, normalize_embeddings=True)
        out: list[list[float]] = []
        for row in encoded:
            vec = [float(x) for x in row]
            if len(vec) != self._dimension:
                raise ValueError(
                    f"Model '{self._model_id}' produced dimension {len(vec)}, expected {self._dimension}."
                )
            out.append(normalize_l2(vec))
        return out


class OpenAICompatibleEmbeddingAdapter:
    """Optional OpenAI-compatible ``POST /v1/embeddings`` neural embedding adapter.

    Supports loopback compatible servers (llama.cpp server, vLLM, LocalAI) OR explicitly
    consented remote BYOK endpoints (OpenAI, Mistral, OpenRouter, etc.). Requires
    ``remote_consent_granted=True`` for any non-loopback host and refuses all HTTP redirects.
    """

    def __init__(
        self,
        model_id: str,
        *,
        dimension: int,
        base_url: str,
        api_key: str | None = None,
        remote_consent_granted: bool = False,
        revision: str = "api",
        timeout_sec: float = 30.0,
        batch_size: int = 32,
        session: requests.Session | None = None,
    ) -> None:
        self._model_id = model_id.strip()
        if not self._model_id:
            raise ValueError("Embedding model_id must be non-empty.")
        cleaned_url = base_url.rstrip("/")
        is_local = _is_loopback_url(cleaned_url)
        if not is_local:
            parsed = urlparse(cleaned_url)
            if parsed.scheme != "https":
                raise PermissionError("Remote embedding endpoints must use HTTPS.")
            if not remote_consent_granted:
                raise PermissionError(
                    "Remote embedding transfer blocked: explicit cloud_data_consent is required."
                )
        self._dimension = int(dimension)
        self._base_url = cleaned_url
        self._api_key = api_key
        self._revision = revision
        self._timeout_sec = max(1.0, min(float(timeout_sec), 180.0))
        self._batch_size = max(1, min(int(batch_size), 128))
        self._session = _create_isolated_session(session)

    @property
    def space_id(self) -> str:
        return f"openai-compat/{self._model_id}@{self._revision}:d{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def embedding_kind(self) -> EmbeddingKind:
        return "neural_dense"

    def embed_texts(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts via ``POST {base_url}/v1/embeddings`` without following redirects."""
        if not texts:
            return []
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        endpoint = (
            self._base_url
            if self._base_url.endswith("/embeddings")
            else f"{self._base_url}/v1/embeddings"
        )
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch_size):
            batch = list(texts[start : start + self._batch_size])
            resp = self._session.post(
                endpoint,
                headers=headers,
                json={"model": self._model_id, "input": batch},
                timeout=self._timeout_sec,
                allow_redirects=False,
            )
            if 300 <= resp.status_code < 400:
                raise RuntimeError("Blocked unexpected HTTP redirect from embedding endpoint.")
            resp.raise_for_status()
            if len(resp.content) > _MAX_EMBEDDING_RESPONSE_BYTES:
                raise RuntimeError("Embedding API response exceeded safety size limit.")
            payload = resp.json()
            rows = sorted(payload.get("data") or [], key=lambda item: int(item.get("index", 0)))
            if len(rows) != len(batch):
                raise RuntimeError("Embedding endpoint returned unexpected vector count.")
            for item in rows:
                vec = [float(x) for x in item["embedding"]]
                if len(vec) != self._dimension:
                    raise ValueError(
                        f"Embedding model '{self._model_id}' returned dimension {len(vec)}, expected {self._dimension}."
                    )
                out.append(normalize_l2(vec))
        return out


def build_embedding_provider(
    backend: Literal["feature_hash", "ollama", "sentence_transformers", "openai_compatible"] = "feature_hash",
    *,
    model_id: str | None = None,
    dimension: int = 256,
    base_url: str | None = None,
    api_key: str | None = None,
    remote_consent_granted: bool = False,
    local_files_only: bool = True,
) -> EmbeddingProvider:
    """Factory creating a configured deterministic feature-hash or neural embedding provider."""
    if backend == "feature_hash":
        return LocalMultilingualHashEmbedder(dimension=dimension)
    if backend == "ollama":
        return OllamaEmbeddingAdapter(
            model_id=model_id or "bge-m3",
            dimension=dimension,
            base_url=base_url or "http://127.0.0.1:11434",
        )
    if backend == "sentence_transformers":
        return SentenceTransformersEmbeddingAdapter(
            model_id=model_id or "intfloat/multilingual-e5-small",
            dimension=dimension,
            local_files_only=local_files_only,
        )
    if backend == "openai_compatible":
        if not base_url or not model_id:
            raise ValueError("openai_compatible embedding provider requires base_url and model_id.")
        return OpenAICompatibleEmbeddingAdapter(
            model_id=model_id,
            dimension=dimension,
            base_url=base_url,
            api_key=api_key,
            remote_consent_granted=remote_consent_granted,
        )
    raise ValueError(f"Unsupported embedding backend: {backend}")
