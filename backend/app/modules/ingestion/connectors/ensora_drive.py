"""Authorized read-only Ensora shared Google Drive connector.

Strictly scoped to the approved Ensora shared root folder:
    1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO

Expected organization: 1A / 2A / 3A / Global.
Personal Google Drive integration is explicitly excluded and blocked.
Shortcuts pointing outside the approved Ensora folder tree are never followed.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
from dataclasses import dataclass
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

EXPECTED_COHORT_FOLDERS: frozenset[str] = frozenset({"1A", "2A", "3A", "Global"})


@dataclass(frozen=True)
class DriveFolderMetadata:
    """Metadata of the validated Ensora root folder."""

    folder_id: str
    name: str
    mime_type: str
    modified_time: str | None = None
    web_view_link: str | None = None


def _classify_http_error(exc: Exception) -> Exception:
    """Translate Google API HttpError status codes into connector exception hierarchy."""
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if status == 401:
        return AuthenticationRequiredError("Google Drive API returned HTTP 401; reauthorization required.")
    if status == 403:
        msg = str(exc).lower()
        if "ratelimitexceeded" in msg or "userratelimitexceeded" in msg:
            return TransientSourceError("Google Drive API rate limit exceeded.", status_code=429)
        return PermissionDeniedError("Google Drive API returned HTTP 403 (access denied).")
    if status in (429, 500, 502, 503, 504):
        return TransientSourceError(f"Google Drive transient HTTP {status}.", status_code=status)
    return exc


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
    ) -> None:
        self._config = config
        self._root_folder_id = (
            config.ensora_drive_root_folder_id or DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
        ).strip()
        if not self._root_folder_id:
            raise ValueError("Ensora Drive root folder ID must be configured.")
        self._oauth = oauth_adapter or GoogleOAuthAdapter(config, source=SourceKind.ENSORA_DRIVE)
        self._service_factory = drive_service_factory
        self._rate_limiter = rate_limiter or RateLimiter(config.rate_limit_min_interval_sec)
        # Bounded process-local continuation states preserving fixed listing context across pages
        self._paging_states: dict[str, dict[str, Any]] = {}
        self.last_scan_authoritative: bool = False
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

    def _build_service(self) -> Any:
        creds = self._oauth.get_credentials(allow_refresh=True, interactive=False)
        if creds is None:
            raise AuthenticationRequiredError(
                "Google Drive OAuth token missing or expired; run Google authorization first."
            )
        if self._service_factory is not None:
            return self._service_factory(creds)
        from googleapiclient.discovery import build  # type: ignore

        return build("drive", "v3", credentials=creds, cache_discovery=False)

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

    def _validate_target_folder_boundary(
        self,
        folder_id: str,
        service: Any,
        *,
        verified_in_run: set[str] | None = None,
        max_depth: int = 20,
    ) -> bool:
        """Verify live ancestry proving folder_id is the Ensora root or a non-trashed descendant.

        Never trusts process-lifetime cached ancestry across calls so folders moved
        outside the approved Ensora root or trashed after discovery are rejected.
        """
        if folder_id == self._root_folder_id:
            return True
        if verified_in_run is not None and folder_id in verified_in_run:
            return True

        visited: set[str] = set()
        current = folder_id
        for _ in range(max_depth):
            if current in visited:
                return False
            visited.add(current)
            if current == self._root_folder_id:
                if verified_in_run is not None:
                    verified_in_run.update(visited)
                return True
            if verified_in_run is not None and current in verified_in_run:
                verified_in_run.update(visited)
                return True
            try:
                self._rate_limiter.wait()
                meta = (
                    service.files()
                    .get(fileId=current, fields="id,parents,trashed", supportsAllDrives=True)
                    .execute()
                )
            except Exception:
                return False
            if meta.get("trashed"):
                return False
            parents = meta.get("parents") or []
            if not parents:
                return False
            if self._root_folder_id in parents:
                if verified_in_run is not None:
                    verified_in_run.update(visited)
                return True
            current = parents[0]
        return False

    def _list_folder_children(
        self,
        service: Any,
        folder_id: str,
        *,
        page_size: int = 100,
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

    def normalize(self, raw_item: dict[str, Any]) -> SourceRecord:
        """Normalize a Drive file metadata dict + folder_path into a SourceRecord."""
        file_id = str(raw_item["id"])
        name = str(raw_item.get("name") or file_id)
        mime_type = str(raw_item.get("mimeType") or guess_mime_type(name))
        folder_path = str(raw_item.get("_folder_path") or "Ensora")
        parts = [p for p in folder_path.split("/") if p]

        # Extract cohort (1A/2A/3A/Global) and subject hint from Ensora hierarchy
        cohort_hint = next((p for p in parts if p in EXPECTED_COHORT_FOLDERS), None)
        subject_hint: str | None = None
        if cohort_hint and cohort_hint in parts:
            idx = parts.index(cohort_hint)
            if idx + 1 < len(parts):
                subject_hint = parts[idx + 1]

        version = raw_item.get("md5Checksum") or raw_item.get("version") or raw_item.get("modifiedTime")
        size_raw = raw_item.get("size")
        size_bytes = int(size_raw) if size_raw is not None and str(size_raw).isdigit() else None
        export_target = GOOGLE_WORKSPACE_EXPORTS.get(mime_type)

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
                "parents": raw_item.get("parents") or [],
            },
            provenance={
                "source": SourceKind.ENSORA_DRIVE.value,
                "root_folder_id": self._root_folder_id,
                "drive_file_id": file_id,
                "folder_path": folder_path,
                "retrieved_at": utc_now_iso(),
            },
        )

    def _store_paging_state(
        self,
        *,
        records: tuple[SourceRecord, ...],
        offset: int,
        durable_token: str | None,
        fingerprint: str,
        has_errors: bool,
        authoritative: bool,
    ) -> str:
        """Freeze listing context across continuation pages under a compact `drvpage:<16-hex>` handle."""
        seed = json.dumps(
            {
                "o": offset,
                "d": durable_token,
                "fp": fingerprint,
                "n": len(records),
                "first": records[offset].external_id if offset < len(records) else "",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
        handle = f"drvpage:{key}"
        if len(self._paging_states) >= 64:
            oldest = next(iter(self._paging_states))
            self._paging_states.pop(oldest, None)
        self._paging_states[handle] = {
            "records": records,
            "offset": offset,
            "durable_token": durable_token,
            "fingerprint": fingerprint,
            "has_errors": has_errors,
            "authoritative": authoritative,
        }
        return handle

    def list_changes(
        self,
        scope: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> ChangePage:
        """Recursively traverse ONLY the approved Ensora folder tree with incremental support.

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
        cohorts_filter = {c.lower() for c in (opts.get("cohorts") or [])}
        max_files: int | None = int(opts["max_files"]) if opts.get("max_files") is not None else None
        page_size = max(1, int(opts.get("page_size", 100)))
        scope_fingerprint = hashlib.sha256(
            f"{start_folder}|{','.join(sorted(cohorts_filter))}|{max_files}".encode("utf-8")
        ).hexdigest()[:12]

        # Serve continuation pages from frozen run snapshot so watermark/filters never drift mid-run
        if cursor and cursor.startswith("drvpage:") and cursor in self._paging_states:
            state = self._paging_states.pop(cursor)
            if state["fingerprint"] != scope_fingerprint:
                raise ValueError("Ensora Drive paging cursor does not match the current query scope.")
            all_records: tuple[SourceRecord, ...] = state["records"]
            cur_offset = int(state["offset"])
            sliced = all_records[cur_offset : cur_offset + page_size]
            next_offset = cur_offset + len(sliced)
            complete = next_offset >= len(all_records)
            self.last_scan_authoritative = bool(state["authoritative"])
            next_cursor = (
                None
                if complete
                else self._store_paging_state(
                    records=all_records,
                    offset=next_offset,
                    durable_token=state["durable_token"],
                    fingerprint=scope_fingerprint,
                    has_errors=bool(state["has_errors"]),
                    authoritative=bool(state["authoritative"]),
                )
            )
            durable_out = None if state["has_errors"] else state["durable_token"]
            return ChangePage(
                items=sliced,
                next_cursor=next_cursor,
                complete=complete,
                sync_token=durable_out if complete else None,
            )

        if cursor and cursor.startswith("drvpage:") and not cursor[len("drvpage:") :].isdigit():
            raise ValueError("Expired or unknown Ensora Drive paging handle.")

        service = self._build_service()
        verified_in_run: set[str] = {self._root_folder_id}
        if not self._validate_target_folder_boundary(
            start_folder, service, verified_in_run=verified_in_run
        ):
            raise PermissionDeniedError(
                f"Folder '{start_folder}' is outside the approved Ensora root folder tree ({self._root_folder_id})."
            )

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
        if isinstance(raw_known, Mapping):
            known_titles = {str(k): str(v or k) for k, v in raw_known.items()}
            known_external_ids: set[str] = set(known_titles.keys())
        elif raw_known is not None:
            known_external_ids = set(raw_known)
        else:
            known_external_ids = set()
        max_depth = int(opts.get("max_depth", 12))

        root_meta = self.get_root_folder_metadata() if start_folder == self._root_folder_id else None
        start_name = root_meta.name if root_meta else "Ensora"

        queue: list[tuple[str, str, int]] = [(start_folder, start_name, 0)]
        visited_folders: set[str] = set()
        discovered: list[SourceRecord] = []
        membership_additions: list[SourceRecord] = []
        seen_live_ids: set[str] = set()
        warnings: list[str] = []
        errors: list[str] = []
        depth_truncated = False
        max_files_hit = False
        latest_modified: str | None = modified_since

        while queue:
            if max_files is not None and len(discovered) >= max_files:
                max_files_hit = True
                break
            fid, fpath, depth = queue.pop(0)
            if fid in visited_folders:
                continue
            visited_folders.add(fid)
            verified_in_run.add(fid)

            if depth > max_depth:
                depth_truncated = True
                warnings.append(f"Reached max recursion depth ({max_depth}) at folder {fpath}.")
                continue

            try:
                children = self._list_folder_children(service, fid)
            except (AuthenticationRequiredError, PermissionDeniedError):
                raise
            except Exception as exc:
                errors.append(redact_sensitive_text(f"Error listing folder {fpath}: {exc}"))
                continue

            for entry in children:
                entry_id = entry["id"]
                entry_name = entry.get("name", entry_id)
                mime = entry.get("mimeType", "")
                child_path = f"{fpath}/{entry_name}"

                if mime == DRIVE_SHORTCUT_MIME:
                    target_id = (entry.get("shortcutDetails") or {}).get("targetId")
                    if not target_id or not self._validate_target_folder_boundary(
                        target_id, service, verified_in_run=verified_in_run
                    ):
                        warnings.append(
                            f"Skipped shortcut '{child_path}' pointing outside the approved Ensora folder tree."
                        )
                        continue
                    warnings.append(f"Skipped in-tree shortcut '{child_path}' to prevent duplication.")
                    continue

                if mime == DRIVE_FOLDER_MIME:
                    if depth == 0 and cohorts_filter and entry_name.lower() not in cohorts_filter:
                        continue
                    verified_in_run.add(entry_id)
                    queue.append((entry_id, child_path, depth + 1))
                    continue

                if mime in UNEXPORTABLE_WORKSPACE_MIMES:
                    warnings.append(
                        f"Skipped non-exportable Google Workspace item ({mime}) in approved Drive tree."
                    )
                    continue

                mod_time = entry.get("modifiedTime")
                if mod_time and (latest_modified is None or mod_time > latest_modified):
                    latest_modified = mod_time
                ext_id_live = f"ensora_drive:{entry_id}"
                seen_live_ids.add(ext_id_live)
                is_membership_addition = has_membership_baseline and (
                    ext_id_live not in known_external_ids
                )
                if modified_since and mod_time and mod_time <= modified_since:
                    # Treat IDs absent from a loaded active membership baseline (e.g. returned
                    # previously-tombstoned files or newly moved-in old files) as candidate
                    # membership additions; emit them only if the scan completes authoritatively.
                    if is_membership_addition:
                        enriched = dict(entry)
                        enriched["_folder_path"] = fpath
                        membership_additions.append(self.normalize(enriched))
                    continue

                enriched = dict(entry)
                enriched["_folder_path"] = fpath
                rec = self.normalize(enriched)
                if (
                    not is_membership_addition
                    and rec.external_id in known_versions
                    and rec.remote_version is not None
                    and known_versions[rec.external_id] == rec.remote_version
                ):
                    continue

                discovered.append(rec)
                if max_files is not None and len(discovered) >= max_files:
                    max_files_hit = True
                    break

        # Reconcile membership additions (returning/newly-moved-in old-timestamp files) and
        # deletions/move-outs ONLY after an authoritative, complete, non-truncated, zero-error
        # approved-root scan (depth truncation or partial errors MUST disable reconciliation).
        authoritative_complete = (
            not errors
            and not depth_truncated
            and not max_files_hit
            and max_files is None
            and not cohorts_filter
            and start_folder == self._root_folder_id
        )
        self.last_scan_authoritative = authoritative_complete

        if has_membership_baseline and authoritative_complete:
            if membership_additions:
                discovered.extend(membership_additions)
            for missing_id in sorted(known_external_ids - seen_live_ids):
                discovered.append(
                    SourceRecord(
                        source_kind=SourceKind.ENSORA_DRIVE,
                        external_id=missing_id,
                        title=known_titles.get(missing_id, missing_id),
                        record_kind="document",
                        access_status=AccessStatus.TOMBSTONED,
                        provenance={
                            "source": SourceKind.ENSORA_DRIVE.value,
                            "root_folder_id": self._root_folder_id,
                            "reconciled_tombstone": True,
                            "retrieved_at": utc_now_iso(),
                        },
                    )
                )

        all_records_tuple = tuple(discovered)
        sliced = all_records_tuple[offset : offset + page_size]
        next_offset = offset + len(sliced)
        complete = next_offset >= len(all_records_tuple)
        has_incomplete_or_error = bool(errors) or depth_truncated
        durable_token = (
            f"drvmod:{latest_modified}"
            if (latest_modified and not has_incomplete_or_error)
            else None
        )
        next_cursor = (
            None
            if complete
            else self._store_paging_state(
                records=all_records_tuple,
                offset=next_offset,
                durable_token=durable_token,
                fingerprint=scope_fingerprint,
                has_errors=has_incomplete_or_error,
                authoritative=authoritative_complete,
            )
        )

        self.last_stats = {
            "folders_traversed": len(visited_folders),
            "resources_discovered": len(discovered),
            "depth_truncated": depth_truncated,
            "authoritative_complete": authoritative_complete,
        }

        return ChangePage(
            items=sliced,
            next_cursor=next_cursor,
            complete=complete,
            sync_token=durable_token if complete else None,
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def fetch_blob(self, item: SourceRecord) -> FetchResult:
        """Download an approved Ensora Drive file or export a Google Workspace document."""
        file_id = str(item.payload.get("drive_file_id") or item.external_id.split(":", 1)[-1])
        service = self._build_service()

        # Re-verify file ancestry before download to guarantee boundary integrity
        def _meta_op():
            self._rate_limiter.wait()
            try:
                return (
                    service.files()
                    .get(
                        fileId=file_id,
                        fields="id,name,mimeType,parents,size,trashed",
                        supportsAllDrives=True,
                    )
                    .execute()
                )
            except Exception as exc:
                raise _classify_http_error(exc) from exc

        meta = retry_with_backoff(_meta_op, max_retries=self._config.max_retries)
        if meta.get("trashed"):
            raise PermissionDeniedError(
                f"Blocked download of trashed Drive file '{file_id}'."
            )
        parents = meta.get("parents") or []
        if not parents or not any(
            self._validate_target_folder_boundary(p, service, verified_in_run=None)
            for p in parents
        ):
            raise PermissionDeniedError(
                f"Blocked download of Drive file '{file_id}' outside approved Ensora root tree."
            )

        orig_mime = str(meta.get("mimeType") or item.mime_type or "application/octet-stream")
        export_spec = GOOGLE_WORKSPACE_EXPORTS.get(orig_mime)
        active_export_mime = export_spec[0] if export_spec else None
        active_export_ext = export_spec[1] if export_spec else None

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
            return buf.getvalue()

        def _download_op() -> bytes:
            nonlocal active_export_mime, active_export_ext
            self._rate_limiter.wait()
            try:
                if active_export_mime is not None:
                    req = service.files().export_media(fileId=file_id, mimeType=active_export_mime)
                else:
                    req = service.files().get_media(fileId=file_id, supportsAllDrives=True)
                return _execute_media(req)
            except Exception as exc:
                # Fallback to text/plain when Google Workspace PDF/PPTX export exceeds Google's 10MB export cap
                if (
                    orig_mime == "application/vnd.google-apps.document"
                    and active_export_mime != "text/plain"
                    and "exportsizelimitexceeded" in str(exc).lower()
                ):
                    active_export_mime = "text/plain"
                    active_export_ext = ".txt"
                    req_fb = service.files().export_media(fileId=file_id, mimeType="text/plain")
                    return _execute_media(req_fb)
                raise _classify_http_error(exc) from exc

        content = retry_with_backoff(_download_op, max_retries=self._config.max_retries)
        if len(content) > self._config.max_download_bytes:
            raise ValueError(
                f"Drive file exceeds configured size limit ({len(content)} > {self._config.max_download_bytes})."
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
            exported_from_mime=orig_mime if export_spec else None,
            source_url=item.source_url,
        )

    def disconnect(self) -> AuthStatus:
        return self._oauth.disconnect(revoke_remote=True)
