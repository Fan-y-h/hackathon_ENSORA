"""Authorized read-only Ensora shared Google Drive connector.

Strictly scoped to the approved Ensora shared root folder:
    1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO

Authorized academic top-level folders ONLY: 1A / 2A / 3A.
The top-level 'Global' folder (and any non-academic top-level folder) is
EXCLUDED COMPLETELY before recursive traversal begins.
Personal Google Drive integration is explicitly excluded and blocked.
Shortcuts pointing to Global or outside the approved academic folders are never followed.
"""

from __future__ import annotations

import hashlib
import http.client
import io
import json
import logging
import os
import re
import ssl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from ..auth.google_oauth import GoogleOAuthAdapter
from ..config import DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID, SourcesRuntimeConfig
from ..contracts import (
    AccessStatus,
    AuthState,
    AuthStatus,
    ChangePage,
    ConnectorCapabilities,
    FetchResult,
    SourceKind,
    SourceRecord,
    utc_now_iso,
)
from ..credentials import redact_sensitive_text
from .base import (
    AuthenticationRequiredError,
    PermissionDeniedError,
    RateLimiter,
    SourceConnector,
    TransientSourceError,
    guess_mime_type,
    retry_with_backoff,
    sha256_bytes,
)

logger = logging.getLogger(__name__)

DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

GOOGLE_WORKSPACE_EXPORTS: dict[str, tuple[str, str]] = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}

GOOGLE_WORKSPACE_EXPORT_FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    "application/vnd.google-apps.document": (
        ("application/pdf", ".pdf"),
        ("text/plain", ".txt"),
    ),
    "application/vnd.google-apps.presentation": (
        ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ".pptx"),
        ("application/pdf", ".pdf"),
        ("text/plain", ".txt"),
    ),
    "application/vnd.google-apps.spreadsheet": (
        ("text/csv", ".csv"),
        ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    ),
    "application/vnd.google-apps.drawing": (
        ("application/pdf", ".pdf"),
        ("image/png", ".png"),
    ),
}

# Google Workspace types that have no binary download or export representation in Drive API v3
UNEXPORTABLE_WORKSPACE_MIMES: frozenset[str] = frozenset(
    {
        "application/vnd.google-apps.form",
        "application/vnd.google-apps.site",
        "application/vnd.google-apps.map",
        "application/vnd.google-apps.jam",
        "application/vnd.google-apps.script",
        "application/vnd.google-apps.fusiontable",
        "application/vnd.google-apps.drive-sdk",
    }
)

APPROVED_ACADEMIC_COHORT_FOLDERS: tuple[str, ...] = ("1A", "2A", "3A")
APPROVED_ACADEMIC_COHORT_KEYS: frozenset[str] = frozenset({"1a", "2a", "3a"})
EXCLUDED_TOP_LEVEL_FOLDER_KEYS: frozenset[str] = frozenset({"global"})
EXPECTED_COHORT_FOLDERS: frozenset[str] = frozenset(APPROVED_ACADEMIC_COHORT_FOLDERS)
DRIVE_SCOPE_POLICY_VERSION = 2
DRIVE_SCOPE_POLICY_ID = "academic_cohorts_v2:1a,2a,3a"

_COHORT_CANONICAL_BY_KEY: dict[str, str] = {
    "1a": "1A",
    "2a": "2A",
    "3a": "3A",
    "global": "Global",
}
_ACADEMIC_YEAR_RE = re.compile(r"\b(20\d{2})\s*[-/]\s*((?:20)?\d{2})\b")
_SEMESTER_SEGMENT_RE = re.compile(r"^(?:s[1-6]|semestre\s*[1-6])$", re.IGNORECASE)
_SPECIALIZATION_HINTS: tuple[str, ...] = (
    "data science",
    "biostatistique",
    "statistique",
    "economie",
    "économie",
    "finance",
    "actuariat",
    "marketing",
    "gestion des risques",
    "evaluation des politiques publiques",
    "évaluation des politiques publiques",
    "big data",
    "insee",
    "tronc commun",
    "ds",
    "epp",
    "id",
)


def extract_drive_top_level_cohort(folder_path: str | None) -> str | None:
    """Return the top-level folder segment directly beneath the Ensora Drive root.

    Examples:
    - "Ensora/1A/Stats" -> "1A"
    - "Ensora/2A/Finance/Global/Cours" -> "2A" (descendant named Global inside 2A is NOT top-level Global)
    - "Ensora/Global/Admin" -> "Global"
    - "Global/Admin" -> "Global"
    - "Ensora" -> None
    """
    if not folder_path:
        return None
    parts = [p.strip() for p in str(folder_path).replace("\\", "/").split("/") if p.strip()]
    if not parts:
        return None
    first_key = parts[0].lower()
    if first_key in _COHORT_CANONICAL_BY_KEY:
        return _COHORT_CANONICAL_BY_KEY[first_key]
    if len(parts) < 2:
        return None
    second_key = parts[1].lower()
    return _COHORT_CANONICAL_BY_KEY.get(second_key, parts[1])


def is_approved_drive_folder_path(folder_path: str | None) -> bool:
    """Return True iff folder_path lies inside an approved Ensora academic folder (1A, 2A, or 3A)."""
    top = extract_drive_top_level_cohort(folder_path)
    return top in EXPECTED_COHORT_FOLDERS


def is_global_drive_folder_path(folder_path: str | None) -> bool:
    """Return True iff folder_path belongs to the excluded top-level Ensora 'Global' folder."""
    return extract_drive_top_level_cohort(folder_path) == "Global"


def is_excluded_drive_folder_path(folder_path: str | None) -> bool:
    """Return True iff folder_path is excluded under the 1A/2A/3A-only Ensora Drive policy."""
    return not is_approved_drive_folder_path(folder_path)


def _extract_explicit_academic_year(*values: str | None) -> str | None:
    """Extract an explicit YYYY-YYYY academic year label if present; never guess."""
    for raw in values:
        if not raw:
            continue
        m = _ACADEMIC_YEAR_RE.search(str(raw))
        if not m:
            continue
        y1 = int(m.group(1))
        y2_str = m.group(2)
        y2 = int(y2_str) if len(y2_str) == 4 else (y1 // 100) * 100 + int(y2_str)
        if y2 == y1 + 1:
            return f"{y1}-{y2}"
    return None


class DriveSizeLimitError(ValueError):
    """Raised when a Drive file exceeds the configured download size limit."""

    def __init__(self, message: str, *, size_bytes: int, limit_bytes: int) -> None:
        super().__init__(message)
        self.size_bytes = size_bytes
        self.limit_bytes = limit_bytes


class DriveResourceNotFoundError(FileNotFoundError):
    """Raised when a Drive file or folder no longer exists (HTTP 404)."""


class DriveUnexportableError(ValueError):
    """Raised when a Google Workspace file cannot be exported or downloaded as binary."""


class DriveExportSizeLimitError(ValueError):
    """Raised when Google Drive rejects an export due to Google's 10MB export cap across all fallbacks."""


@dataclass(frozen=True)
class DriveFolderMetadata:
    """Metadata of the validated Ensora root folder."""

    folder_id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    web_view_link: str | None = None


def _classify_http_error(exc: Exception) -> Exception:
    """Translate Google API HttpError and transport/network exceptions into connector hierarchy."""
    if isinstance(
        exc,
        (
            AuthenticationRequiredError,
            PermissionDeniedError,
            TransientSourceError,
            DriveSizeLimitError,
            DriveResourceNotFoundError,
            DriveUnexportableError,
            DriveExportSizeLimitError,
        ),
    ):
        return exc

    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    msg = str(exc).lower()

    if status == 401:
        return AuthenticationRequiredError("Google Drive API returned HTTP 401; reauthorization required.")
    if status == 404:
        return DriveResourceNotFoundError("Google Drive API returned HTTP 404 (file not found or removed).")
    if status == 400:
        if "exportsizelimitexceeded" in msg:
            return DriveExportSizeLimitError("Google Drive export size limit exceeded (HTTP 400).")
        if any(tok in msg for tok in ("filenotexportable", "exportnotallowed", "conversionunsupported")):
            return DriveUnexportableError("Google Workspace file is not exportable via Drive API v3.")
    if status == 403:
        if "exportsizelimitexceeded" in msg:
            return DriveExportSizeLimitError("Google Drive export size limit exceeded (HTTP 403).")
        if any(
            tok in msg
            for tok in (
                "ratelimitexceeded",
                "userratelimitexceeded",
                "downloadquotaexceeded",
                "sharingratelimitexceeded",
                "quotaexceeded",
                "backenderror",
            )
        ):
            return TransientSourceError("Google Drive API rate/quota limit exceeded.", status_code=429)
        if "filenotexportable" in msg or "cannotdownloadfile" in msg:
            return DriveUnexportableError("Google Drive file does not permit download or export (HTTP 403).")
        return PermissionDeniedError("Google Drive API returned HTTP 403 (access denied).")
    if status in (408, 429, 500, 502, 503, 504):
        return TransientSourceError(f"Google Drive transient HTTP {status}.", status_code=status)

    # Translate transient socket/TLS/HTTP/connection/timeout drops so retry_with_backoff recovers cleanly
    if isinstance(
        exc,
        (
            TimeoutError,
            ConnectionError,
            ssl.SSLError,
            http.client.HTTPException,
            BrokenPipeError,
            ConnectionResetError,
        ),
    ) or any(
        tok in msg
        for tok in (
            "timed out",
            "timeout",
            "connection reset",
            "broken pipe",
            "eof occurred",
            "remotedisconnected",
            "servernotfounderror",
            "temporary failure in name resolution",
            "unable to find the server",
        )
    ):
        return TransientSourceError(
            f"Google Drive transient network error ({type(exc).__name__}).",
            status_code=None,
        )
    return exc


def _categorize_drive_error_reason(exc: Exception) -> str:
    if isinstance(exc, DriveSizeLimitError):
        return "size_limit_exceeded"
    if isinstance(exc, DriveResourceNotFoundError):
        return "not_found"
    if isinstance(exc, DriveUnexportableError):
        return "unexportable_workspace"
    if isinstance(exc, DriveExportSizeLimitError):
        return "export_size_limit"
    if isinstance(exc, AuthenticationRequiredError):
        return "auth_expired"
    if isinstance(exc, PermissionDeniedError):
        return "permission_denied"
    if isinstance(exc, TransientSourceError):
        if exc.status_code == 429:
            return "rate_limit"
        if exc.status_code is None:
            return "network_timeout"
        return f"transient_http_{exc.status_code}"
    return "other"


DRIVE_DEFAULT_RATE_LIMIT_CAP_SEC = 0.12
DRIVE_FOLDER_BATCH_SIZE = 15
DRIVE_LIST_PAGE_SIZE = 1000
DRIVE_PAGING_SNAPSHOT_TTL_SEC = 24 * 3600


class EnsoraDriveConnector(SourceConnector):
    """Read-only connector strictly restricted to the Ensora shared Drive folder tree."""

    source_kind = SourceKind.ENSORA_DRIVE

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        oauth_adapter: GoogleOAuthAdapter | None = None,
        drive_service_factory: Callable[[Any], Any] | None = None,
        rate_limiter: RateLimiter | None = None,
        batch_folder_queries: bool | None = None,
    ) -> None:
        self._config = config
        self._root_folder_id = (
            config.ensora_drive_root_folder_id or DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
        ).strip()
        if not self._root_folder_id:
            raise ValueError("Ensora Drive root folder ID must be configured.")
        self._oauth = oauth_adapter or GoogleOAuthAdapter(config, source=SourceKind.ENSORA_DRIVE)
        self._service_factory = drive_service_factory
        # Google Drive API v3 quota is 12,000 req/min/user; avoid applying 1.0s HTML-scraping
        # delays to Drive REST calls while keeping polite pacing + exponential backoff on 429.
        effective_interval = min(
            max(0.0, float(config.rate_limit_min_interval_sec)),
            DRIVE_DEFAULT_RATE_LIMIT_CAP_SEC,
        )
        self._rate_limiter = rate_limiter or RateLimiter(effective_interval)
        # Multi-folder OR batching (`pageSize=1000`) auto-enabled for real Google Drive API v3;
        # disabled by default for injected test fakes unless explicitly opted in.
        self._batch_folder_queries = (
            (drive_service_factory is None)
            if batch_folder_queries is None
            else bool(batch_folder_queries)
        )
        # Bounded process-local + durable 0o600 continuation states across pages & restarts
        self._paging_states: dict[str, dict[str, Any]] = {}
        self._cached_service: Any | None = None
        self._cached_creds: Any | None = None
        self._cached_service_factory_ref: Any | None = None
        self.last_scan_authoritative: bool = False
        self.last_fetch_errors_by_reason: dict[str, int] = {}
        self.last_stats: dict[str, Any] = {}

    @property
    def root_folder_id(self) -> str:
        return self._root_folder_id

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            source=SourceKind.ENSORA_DRIVE,
            supports_documents=True,
            supports_events=False,
            supports_assignments=False,
            supports_academic_records=False,
            supports_incremental=True,
            supports_full_scan=True,
            supports_download=True,
            read_only=True,
            fallback_formats=("local_folder", "zip"),
        )

    def _build_service(self, *, force_refresh: bool = False) -> Any:
        # Reuse initialized Drive API client across requests so 900+ file runs avoid rebuilding
        # discovery clients & TLS sockets on every call; refresh transparently if credentials expire.
        if (
            not force_refresh
            and self._cached_service is not None
            and self._cached_service_factory_ref is self._service_factory
            and not bool(getattr(self._cached_creds, "expired", False))
        ):
            return self._cached_service

        creds = self._oauth.get_credentials(allow_refresh=True, interactive=False)
        if creds is None:
            self._cached_service = None
            self._cached_creds = None
            raise AuthenticationRequiredError(
                "Google Drive OAuth token missing or expired; run Google authorization first."
            )
        if self._service_factory is not None:
            svc = self._service_factory(creds)
        else:
            from googleapiclient.discovery import build  # type: ignore

            timeout_sec = max(30.0, float(self._config.request_timeout_sec))
            try:
                import google_auth_httplib2  # type: ignore
                import httplib2  # type: ignore

                authed_http = google_auth_httplib2.AuthorizedHttp(
                    creds,
                    http=httplib2.Http(timeout=timeout_sec),
                )
                svc = build("drive", "v3", http=authed_http, cache_discovery=False)
            except Exception:
                svc = build("drive", "v3", credentials=creds, cache_discovery=False)

        self._cached_service = svc
        self._cached_creds = creds
        self._cached_service_factory_ref = self._service_factory
        return svc

    def _invalidate_service(self) -> None:
        self._cached_service = None
        self._cached_creds = None

    def authorize(self, config: dict[str, Any] | None = None) -> AuthStatus:
        interactive = bool((config or {}).get("interactive", False))
        status = self._oauth.authenticate(interactive=interactive)
        if not status.authenticated:
            return status
        try:
            meta = self.get_root_folder_metadata()
            return AuthStatus(
                source=SourceKind.ENSORA_DRIVE,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail=f"Validated read-only access to Ensora Drive root '{meta.name}'.",
                scopes=status.scopes,
                metadata={"root_folder_id": meta.folder_id, "root_name": meta.name},
            )
        except Exception as exc:
            translated = _classify_http_error(exc)
            state = (
                AuthState.EXPIRED
                if isinstance(translated, AuthenticationRequiredError)
                else AuthState.ERROR
            )
            return AuthStatus(
                source=SourceKind.ENSORA_DRIVE,
                state=state,
                authenticated=False,
                detail=redact_sensitive_text(f"Ensora Drive validation failed: {translated}"),
                requires_interactive_action=True,
            )

    def get_root_folder_metadata(self) -> DriveFolderMetadata:
        """Fetch and validate metadata of the approved Ensora root folder."""
        service = self._build_service()

        def _op():
            self._rate_limiter.wait()
            try:
                return (
                    service.files()
                    .get(
                        fileId=self._root_folder_id,
                        fields="id,name,mimeType,modifiedTime,webViewLink,trashed",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            except Exception as exc:
                raise _classify_http_error(exc) from exc

        meta = retry_with_backoff(_op, max_retries=self._config.max_retries)
        if meta.get("mimeType") != DRIVE_FOLDER_MIME:
            raise PermissionDeniedError(
                f"Configured Ensora root ID {self._root_folder_id} is not a Google Drive folder."
            )
        if meta.get("trashed"):
            raise PermissionDeniedError("Configured Ensora root folder is trashed.")
        return DriveFolderMetadata(
            folder_id=meta["id"],
            name=meta.get("name", "Ensora"),
            mime_type=meta["mimeType"],
            modified_time=meta.get("modifiedTime"),
            web_view_link=meta.get("webViewLink"),
        )

    def _resolve_folder_ancestry(
        self,
        folder_id: str,
        service: Any,
        *,
        verified_in_run: set[str] | None = None,
        require_approved_cohort: bool = True,
        max_depth: int = 20,
    ) -> tuple[bool, str | None, str | None, list[str]]:
        """Verify live ancestry proving folder_id is inside approved Ensora academic folders (1A/2A/3A).

        Never trusts process-lifetime cached ancestry across calls so folders moved
        outside 1A/2A/3A, moved into Global, or trashed after discovery are rejected.

        Returns `(approved, top_level_cohort_name, top_level_folder_id, relative_path_segments)`.
        """
        if folder_id == self._root_folder_id:
            if require_approved_cohort:
                return False, None, None, []
            return True, None, None, []
        if (
            verified_in_run is not None
            and folder_id in verified_in_run
            and (not require_approved_cohort or folder_id != self._root_folder_id)
        ):
            return True, None, None, []

        visited: set[str] = set()
        chain_names: list[str] = []
        current = folder_id
        for _ in range(max_depth):
            if current in visited:
                return False, None, None, []
            visited.add(current)
            if current == self._root_folder_id:
                if require_approved_cohort:
                    return False, None, None, []
                if verified_in_run is not None:
                    verified_in_run.update(v for v in visited if v != self._root_folder_id)
                return True, None, None, list(reversed(chain_names))
            if (
                verified_in_run is not None
                and current in verified_in_run
                and (not require_approved_cohort or current != self._root_folder_id)
            ):
                verified_in_run.update(v for v in visited if v != self._root_folder_id)
                return True, None, None, list(reversed(chain_names))
            try:
                self._rate_limiter.wait()
                meta = (
                    service.files()
                    .get(
                        fileId=current,
                        fields="id,name,mimeType,parents,trashed",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            except Exception:
                return False, None, None, []
            if meta.get("trashed"):
                return False, None, None, []
            node_name = str(meta.get("name") or "").strip()
            node_mime = str(meta.get("mimeType") or "")
            if node_name:
                chain_names.append(node_name)
            parents = meta.get("parents") or []
            if not parents:
                return False, None, None, []
            if self._root_folder_id in parents:
                if require_approved_cohort:
                    norm_name = node_name.lower()
                    is_folder = (not node_mime) or (node_mime == DRIVE_FOLDER_MIME)
                    if not is_folder or norm_name not in APPROVED_ACADEMIC_COHORT_KEYS:
                        return False, None, None, []
                    canonical_cohort = _COHORT_CANONICAL_BY_KEY[norm_name]
                    if verified_in_run is not None:
                        verified_in_run.update(v for v in visited if v != self._root_folder_id)
                    return True, canonical_cohort, current, list(reversed(chain_names))
                if verified_in_run is not None:
                    verified_in_run.update(v for v in visited if v != self._root_folder_id)
                return True, None, current, list(reversed(chain_names))
            current = parents[0]
        return False, None, None, []

    def _validate_target_folder_boundary(
        self,
        folder_id: str,
        service: Any,
        *,
        verified_in_run: set[str] | None = None,
        require_approved_cohort: bool = True,
        max_depth: int = 20,
    ) -> bool:
        """Verify live ancestry proving folder_id is inside an approved Ensora academic folder (1A/2A/3A)."""
        approved, _, _, _ = self._resolve_folder_ancestry(
            folder_id,
            service,
            verified_in_run=verified_in_run,
            require_approved_cohort=require_approved_cohort,
            max_depth=max_depth,
        )
        return approved

    def resolve_authorized_top_level_folders(
        self,
        service: Any | None = None,
        *,
        cohorts_filter: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Resolve direct children of the Ensora root into approved (1A/2A/3A) vs excluded (Global/other).

        Performs a single `files.list` call on the root folder BEFORE recursive traversal begins,
        ensuring Global and any non-academic top-level folders are never queued or listed.
        """
        svc = service or self._build_service()
        try:
            children = self._list_folder_children(svc, self._root_folder_id)
        except AuthenticationRequiredError:
            self._invalidate_service()
            svc = self._build_service(force_refresh=True)
            children = self._list_folder_children(svc, self._root_folder_id)

        order_rank = {name.lower(): idx for idx, name in enumerate(APPROVED_ACADEMIC_COHORT_FOLDERS)}
        authorized: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for entry in children:
            if entry.get("trashed"):
                continue
            mime = str(entry.get("mimeType") or "")
            raw_name = str(entry.get("name") or "").strip()
            norm_name = raw_name.lower()
            if mime == DRIVE_FOLDER_MIME and norm_name in APPROVED_ACADEMIC_COHORT_KEYS:
                if cohorts_filter and norm_name not in cohorts_filter:
                    continue
                authorized.append(entry)
            else:
                excluded.append(entry)
        authorized.sort(key=lambda e: (order_rank.get(str(e.get("name") or "").strip().lower(), 99), str(e.get("id") or "")))
        return authorized, excluded

    def _list_folder_children(
        self,
        service: Any,
        folder_id: str,
        *,
        page_size: int = DRIVE_LIST_PAGE_SIZE,
    ) -> list[dict[str, Any]]:
        """List direct children of a validated folder using paginated Drive v3 queries."""
        children: list[dict[str, Any]] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed = false"
        fields = (
            "nextPageToken, files(id, name, mimeType, parents, modifiedTime, "
            "md5Checksum, version, size, webViewLink, shortcutDetails, trashed)"
        )

        while True:
            def _op(token=page_token):
                self._rate_limiter.wait()
                try:
                    return (
                        service.files()
                        .list(
                            q=query,
                            pageSize=page_size,
                            pageToken=token,
                            fields=fields,
                            includeItemsFromAllDrives=True,
                            supportsAllDrives=True,
                        )
                        .execute()
                    )
                except Exception as exc:
                    raise _classify_http_error(exc) from exc

            resp = retry_with_backoff(_op, max_retries=self._config.max_retries)
            children.extend(resp.get("files") or [])
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return children

    def _list_folders_batch(
        self,
        service: Any,
        folder_ids: list[str],
        *,
        page_size: int = DRIVE_LIST_PAGE_SIZE,
    ) -> dict[str, list[dict[str, Any]]]:
        """List children for up to DRIVE_FOLDER_BATCH_SIZE validated folders in a single Drive API query."""
        grouped: dict[str, list[dict[str, Any]]] = {fid: [] for fid in folder_ids}
        if not folder_ids:
            return grouped
        if len(folder_ids) == 1:
            grouped[folder_ids[0]] = self._list_folder_children(
                service, folder_ids[0], page_size=page_size
            )
            return grouped

        parents_clause = " or ".join(f"'{fid}' in parents" for fid in folder_ids)
        query = f"({parents_clause}) and trashed = false"
        fields = (
            "nextPageToken, files(id, name, mimeType, parents, modifiedTime, "
            "md5Checksum, version, size, webViewLink, shortcutDetails, trashed)"
        )
        page_token: str | None = None
        while True:
            def _op(token=page_token):
                self._rate_limiter.wait()
                try:
                    return (
                        service.files()
                        .list(
                            q=query,
                            pageSize=page_size,
                            pageToken=token,
                            fields=fields,
                            includeItemsFromAllDrives=True,
                            supportsAllDrives=True,
                        )
                        .execute()
                    )
                except Exception as exc:
                    raise _classify_http_error(exc) from exc

            resp = retry_with_backoff(_op, max_retries=self._config.max_retries)
            for entry in resp.get("files") or []:
                for pid in entry.get("parents") or []:
                    if pid in grouped:
                        grouped[pid].append(entry)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return grouped

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Normalize a Drive file metadata dict + folder_path into a SourceRecord."""
        file_id = str(raw_item["id"])
        name = str(raw_item.get("name") or file_id)
        mime_type = str(raw_item.get("mimeType") or guess_mime_type(name))
        folder_path = str(raw_item.get("_folder_path") or "Ensora")
        parts = [p.strip() for p in folder_path.replace("\\", "/").split("/") if p.strip()]

        # Extract cohort (1A/2A/3A), academic year, semester, specialization and subject hints
        cohort_hint = extract_drive_top_level_cohort(folder_path)
        academic_year_hint = _extract_explicit_academic_year(folder_path, name)
        semester_hint: str | None = None
        specialization_hint: str | None = None
        subject_hint: str | None = None

        cohort_idx: int | None = None
        if cohort_hint:
            for i, seg in enumerate(parts):
                if seg.lower() == cohort_hint.lower():
                    cohort_idx = i
                    break
        if cohort_idx is not None:
            descendants = parts[cohort_idx + 1 :]
            subject_candidates: list[str] = []
            for seg in descendants:
                low_seg = seg.lower()
                if _SEMESTER_SEGMENT_RE.match(seg):
                    if semester_hint is None:
                        semester_hint = seg.upper() if low_seg.startswith("s") and len(seg) <= 2 else seg
                    continue
                if _extract_explicit_academic_year(seg) and re.fullmatch(r"20\d{2}\s*[-/]\s*(?:20)?\d{2}", seg):
                    continue
                if specialization_hint is None and any(
                    low_seg == sp or (len(sp) > 3 and sp in low_seg) for sp in _SPECIALIZATION_HINTS
                ):
                    specialization_hint = seg
                    continue
                subject_candidates.append(seg)
            if subject_candidates:
                subject_hint = subject_candidates[0]
            elif descendants:
                subject_hint = descendants[0]

        academic_category: str | None = None
        if cohort_hint and specialization_hint:
            academic_category = f"{cohort_hint} / {specialization_hint}"
        elif cohort_hint:
            academic_category = cohort_hint

        top_folder_id = raw_item.get("_top_level_folder_id")
        top_folder_name = raw_item.get("_top_level_folder_name") or cohort_hint

        version = raw_item.get("md5Checksum") or raw_item.get("version") or raw_item.get("modifiedTime")
        size_raw = raw_item.get("size")
        size_bytes = int(size_raw) if size_raw is not None and str(size_raw).isdigit() else None
        export_target = GOOGLE_WORKSPACE_EXPORTS.get(mime_type)

        provenance: dict[str, Any] = {
            "source": SourceKind.ENSORA_DRIVE.value,
            "root_folder_id": self._root_folder_id,
            "drive_file_id": file_id,
            "folder_path": folder_path,
            "scope_policy": DRIVE_SCOPE_POLICY_ID,
            "retrieved_at": utc_now_iso(),
        }
        if top_folder_id:
            provenance["top_level_folder_id"] = str(top_folder_id)
        if top_folder_name:
            provenance["top_level_folder_name"] = str(top_folder_name)
        if academic_year_hint:
            provenance["academic_year"] = academic_year_hint

        return SourceRecord(
            source_kind=SourceKind.ENSORA_DRIVE,
            external_id=f"ensora_drive:{file_id}",
            title=name,
            record_kind="document",
            remote_version=str(version) if version is not None else None,
            remote_modified_at=raw_item.get("modifiedTime"),
            mime_type=export_target[0] if export_target else mime_type,
            size_bytes=size_bytes,
            content_hash=raw_item.get("md5Checksum"),
            folder_path=folder_path,
            course_title=subject_hint,
            section_title=parts[-1] if parts else None,
            source_url=raw_item.get("webViewLink"),
            access_status=AccessStatus.TOMBSTONED if raw_item.get("trashed") else AccessStatus.ACTIVE,
            download_url=f"gdrive://{file_id}",
            payload={
                "drive_file_id": file_id,
                "original_mime_type": mime_type,
                "export_mime_type": export_target[0] if export_target else None,
                "export_extension": export_target[1] if export_target else None,
                "cohort_hint": cohort_hint,
                "subject_hint": subject_hint,
                "specialization_hint": specialization_hint,
                "semester_hint": semester_hint,
                "academic_year": academic_year_hint,
                "academic_category": academic_category,
                "top_level_folder_id": str(top_folder_id) if top_folder_id else None,
                "top_level_folder_name": str(top_folder_name) if top_folder_name else None,
                "scope_policy": DRIVE_SCOPE_POLICY_ID,
                "parents": raw_item.get("parents") or [],
            },
            provenance=provenance,
        )

    def _paging_snapshot_dir(self) -> Path:
        return self._config.config_dir / "state" / "drive_paging"

    @staticmethod
    def _serialize_record(rec: SourceRecord) -> dict[str, Any]:
        return rec.to_dict()

    @staticmethod
    def _deserialize_record(data: dict[str, Any]) -> SourceRecord:
        return SourceRecord(
            source_kind=SourceKind(data["source_kind"]),
            external_id=str(data["external_id"]),
            title=str(data["title"]),
            record_kind=str(data.get("record_kind", "document")),
            remote_version=data.get("remote_version"),
            remote_modified_at=data.get("remote_modified_at"),
            mime_type=data.get("mime_type"),
            size_bytes=data.get("size_bytes"),
            content_hash=data.get("content_hash"),
            folder_path=data.get("folder_path"),
            course_id=data.get("course_id"),
            course_title=data.get("course_title"),
            section_title=data.get("section_title"),
            source_url=data.get("source_url"),
            access_status=AccessStatus(data.get("access_status", "active")),
            download_url=data.get("download_url"),
            payload=dict(data.get("payload") or {}),
            provenance=dict(data.get("provenance") or {}),
        )

    def _save_durable_paging_state(self, handle: str, state: dict[str, Any]) -> None:
        """Persist metadata-only BFS continuation atomically (0o600, O_NOFOLLOW) across restarts."""
        key = handle.split(":", 1)[-1]
        if not key or not key.isalnum():
            return
        snap_dir = self._paging_snapshot_dir()
        try:
            snap_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(snap_dir, 0o700)
        except OSError:
            return
        target = snap_dir / f"{key}.json"
        if target.is_symlink():
            return
        tmp = snap_dir / f".{key}.{os.getpid()}.tmp"
        serialized = {
            "version": DRIVE_SCOPE_POLICY_VERSION,
            "scope_policy": DRIVE_SCOPE_POLICY_ID,
            "created_at": time.time(),
            "root_folder_id": self._root_folder_id,
            "fingerprint": state["fingerprint"],
            "start_folder": state["start_folder"],
            "cohorts_filter": sorted(state["cohorts_filter"]),
            "max_files": state["max_files"],
            "max_depth": state["max_depth"],
            "modified_since": state["modified_since"],
            "known_versions": state["known_versions"],
            "has_membership_baseline": state["has_membership_baseline"],
            "known_external_ids": sorted(state["known_external_ids"]),
            "known_titles": state["known_titles"],
            "known_folders": state.get("known_folders") or {},
            "authorized_top_level_folders": state.get("authorized_top_level_folders") or [],
            "top_folder_id_by_cohort": state.get("top_folder_id_by_cohort") or {},
            "excluded_top_level_folders_count": int(state.get("excluded_top_level_folders_count", 0)),
            "queue": [list(t) for t in state["queue"]],
            "visited_folders": sorted(state["visited_folders"]),
            "verified_in_run": sorted(
                fid for fid in state["verified_in_run"] if fid != self._root_folder_id
            ),
            "pending_items": [self._serialize_record(r) for r in state["pending_items"]],
            "membership_additions": [self._serialize_record(r) for r in state["membership_additions"]],
            "seen_live_ids": sorted(state["seen_live_ids"]),
            "warnings": list(state["warnings"]),
            "errors": list(state["errors"]),
            "depth_truncated": bool(state["depth_truncated"]),
            "max_files_hit": bool(state["max_files_hit"]),
            "reconciled": bool(state["reconciled"]),
            "latest_modified": state["latest_modified"],
            "total_discovered": int(state["total_discovered"]),
            "total_emitted": int(state["total_emitted"]),
            "shortcuts_skipped": int(state["shortcuts_skipped"]),
            "excluded_shortcuts_skipped": int(state.get("excluded_shortcuts_skipped", 0)),
            "unexportable_skipped": int(state["unexportable_skipped"]),
            "api_list_calls": int(state.get("api_list_calls", 0)),
        }
        try:
            fd = os.open(
                str(tmp),
                os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.fchmod(fh.fileno(), 0o600)
                json.dump(serialized, fh, ensure_ascii=True, separators=(",", ":"))
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
            # Prune only expired (>TTL) legacy snapshots; retain recent snapshots so crash-replay
            # from a previously committed job cursor remains idempotent across restarts.
            now_ts = time.time()
            for old in snap_dir.glob("*.json"):
                if old == target or old.is_symlink():
                    continue
                try:
                    if (now_ts - old.stat().st_mtime) > DRIVE_PAGING_SNAPSHOT_TTL_SEC:
                        old.unlink(missing_ok=True)
                except OSError:
                    continue
        except OSError:
            tmp.unlink(missing_ok=True)

    def _delete_durable_paging_state(self, handle: str | None) -> None:
        if not handle or not handle.startswith("drvpage:"):
            return
        key = handle.split(":", 1)[-1]
        if not key or not key.isalnum():
            return
        target = self._paging_snapshot_dir() / f"{key}.json"
        try:
            if target.exists() and not target.is_symlink():
                target.unlink(missing_ok=True)
        except OSError:
            pass

    def _load_durable_paging_state(self, handle: str) -> dict[str, Any] | None:
        key = handle.split(":", 1)[-1]
        if not key or not key.isalnum():
            return None
        target = self._paging_snapshot_dir() / f"{key}.json"
        if not target.is_file() or target.is_symlink():
            return None
        try:
            fd = os.open(str(target), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception:
            return None
        if not isinstance(raw, dict) or raw.get("root_folder_id") != self._root_folder_id:
            return None
        # Invalidate any legacy checkpoint created under the previous broader scope (before Global exclusion)
        if (
            int(raw.get("version") or 0) < DRIVE_SCOPE_POLICY_VERSION
            or raw.get("scope_policy") != DRIVE_SCOPE_POLICY_ID
        ):
            self._delete_durable_paging_state(handle)
            return None
        created_at = float(raw.get("created_at") or 0.0)
        if (time.time() - created_at) > DRIVE_PAGING_SNAPSHOT_TTL_SEC:
            self._delete_durable_paging_state(handle)
            return None
        raw_queue = [(str(a), str(b), int(c)) for a, b, c in (raw.get("queue") or [])]
        filtered_queue = [t for t in raw_queue if is_approved_drive_folder_path(t[1])]
        raw_pending = [self._deserialize_record(r) for r in (raw.get("pending_items") or [])]
        filtered_pending = [
            r for r in raw_pending
            if r.access_status == AccessStatus.TOMBSTONED or is_approved_drive_folder_path(r.folder_path)
        ]
        raw_additions = [self._deserialize_record(r) for r in (raw.get("membership_additions") or [])]
        filtered_additions = [
            r for r in raw_additions if is_approved_drive_folder_path(r.folder_path)
        ]
        return {
            "scope_policy": DRIVE_SCOPE_POLICY_ID,
            "fingerprint": str(raw["fingerprint"]),
            "start_folder": str(raw["start_folder"]),
            "cohorts_filter": set(raw.get("cohorts_filter") or []),
            "max_files": raw.get("max_files"),
            "max_depth": int(raw.get("max_depth", 12)),
            "modified_since": raw.get("modified_since"),
            "known_versions": dict(raw.get("known_versions") or {}),
            "has_membership_baseline": bool(raw.get("has_membership_baseline", False)),
            "known_external_ids": set(raw.get("known_external_ids") or []),
            "known_titles": dict(raw.get("known_titles") or {}),
            "known_folders": dict(raw.get("known_folders") or {}),
            "authorized_top_level_folders": list(raw.get("authorized_top_level_folders") or []),
            "top_folder_id_by_cohort": dict(raw.get("top_folder_id_by_cohort") or {}),
            "excluded_top_level_folders_count": int(raw.get("excluded_top_level_folders_count", 0)),
            "queue": filtered_queue,
            "visited_folders": set(raw.get("visited_folders") or []),
            "verified_in_run": {
                str(fid)
                for fid in (raw.get("verified_in_run") or [])
                if str(fid) != self._root_folder_id
            },
            "pending_items": filtered_pending,
            "membership_additions": filtered_additions,
            "seen_live_ids": set(raw.get("seen_live_ids") or []),
            "warnings": list(raw.get("warnings") or []),
            "errors": list(raw.get("errors") or []),
            "depth_truncated": bool(raw.get("depth_truncated", False)),
            "max_files_hit": bool(raw.get("max_files_hit", False)),
            "reconciled": bool(raw.get("reconciled", False)),
            "latest_modified": raw.get("latest_modified"),
            "total_discovered": int(raw.get("total_discovered", 0)),
            "total_emitted": int(raw.get("total_emitted", 0)),
            "shortcuts_skipped": int(raw.get("shortcuts_skipped", 0)),
            "excluded_shortcuts_skipped": int(raw.get("excluded_shortcuts_skipped", 0)),
            "unexportable_skipped": int(raw.get("unexportable_skipped", 0)),
            "api_list_calls": int(raw.get("api_list_calls", 0)),
        }

    def _store_bfs_paging_state(self, state: dict[str, Any]) -> str:
        """Store live streaming-BFS traversal state in memory AND durable private 0o600 storage."""
        pending = state["pending_items"]
        seed = json.dumps(
            {
                "fp": state["fingerprint"],
                "em": state["total_emitted"],
                "q": len(state["queue"]),
                "p": len(pending),
                "first": pending[0].external_id if pending else "",
                "v": len(state["visited_folders"]),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        handle = f"drvpage:{key}"
        if len(self._paging_states) >= 64:
            oldest = next(iter(self._paging_states))
            # Evict ONLY the in-memory entry; retain the durable 0o600 on-disk snapshot until
            # its 24h TTL expires so prior interrupted job checkpoints remain replayable.
            self._paging_states.pop(oldest, None)
        self._paging_states[handle] = state
        self._save_durable_paging_state(handle, state)
        return handle

    def _advance_bfs_page(
        self,
        state: dict[str, Any],
        *,
        page_size: int,
        drain_offset: int = 0,
    ) -> ChangePage:
        """Advance incremental BFS traversal only enough to serve the next page.

        Batches up to DRIVE_FOLDER_BATCH_SIZE folders per `files.list` call (`pageSize=1000`)
        on real Google Drive API v3 and yields page 1 as soon as > page_size files are found,
        while guaranteeing authoritative membership reconciliation, tombstones, and durable
        `drvmod:` watermarks ONLY once the entire approved tree finishes cleanly.
        """
        service = self._build_service()
        queue: list[tuple[str, str, int]] = state["queue"]
        visited_folders: set[str] = state["visited_folders"]
        verified_in_run: set[str] = state["verified_in_run"]
        pending_items: list[SourceRecord] = state["pending_items"]
        membership_additions: list[SourceRecord] = state["membership_additions"]
        seen_live_ids: set[str] = state["seen_live_ids"]
        known_versions: dict[str, str] = state["known_versions"]
        known_external_ids: set[str] = state["known_external_ids"]
        known_titles: dict[str, str] = state["known_titles"]
        has_membership_baseline: bool = state["has_membership_baseline"]
        modified_since: str | None = state["modified_since"]
        max_depth: int = state["max_depth"]
        max_files: int | None = state["max_files"]
        cohorts_filter: set[str] = state["cohorts_filter"]
        start_folder: str = state["start_folder"]
        page_warnings: list[str] = []
        page_errors: list[str] = []

        target_buffer = drain_offset + page_size + 1
        batch_cap = DRIVE_FOLDER_BATCH_SIZE if self._batch_folder_queries else 1
        folders_before = len(visited_folders)
        discovered_before = int(state["total_discovered"])

        while queue and not state["max_files_hit"] and (len(pending_items) < target_buffer):
            if max_files is not None and (state["total_discovered"] >= max_files):
                state["max_files_hit"] = True
                break

            batch: list[tuple[str, str, int]] = []
            while queue and len(batch) < batch_cap:
                fid, fpath, depth = queue.pop(0)
                if fid in visited_folders:
                    continue
                visited_folders.add(fid)
                verified_in_run.add(fid)
                if depth > max_depth:
                    state["depth_truncated"] = True
                    msg = f"Reached max recursion depth ({max_depth}) at folder {fpath}."
                    state["warnings"].append(msg)
                    page_warnings.append(msg)
                    continue
                batch.append((fid, fpath, depth))
            if not batch:
                continue

            children_by_fid: dict[str, list[dict[str, Any]]] = {}
            if len(batch) > 1:
                batch_ids = [b[0] for b in batch]
                try:
                    state["api_list_calls"] = state.get("api_list_calls", 0) + 1
                    children_by_fid = self._list_folders_batch(service, batch_ids)
                except AuthenticationRequiredError:
                    self._invalidate_service()
                    service = self._build_service(force_refresh=True)
                    state["api_list_calls"] = state.get("api_list_calls", 0) + 1
                    children_by_fid = self._list_folders_batch(service, batch_ids)
                except PermissionDeniedError:
                    raise
                except Exception:
                    # Fallback cleanly to single-folder queries so one problematic folder never fails the batch
                    children_by_fid = {}

            for fid, fpath, depth in batch:
                if max_files is not None and state["total_discovered"] >= max_files:
                    state["max_files_hit"] = True
                    break
                if fid in children_by_fid:
                    children = children_by_fid[fid]
                else:
                    try:
                        state["api_list_calls"] = state.get("api_list_calls", 0) + 1
                        children = self._list_folder_children(service, fid)
                    except AuthenticationRequiredError:
                        self._invalidate_service()
                        service = self._build_service(force_refresh=True)
                        state["api_list_calls"] = state.get("api_list_calls", 0) + 1
                        children = self._list_folder_children(service, fid)
                    except PermissionDeniedError:
                        raise
                    except Exception as exc:
                        err_msg = redact_sensitive_text(f"Error listing folder {fpath}: {exc}")
                        state["errors"].append(err_msg)
                        page_errors.append(err_msg)
                        continue

                for entry in children:
                    entry_id = entry["id"]
                    entry_name = entry.get("name", entry_id)
                    mime = entry.get("mimeType", "")
                    child_path = f"{fpath}/{entry_name}"

                    if mime == DRIVE_SHORTCUT_MIME:
                        state["shortcuts_skipped"] += 1
                        target_id = (entry.get("shortcutDetails") or {}).get("targetId")
                        if not target_id or not self._validate_target_folder_boundary(
                            target_id,
                            service,
                            verified_in_run=verified_in_run,
                            require_approved_cohort=True,
                        ):
                            state["excluded_shortcuts_skipped"] = (
                                int(state.get("excluded_shortcuts_skipped", 0)) + 1
                            )
                            msg = (
                                f"Skipped shortcut '{child_path}' pointing to excluded (Global) "
                                "or out-of-scope Drive target."
                            )
                            state["warnings"].append(msg)
                            page_warnings.append(msg)
                            continue
                        msg = f"Skipped in-tree shortcut '{child_path}' to prevent duplication."
                        state["warnings"].append(msg)
                        page_warnings.append(msg)
                        continue

                    if mime == DRIVE_FOLDER_MIME:
                        if depth == 0:
                            norm_top = entry_name.strip().lower()
                            if norm_top not in APPROVED_ACADEMIC_COHORT_KEYS:
                                continue
                            if cohorts_filter and norm_top not in cohorts_filter:
                                continue
                        verified_in_run.add(entry_id)
                        queue.append((entry_id, child_path, depth + 1))
                        continue

                    if mime in UNEXPORTABLE_WORKSPACE_MIMES:
                        state["unexportable_skipped"] += 1
                        msg = f"Skipped non-exportable Google Workspace item ({mime}) in approved Drive tree."
                        state["warnings"].append(msg)
                        page_warnings.append(msg)
                        continue

                    if not is_approved_drive_folder_path(fpath):
                        continue

                    mod_time = entry.get("modifiedTime")
                    if mod_time and (state["latest_modified"] is None or mod_time > state["latest_modified"]):
                        state["latest_modified"] = mod_time
                    ext_id_live = f"ensora_drive:{entry_id}"
                    seen_live_ids.add(ext_id_live)
                    is_membership_addition = has_membership_baseline and (
                        ext_id_live not in known_external_ids
                    )
                    top_cohort = extract_drive_top_level_cohort(fpath)
                    top_id_map: dict[str, str] = state.get("top_folder_id_by_cohort") or {}
                    top_folder_id = top_id_map.get(top_cohort or "")
                    if modified_since and mod_time and mod_time <= modified_since:
                        if is_membership_addition:
                            enriched = dict(entry)
                            enriched["_folder_path"] = fpath
                            if top_folder_id:
                                enriched["_top_level_folder_id"] = top_folder_id
                            if top_cohort:
                                enriched["_top_level_folder_name"] = top_cohort
                            membership_additions.append(self.normalize(enriched))
                        continue

                    enriched = dict(entry)
                    enriched["_folder_path"] = fpath
                    if top_folder_id:
                        enriched["_top_level_folder_id"] = top_folder_id
                    if top_cohort:
                        enriched["_top_level_folder_name"] = top_cohort
                    rec = self.normalize(enriched)
                    if (
                        not is_membership_addition
                        and rec.external_id in known_versions
                        and rec.remote_version is not None
                        and known_versions[rec.external_id] == rec.remote_version
                    ):
                        continue

                    pending_items.append(rec)
                    state["total_discovered"] += 1
                    if max_files is not None and state["total_discovered"] >= max_files:
                        state["max_files_hit"] = True
                        break

        traversal_finished = (not queue) or bool(state["max_files_hit"])
        authoritative_complete = False
        if traversal_finished:
            is_full_approved_cohorts = (not cohorts_filter) or (
                cohorts_filter == APPROVED_ACADEMIC_COHORT_KEYS
            )
            authoritative_complete = (
                not state["errors"]
                and not state["depth_truncated"]
                and not state["max_files_hit"]
                and max_files is None
                and is_full_approved_cohorts
                and start_folder == self._root_folder_id
            )
            if not state["reconciled"]:
                state["reconciled"] = True
                if has_membership_baseline and authoritative_complete:
                    if membership_additions:
                        pending_items.extend(membership_additions)
                        state["total_discovered"] += len(membership_additions)
                    known_folders: dict[str, str | None] = state.get("known_folders") or {}
                    for missing_id in sorted(known_external_ids - seen_live_ids):
                        prior_folder = known_folders.get(missing_id)
                        scope_excluded = bool(
                            prior_folder and is_excluded_drive_folder_path(prior_folder)
                        )
                        pending_items.append(
                            SourceRecord(
                                source_kind=SourceKind.ENSORA_DRIVE,
                                external_id=missing_id,
                                title=known_titles.get(missing_id, missing_id),
                                record_kind="document",
                                folder_path=prior_folder,
                                access_status=AccessStatus.TOMBSTONED,
                                provenance={
                                    "source": SourceKind.ENSORA_DRIVE.value,
                                    "root_folder_id": self._root_folder_id,
                                    "folder_path": prior_folder,
                                    "reconciled_tombstone": True,
                                    "excluded_by_scope_policy": scope_excluded,
                                    "scope_policy": DRIVE_SCOPE_POLICY_ID,
                                    "retrieved_at": utc_now_iso(),
                                },
                            )
                        )
                        state["total_discovered"] += 1

        if drain_offset > 0:
            del pending_items[:drain_offset]

        sliced = tuple(pending_items[:page_size])
        del pending_items[: len(sliced)]
        state["total_emitted"] += len(sliced)

        complete = traversal_finished and (len(pending_items) == 0)
        has_incomplete_or_error = bool(state["errors"]) or bool(state["depth_truncated"])
        durable_token = (
            f"drvmod:{state['latest_modified']}"
            if (state["latest_modified"] and not has_incomplete_or_error)
            else None
        )
        self.last_scan_authoritative = authoritative_complete if complete else False
        next_cursor = None if complete else self._store_bfs_paging_state(state)

        excluded_top_count = int(state.get("excluded_top_level_folders_count", 0))
        excluded_shortcuts = int(state.get("excluded_shortcuts_skipped", 0))
        self.last_stats = {
            "scope_policy": DRIVE_SCOPE_POLICY_ID,
            "authorized_top_level_folders": list(state.get("authorized_top_level_folders") or []),
            "excluded_top_level_folders_count": excluded_top_count,
            "folders_traversed": len(visited_folders),
            "page_folders_traversed": max(0, len(visited_folders) - folders_before),
            "folders_remaining": len(queue),
            "api_list_calls": int(state.get("api_list_calls", 0)),
            "resources_discovered": state["total_discovered"],
            "page_resources_discovered": max(0, int(state["total_discovered"]) - discovered_before),
            "resources_emitted": state["total_emitted"],
            "shortcuts_skipped": state["shortcuts_skipped"],
            "excluded_shortcuts_skipped": excluded_shortcuts,
            "unexportable_workspace_skipped": state["unexportable_skipped"],
            "resources_excluded": (
                excluded_top_count + excluded_shortcuts + int(state["unexportable_skipped"])
            ),
            "depth_truncated": state["depth_truncated"],
            "authoritative_complete": self.last_scan_authoritative,
            "fetch_errors_by_reason": dict(self.last_fetch_errors_by_reason),
        }

        return ChangePage(
            items=sliced,
            next_cursor=next_cursor,
            complete=complete,
            sync_token=durable_token if complete else None,
            warnings=tuple(page_warnings),
            errors=tuple(page_errors),
        )

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Recursively traverse ONLY the approved Ensora folder tree with streaming BFS pagination.

        Supported scope keys:
        - folder_id: optional subfolder ID (validated to be inside Ensora root!)
        - cohorts: optional list[str] filter (e.g. ["2A", "Global"])
        - modified_since: optional ISO UTC string for incremental sync
        - known_versions: dict[external_id, remote_version] to skip unchanged files
        - known_external_ids: set[str] or dict[external_id, title] of active cached Drive IDs
          for authoritative approved-root membership reconciliation
        - max_files: optional bounded limit for safe live validation
        - max_depth: recursion limit (depth truncation marks scan non-authoritative)
        - page_size: pagination chunk size (default 100)
        """
        opts = scope or {}
        if opts.get("personal_drive") is True:
            raise PermissionDeniedError("Personal Google Drive integration is explicitly prohibited.")

        start_folder = str(opts.get("folder_id") or self._root_folder_id)
        raw_cohorts = [str(c).strip().lower() for c in (opts.get("cohorts") or []) if str(c).strip()]
        disallowed_cohorts = [c for c in raw_cohorts if c not in APPROVED_ACADEMIC_COHORT_KEYS]
        if disallowed_cohorts:
            raise PermissionDeniedError(
                "Ensora Drive acquisition is restricted strictly to approved academic folders (1A, 2A, 3A); "
                f"excluded folder(s) requested: {', '.join(sorted(set(disallowed_cohorts)))}."
            )
        cohorts_filter = set(raw_cohorts)
        # Treat passing the full approved set ("1A", "2A", "3A") identically to unscoped default
        # so authoritative membership reconciliation and scope fingerprints stay consistent.
        if cohorts_filter == APPROVED_ACADEMIC_COHORT_KEYS:
            cohorts_filter = set()
        max_files: int | None = int(opts["max_files"]) if opts.get("max_files") is not None else None
        page_size = max(1, int(opts.get("page_size", 100)))
        scope_fingerprint = hashlib.sha256(
            f"{DRIVE_SCOPE_POLICY_ID}|{start_folder}|{','.join(sorted(cohorts_filter))}|{max_files}".encode("utf-8")
        ).hexdigest()[:12]

        # Serve continuation pages from in-memory OR durable 0o600 on-disk BFS snapshot.
        # Rejects legacy v1 snapshots from the broader scope (before Global exclusion) and
        # filters resumed queue/items defensively so excluded Global state can never leak.
        if cursor and cursor.startswith("drvpage:") and not cursor[len("drvpage:") :].isdigit():
            state = self._load_durable_paging_state(cursor)
            loaded_from_disk = state is not None
            if state is None:
                mem_state = self._paging_states.get(cursor)
                if mem_state is not None:
                    if mem_state.get("scope_policy") != DRIVE_SCOPE_POLICY_ID:
                        self._paging_states.pop(cursor, None)
                    else:
                        # Deep-copy mutable traversal containers so replaying a cursor is idempotent
                        state = {
                            **mem_state,
                            "queue": [
                                t for t in mem_state["queue"] if is_approved_drive_folder_path(t[1])
                            ],
                            "visited_folders": set(mem_state["visited_folders"]),
                            "verified_in_run": {
                                fid
                                for fid in mem_state["verified_in_run"]
                                if fid != self._root_folder_id
                            },
                            "pending_items": [
                                r
                                for r in mem_state["pending_items"]
                                if r.access_status == AccessStatus.TOMBSTONED
                                or is_approved_drive_folder_path(r.folder_path)
                            ],
                            "membership_additions": [
                                r
                                for r in mem_state["membership_additions"]
                                if is_approved_drive_folder_path(r.folder_path)
                            ],
                            "seen_live_ids": set(mem_state["seen_live_ids"]),
                            "warnings": list(mem_state["warnings"]),
                            "errors": list(mem_state["errors"]),
                        }
            if state is None:
                raise ValueError("Expired or unknown Ensora Drive paging handle.")
            if state["fingerprint"] != scope_fingerprint:
                raise ValueError("Ensora Drive paging cursor does not match the current query scope.")
            if loaded_from_disk:
                # Re-validate approved academic folder containment whenever resuming across a process boundary
                service = self._build_service()
                if state["start_folder"] != self._root_folder_id:
                    if not self._validate_target_folder_boundary(
                        state["start_folder"],
                        service,
                        verified_in_run=None,
                        require_approved_cohort=True,
                    ):
                        raise PermissionDeniedError(
                            f"Resumed folder '{state['start_folder']}' is outside approved Ensora academic folders (1A/2A/3A)."
                        )
                validated_queue: list[tuple[str, str, int]] = []
                for q_fid, q_path, q_depth in state["queue"]:
                    if self._validate_target_folder_boundary(
                        q_fid,
                        service,
                        verified_in_run=None,
                        require_approved_cohort=True,
                    ):
                        validated_queue.append((q_fid, q_path, q_depth))
                state["queue"] = validated_queue
            return self._advance_bfs_page(state, page_size=page_size)

        service = self._build_service()
        verified_in_run: set[str] = set()
        authorized_top_meta: list[dict[str, str]] = []
        top_folder_id_by_cohort: dict[str, str] = {}
        excluded_top_count = 0
        initial_api_list_calls = 0
        initial_visited: set[str] = set()

        if start_folder == self._root_folder_id:
            root_meta = self.get_root_folder_metadata()
            start_name = root_meta.name or "Ensora"
            auth_folders, excl_entries = self.resolve_authorized_top_level_folders(
                service, cohorts_filter=cohorts_filter
            )
            initial_api_list_calls = 1
            initial_visited.add(self._root_folder_id)
            excluded_top_count = len(excl_entries)
            initial_queue: list[tuple[str, str, int]] = []
            for entry in auth_folders:
                fid = str(entry["id"])
                raw_name = str(entry.get("name") or fid).strip()
                canonical_cohort = _COHORT_CANONICAL_BY_KEY.get(raw_name.lower(), raw_name)
                authorized_top_meta.append({"id": fid, "name": canonical_cohort})
                top_folder_id_by_cohort[canonical_cohort] = fid
                verified_in_run.add(fid)
                initial_queue.append((fid, f"{start_name}/{canonical_cohort}", 1))
        else:
            approved, cohort_name, top_fid, rel_segments = self._resolve_folder_ancestry(
                start_folder,
                service,
                verified_in_run=verified_in_run,
                require_approved_cohort=True,
            )
            if not approved:
                raise PermissionDeniedError(
                    f"Folder '{start_folder}' is excluded (Global) or outside approved Ensora academic folders (1A/2A/3A)."
                )
            if cohorts_filter and cohort_name and cohort_name.lower() not in cohorts_filter:
                raise PermissionDeniedError(
                    f"Folder '{start_folder}' belongs to cohort '{cohort_name}', outside requested cohort filter."
                )
            if cohort_name and top_fid:
                authorized_top_meta.append({"id": top_fid, "name": cohort_name})
                top_folder_id_by_cohort[cohort_name] = top_fid
            start_path = "/".join(["Ensora", *rel_segments]) if rel_segments else f"Ensora/{cohort_name or '1A'}"
            start_depth = max(1, len(rel_segments))
            initial_queue = [(start_folder, start_path, start_depth)]

        # Separately typed durable watermark (`drvmod:<iso_utc>`) vs legacy numeric offset
        cursor_modified_since: str | None = None
        offset = 0
        if cursor:
            if cursor.startswith("drvmod:"):
                cursor_modified_since = cursor[len("drvmod:") :]
            elif cursor.startswith("drvpage:") and cursor[len("drvpage:") :].isdigit():
                offset = int(cursor[len("drvpage:") :])
            elif cursor.isdigit():
                offset = int(cursor)
            elif "T" in cursor and cursor.endswith("Z"):
                cursor_modified_since = cursor

        modified_since: str | None = opts.get("modified_since") or cursor_modified_since
        known_versions: dict[str, str] = dict(opts.get("known_versions") or {})
        raw_known = opts.get("known_external_ids")
        has_membership_baseline = ("known_external_ids" in opts) and (raw_known is not None)
        known_titles: dict[str, str] = {}
        known_folders: dict[str, str | None] = {}
        if isinstance(raw_known, Mapping):
            for k, v in raw_known.items():
                sk = str(k)
                if isinstance(v, Mapping):
                    known_titles[sk] = str(v.get("title") or sk)
                    raw_f = v.get("folder")
                    known_folders[sk] = str(raw_f) if raw_f is not None else None
                else:
                    known_titles[sk] = str(v or sk)
            known_external_ids: set[str] = set(known_titles.keys())
        elif raw_known is not None:
            known_external_ids = set(raw_known)
        else:
            known_external_ids = set()
        max_depth = int(opts.get("max_depth", 12))

        state: dict[str, Any] = {
            "scope_policy": DRIVE_SCOPE_POLICY_ID,
            "fingerprint": scope_fingerprint,
            "start_folder": start_folder,
            "cohorts_filter": cohorts_filter,
            "max_files": max_files,
            "max_depth": max_depth,
            "modified_since": modified_since,
            "known_versions": known_versions,
            "has_membership_baseline": has_membership_baseline,
            "known_external_ids": known_external_ids,
            "known_titles": known_titles,
            "known_folders": known_folders,
            "authorized_top_level_folders": authorized_top_meta,
            "top_folder_id_by_cohort": top_folder_id_by_cohort,
            "excluded_top_level_folders_count": excluded_top_count,
            "queue": initial_queue,
            "visited_folders": initial_visited,
            "verified_in_run": verified_in_run,
            "pending_items": [],
            "membership_additions": [],
            "seen_live_ids": set(),
            "warnings": [],
            "errors": [],
            "depth_truncated": False,
            "max_files_hit": False,
            "reconciled": False,
            "latest_modified": modified_since,
            "total_discovered": 0,
            "total_emitted": 0,
            "shortcuts_skipped": 0,
            "excluded_shortcuts_skipped": 0,
            "unexportable_skipped": 0,
            "api_list_calls": initial_api_list_calls,
        }
        return self._advance_bfs_page(state, page_size=page_size, drain_offset=offset)

    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        """Download an approved Ensora Drive file or export a Google Workspace document."""
        file_id = str(item.payload.get("drive_file_id") or item.external_id.split(":", 1)[-1])

        def _record_failure(exc: Exception) -> None:
            reason = _categorize_drive_error_reason(exc)
            self.last_fetch_errors_by_reason[reason] = (
                self.last_fetch_errors_by_reason.get(reason, 0) + 1
            )
            if self.last_stats:
                self.last_stats["fetch_errors_by_reason"] = dict(self.last_fetch_errors_by_reason)

        try:
            service = self._build_service()

            # Re-verify file ancestry before download to guarantee boundary integrity
            def _fetch_meta(svc: Any) -> dict[str, Any]:
                def _meta_op():
                    self._rate_limiter.wait()
                    try:
                        return (
                            svc.files()
                            .get(
                                fileId=file_id,
                                fields="id,name,mimeType,parents,size,trashed",
                                supportsAllDrives=True,
                            )
                            .execute()
                        )
                    except Exception as exc:
                        raise _classify_http_error(exc) from exc

                return retry_with_backoff(_meta_op, max_retries=self._config.max_retries)

            try:
                meta = _fetch_meta(service)
            except AuthenticationRequiredError:
                # Transparently refresh expired OAuth access token once during long Drive runs
                self._invalidate_service()
                service = self._build_service(force_refresh=True)
                meta = _fetch_meta(service)

            if meta.get("trashed"):
                raise PermissionDeniedError(
                    f"Blocked download of trashed Drive file '{file_id}'."
                )
            parents = meta.get("parents") or []
            if not parents or not any(
                self._validate_target_folder_boundary(
                    p, service, verified_in_run=None, require_approved_cohort=True
                )
                for p in parents
            ):
                raise PermissionDeniedError(
                    f"Blocked download of Drive file '{file_id}' inside excluded (Global) or outside approved Ensora academic folders (1A/2A/3A)."
                )

            orig_mime = str(meta.get("mimeType") or item.mime_type or "application/octet-stream")
            if orig_mime in UNEXPORTABLE_WORKSPACE_MIMES:
                raise DriveUnexportableError(
                    f"Google Workspace item '{file_id}' ({orig_mime}) has no binary export format."
                )

            export_candidates = GOOGLE_WORKSPACE_EXPORT_FALLBACKS.get(orig_mime)
            if not export_candidates:
                size_raw = meta.get("size")
                if size_raw is not None and str(size_raw).isdigit():
                    declared_size = int(size_raw)
                    if declared_size > self._config.max_download_bytes:
                        raise DriveSizeLimitError(
                            f"Drive file exceeds configured size limit ({declared_size} > {self._config.max_download_bytes}).",
                            size_bytes=declared_size,
                            limit_bytes=self._config.max_download_bytes,
                        )

            def _execute_media(req: Any) -> bytes:
                if hasattr(req, "execute"):
                    data = req.execute()
                    if isinstance(data, bytes):
                        return data

                from googleapiclient.http import MediaIoBaseDownload  # type: ignore

                buf = io.BytesIO()
                downloader = MediaIoBaseDownload(buf, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
                    if buf.tell() > self._config.max_download_bytes:
                        raise DriveSizeLimitError(
                            f"Drive streamed download exceeded limit ({self._config.max_download_bytes} bytes).",
                            size_bytes=buf.tell(),
                            limit_bytes=self._config.max_download_bytes,
                        )
                return buf.getvalue()

            active_export_mime: str | None = export_candidates[0][0] if export_candidates else None
            active_export_ext: str | None = export_candidates[0][1] if export_candidates else None

            def _download_binary(svc: Any) -> bytes:
                def _op() -> bytes:
                    self._rate_limiter.wait()
                    try:
                        req = svc.files().get_media(fileId=file_id, supportsAllDrives=True)
                        return _execute_media(req)
                    except Exception as exc:
                        raise _classify_http_error(exc) from exc

                return retry_with_backoff(_op, max_retries=self._config.max_retries)

            def _download_workspace(svc: Any) -> bytes:
                nonlocal active_export_mime, active_export_ext
                assert export_candidates is not None
                last_export_exc: Exception | None = None
                for cand_mime, cand_ext in export_candidates:
                    active_export_mime = cand_mime
                    active_export_ext = cand_ext

                    def _op(emime: str = cand_mime) -> bytes:
                        self._rate_limiter.wait()
                        try:
                            req = svc.files().export_media(fileId=file_id, mimeType=emime)
                            return _execute_media(req)
                        except Exception as exc:
                            raise _classify_http_error(exc) from exc

                    try:
                        return retry_with_backoff(_op, max_retries=self._config.max_retries)
                    except (DriveExportSizeLimitError, DriveUnexportableError) as exc:
                        last_export_exc = exc
                        continue
                assert last_export_exc is not None
                raise last_export_exc

            try:
                content = _download_workspace(service) if export_candidates else _download_binary(service)
            except AuthenticationRequiredError:
                self._invalidate_service()
                service = self._build_service(force_refresh=True)
                content = _download_workspace(service) if export_candidates else _download_binary(service)

            if len(content) > self._config.max_download_bytes:
                raise DriveSizeLimitError(
                    f"Drive file exceeds configured size limit ({len(content)} > {self._config.max_download_bytes}).",
                    size_bytes=len(content),
                    limit_bytes=self._config.max_download_bytes,
                )

            filename = str(meta.get("name") or item.title)
            final_mime = active_export_mime if active_export_mime else orig_mime
            if active_export_ext and not filename.lower().endswith(active_export_ext):
                filename = f"{filename}{active_export_ext}"

            return FetchResult(
                external_id=item.external_id,
                content=content,
                content_hash=sha256_bytes(content),
                mime_type=final_mime,
                size_bytes=len(content),
                filename=filename,
                exported_from_mime=orig_mime if export_candidates else None,
                source_url=item.source_url,
            )
        except Exception as exc:
            _record_failure(exc)
            raise

    def disconnect(self) -> AuthStatus:
        return self._oauth.disconnect(revoke_remote=True)
