"""Base connector contract, rate limiter, retry policy, and shared utilities."""

from __future__ import annotations

import hashlib
import mimetypes
import random
import threading
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
    """Thread-safe conservative token-spacing limiter respecting source policies.

    Serializes slot reservation under a lock while sleeping outside the lock so
    bounded concurrent workers stay paced without blocking unrelated work.
    """

    def __init__(
        self,
        min_interval_sec: float = 0.5,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._min_interval = max(0.0, float(min_interval_sec))
        self._clock = clock
        self._sleeper = sleeper
        self._lock = threading.Lock()
        self._next_allowed: float | None = None

    @property
    def min_interval_sec(self) -> float:
        """Return the current minimum interval between calls in seconds."""
        with self._lock:
            return self._min_interval

    def wait(self) -> None:
        """Reserve the next rate-limited execution slot and sleep if required."""
        with self._lock:
            interval = self._min_interval
            if interval <= 0:
                return
            now = self._clock()
            if self._next_allowed is None or now >= self._next_allowed:
                wait_sec = 0.0
                self._next_allowed = now + interval
            else:
                wait_sec = self._next_allowed - now
                self._next_allowed = self._next_allowed + interval
        if wait_sec > 0:
            self._sleeper(wait_sec)


class AdaptiveRateLimiter(RateLimiter):
    """Thread-safe rate limiter that backs off on HTTP 429 / Retry-After responses.

    On rate-limit notifications, pushes back the shared next-slot timestamp and
    increases spacing up to ``max_interval_sec``. After consecutive successful
    requests, spacing gradually relaxes toward ``base_interval_sec``.
    """

    def __init__(
        self,
        base_interval_sec: float = 0.12,
        *,
        max_interval_sec: float = 5.0,
        recovery_successes: int = 10,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(base_interval_sec, clock=clock, sleeper=sleeper)
        self._base_interval = max(0.0, float(base_interval_sec))
        self._max_interval = max(self._base_interval, float(max_interval_sec))
        self._recovery_successes = max(1, int(recovery_successes))
        self._success_streak = 0

    def notify_rate_limit(self, retry_after_sec: float | None = None) -> None:
        """Back off shared pacing after an HTTP 429 or quota-exceeded response.

        Args:
            retry_after_sec: Optional server-supplied ``Retry-After`` delay in seconds.
        """
        with self._lock:
            self._success_streak = 0
            bumped = max(self._min_interval * 2.0, self._base_interval * 2.0, 0.25)
            self._min_interval = min(self._max_interval, bumped)
            now = self._clock()
            cooldown = max(float(retry_after_sec or 0.0), self._min_interval)
            target = now + cooldown
            if self._next_allowed is None or target > self._next_allowed:
                self._next_allowed = target

    def notify_success(self) -> None:
        """Record a successful request and relax backoff after sustained stability."""
        with self._lock:
            if self._min_interval <= self._base_interval:
                return
            self._success_streak += 1
            if self._success_streak >= self._recovery_successes:
                self._success_streak = 0
                self._min_interval = max(self._base_interval, self._min_interval * 0.75)


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


# Read-only default policy reference derived from Codex's single authoritative schedule
# defaults (`backend.app.contracts.sync.DEFAULT_SYNC_INTERVALS_MINUTES`).
# Connectors NEVER hardcode, read, or enforce sync intervals internally; Codex owns
# user-configurable per-source frequencies, disable toggles, and immediate manual syncs.
def _derive_recommended_sync_policy_seconds() -> dict[str, int | None]:
    try:
        from ....contracts.sync import DEFAULT_SYNC_INTERVALS_MINUTES

        return {
            "ensora_drive": int(DEFAULT_SYNC_INTERVALS_MINUTES["ensora_drive"]) * 60,
            "moodle": int(DEFAULT_SYNC_INTERVALS_MINUTES["moodle"]) * 60,
            "pamplemousse:timetable": int(DEFAULT_SYNC_INTERVALS_MINUTES["pamplemousse.timetable"]) * 60,
            "pamplemousse:grades": int(DEFAULT_SYNC_INTERVALS_MINUTES["pamplemousse.grades"]) * 60,
            "pamplemousse:catalog": int(DEFAULT_SYNC_INTERVALS_MINUTES["pamplemousse.catalog"]) * 60,
            "google_calendar": int(DEFAULT_SYNC_INTERVALS_MINUTES["google_calendar"]) * 60,
            "local": None,
        }
    except Exception:
        return {
            "ensora_drive": 7 * 24 * 3600,
            "moodle": 7 * 24 * 3600,
            "pamplemousse:timetable": 6 * 3600,
            "pamplemousse:grades": 24 * 3600,
            "pamplemousse:catalog": 7 * 24 * 3600,
            "google_calendar": 30 * 60,
            "local": None,
        }


RECOMMENDED_SYNC_POLICY_SECONDS: dict[str, int | None] = _derive_recommended_sync_policy_seconds()
