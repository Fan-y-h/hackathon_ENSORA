"""Base connector contract, rate limiter, retry policy, and shared utilities."""

from __future__ import annotations

import hashlib
import mimetypes
import random
import time
from abc import ABC, abstractmethod
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

from ..contracts import (
    AuthStatus,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
)

T = TypeVar("T")


class ConnectorError(RuntimeError):
    """Base error for source connector failures."""


class AuthenticationRequiredError(ConnectorError):
    """Raised on 401 or expired session; never retried automatically without reauth."""


class PermissionDeniedError(ConnectorError):
    """Raised on 403 or out-of-scope access attempt; never retried."""


class TransientSourceError(ConnectorError):
    """Raised on 429, 5xx, or transient network failure; eligible for bounded retry."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimiter:
    """Conservative token-spacing limiter respecting institutional service policies."""

    def __init__(
        self,
        min_interval_sec: float = 0.5,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = max(0.0, min_interval_sec)
        self._clock = clock
        self._sleeper = sleeper
        self._last_call: float | None = None

    def wait(self) -> None:
        if self._min_interval <= 0:
            return
        now = self._clock()
        if self._last_call is not None:
            elapsed = now - self._last_call
            remaining = self._min_interval - elapsed
            if remaining > 0:
                self._sleeper(remaining)
        self._last_call = self._clock()


def retry_with_backoff(
    operation: Callable[[], T],
    *,
    max_retries: int = 3,
    base_delay_sec: float = 0.5,
    max_delay_sec: float = 10.0,
    sleeper: Callable[[float], None] = time.sleep,
    jitter_fn: Callable[[float, float], float] = random.uniform,
) -> T:
    """Execute operation with exponential backoff + jitter for transient errors only.

    AuthenticationRequiredError and PermissionDeniedError propagate immediately.
    """
    attempt = 0
    while True:
        try:
            return operation()
        except (AuthenticationRequiredError, PermissionDeniedError):
            raise
        except TransientSourceError:
            if attempt >= max_retries:
                raise
            delay = min(max_delay_sec, base_delay_sec * (2**attempt))
            jitter = jitter_fn(0.0, delay * 0.25)
            sleeper(delay + jitter)
            attempt += 1


def sha256_bytes(data: bytes) -> str:
    """Compute hex SHA-256 digest for content deduplication and change detection."""
    return hashlib.sha256(data).hexdigest()


def guess_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    """Guess MIME type with academic format additions (.ipynb, .md, .yaml, .ics)."""
    lower = filename.lower()
    explicit = {
        ".ipynb": "application/x-ipynb+json",
        ".md": "text/markdown",
        ".markdown": "text/markdown",
        ".yaml": "application/x-yaml",
        ".yml": "application/x-yaml",
        ".ics": "text/calendar",
        ".py": "text/x-python",
        ".r": "text/x-rsrc",
        ".sql": "application/sql",
        ".tex": "application/x-tex",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pdf": "application/pdf",
        ".csv": "text/csv",
        ".json": "application/json",
    }
    for ext, mime in explicit.items():
        if lower.endswith(ext):
            return mime
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or fallback


def is_same_origin(url: str, base_url: str) -> bool:
    """Return True if url has the exact same HTTPS scheme and netloc as base_url."""
    u = urlparse(url)
    b = urlparse(base_url)
    return (
        u.scheme.lower() == b.scheme.lower()
        and u.netloc.lower() == b.netloc.lower()
        and bool(u.netloc)
    )


class SourceConnector(ABC):
    """Canonical read-only connector contract matching docs/DATA_ENGINEERING.md."""

    source_kind: SourceKind

    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """Return declared read-only capabilities."""

    @abstractmethod
    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        """Validate or initiate authorized access for this source."""

    @abstractmethod
    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Return paginated normalized records discovered within authorized scope."""

    @abstractmethod
    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        """Download or read authorized bytes for a single document record."""

    @abstractmethod
    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Convert a source-native item payload into a canonical SourceRecord."""

    @abstractmethod
    def disconnect(self) -> AuthStatus:
        """Revoke or clear local authorization state for this source."""


# Read-only default policy reference for Codex scheduler coordination.
# Connectors NEVER hardcode, read, or enforce sync intervals internally; Codex owns
# user-configurable per-source frequencies, disable toggles, and immediate manual syncs.
RECOMMENDED_SYNC_POLICY_SECONDS: dict[str, int | None] = {
    "ensora_drive": 7 * 24 * 3600,          # 7 days (incremental drvmod: watermark + streaming BFS)
    "moodle": 7 * 24 * 3600,                # 7 days (incremental known_versions skip + cookie HTTP)
    "pamplemousse:timetable": 6 * 3600,     # 6 hours (sections="timetable": iCalendar / p=121 EDT only)
    "pamplemousse:grades": 24 * 3600,       # 24 hours (sections="grades": p=1052 Notes only)
    "pamplemousse:catalog": 7 * 24 * 3600,  # 7 days (sections="catalog": p=107 Inscriptions/coefs only)
    "google_calendar": 30 * 60,             # 30 minutes (incremental syncToken + 410 recovery)
    "local": None,                          # on change (metadata-only scan_fingerprint + hash skip)
}
