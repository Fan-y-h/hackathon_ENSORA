"""Google OAuth 2.0 installed-application authentication adapter.

Strictly read-only least-privilege scopes:
- https://www.googleapis.com/auth/calendar.readonly
- https://www.googleapis.com/auth/drive.readonly

Never asks for, collects, or stores the user's Google password.
Integrates cleanly with Codex's AuthenticationManager via AuthenticationProviderProtocol.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Sequence

import requests

from ..config import (
    GOOGLE_SCOPE_CALENDAR_READONLY,
    GOOGLE_SCOPE_DRIVE_READONLY,
    SourcesRuntimeConfig,
)
from ..contracts import AuthState, AuthStatus, SourceKind
from ..credentials import redact_sensitive_text
from .google_cloud import validate_google_credentials_file

logger = logging.getLogger(__name__)

GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
ALLOWED_GOOGLE_READONLY_SCOPES: frozenset[str] = frozenset(
    {GOOGLE_SCOPE_CALENDAR_READONLY, GOOGLE_SCOPE_DRIVE_READONLY}
)


def scopes_for_source(source: SourceKind) -> tuple[str, ...]:
    """Return the exact least-privilege read-only Google scope for a single source."""
    if source == SourceKind.ENSORA_DRIVE:
        return (GOOGLE_SCOPE_DRIVE_READONLY,)
    if source == SourceKind.GOOGLE_CALENDAR:
        return (GOOGLE_SCOPE_CALENDAR_READONLY,)
    raise ValueError(f"Source '{source.value}' does not use Google OAuth.")


class GoogleOAuthAdapter:
    """Manages local OAuth 2.0 tokens, refresh, consent verification, and revocation."""

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        source: SourceKind = SourceKind.ENSORA_DRIVE,
        scopes: Sequence[str] | None = None,
        token_path: Path | None = None,
        credentials_loader: Callable[[Path, list[str]], Any] | None = None,
        flow_runner: Callable[[Path, list[str], int, bool], Any] | None = None,
        request_factory: Callable[[], Any] | None = None,
        http_post: Callable[..., Any] | None = None,
    ) -> None:
        self.source = source
        self._config = config
        requested = tuple(scopes) if scopes is not None else self._resolve_default_scopes(source)
        self._validate_readonly_scopes(requested)
        self._requested_scopes: tuple[str, ...] = requested
        self._credentials_path = config.google_credentials_path
        self._token_path = token_path or config.google_token_path_for(
            source.value if config.google_separate_source_tokens else None
        )
        self._credentials_loader = credentials_loader
        self._flow_runner = flow_runner
        self._request_factory = request_factory
        self._http_post = http_post or requests.post

    def _resolve_default_scopes(self, source: SourceKind) -> tuple[str, ...]:
        if self._config.google_separate_source_tokens:
            return scopes_for_source(source)
        enabled = self._config.enabled_google_scopes
        required = scopes_for_source(source)
        merged: list[str] = list(enabled)
        for s in required:
            if s not in merged:
                merged.append(s)
        return tuple(merged)

    @staticmethod
    def _validate_readonly_scopes(scopes: Sequence[str]) -> None:
        if not scopes:
            raise ValueError("At least one read-only Google OAuth scope must be requested.")
        for scope in scopes:
            if scope not in ALLOWED_GOOGLE_READONLY_SCOPES:
                raise ValueError(
                    f"Disallowed Google OAuth scope '{scope}'. Only read-only Calendar and Drive scopes are permitted."
                )

    @property
    def requested_scopes(self) -> tuple[str, ...]:
        return self._requested_scopes

    @property
    def token_path(self) -> Path:
        return self._token_path

    def _load_stored_credentials(self) -> Any | None:
        if not self._token_path.is_file():
            return None
        if self._credentials_loader is not None:
            return self._credentials_loader(self._token_path, list(self._requested_scopes))

        from google.oauth2.credentials import Credentials  # type: ignore

        raw_data = json.loads(self._token_path.read_text(encoding="utf-8"))
        # Only trust explicitly persisted verified grants — NEVER treat requested/legacy `scopes` as verified grants!
        verified_grants = raw_data.get("verified_granted_scopes") or raw_data.get("granted_scopes")
        creds = Credentials.from_authorized_user_info(raw_data)
        try:
            creds._verified_granted_scopes = (
                tuple(sorted(set(verified_grants))) if isinstance(verified_grants, list) and verified_grants else ()
            )
            if isinstance(verified_grants, list) and verified_grants:
                creds._granted_scopes = list(verified_grants)
        except Exception:
            pass
        return creds

    def _save_credentials(self, creds: Any) -> None:
        self._config.ensure_private_directories()
        self._token_path.parent.mkdir(parents=True, exist_ok=True)
        raw_serialized = creds.to_json() if hasattr(creds, "to_json") else json.dumps(creds)
        try:
            payload = json.loads(raw_serialized)
            if isinstance(payload, dict):
                verified = list(self._granted_scopes(creds))
                if verified:
                    payload["verified_granted_scopes"] = verified
                    payload["granted_scopes"] = verified
                    payload["scopes"] = verified
                else:
                    payload.pop("verified_granted_scopes", None)
                    payload.pop("granted_scopes", None)
                raw_serialized = json.dumps(payload, indent=2)
        except Exception:
            pass

        # Create exclusive owner-only (0o600) unique temp file BEFORE writing secret bytes
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._token_path.parent),
            prefix=f".{self._token_path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(raw_serialized)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(str(tmp_path), str(self._token_path))
            try:
                os.chmod(self._token_path, 0o600)
            except OSError:
                pass
        finally:
            tmp_path.unlink(missing_ok=True)

    def _make_auth_request(self) -> Any:
        if self._request_factory is not None:
            return self._request_factory()
        from google.auth.transport.requests import Request  # type: ignore

        return Request()

    @staticmethod
    def _granted_scopes(creds: Any) -> tuple[str, ...]:
        """Return strictly verified granted scopes ONLY, preferring fresh provider grants.

        Priority:
        1. Explicit provider-reported `creds.granted_scopes` (even if narrowed or empty
           after a token refresh) so a stale persisted marker can never override a newer
           narrowed grant.
        2. Persisted `_verified_granted_scopes` marker when `creds.granted_scopes` is
           unset (`None`).
        Never falls back to `creds.scopes` (which may reflect requested scopes rather
        than provider-confirmed grants).
        """
        granted = getattr(creds, "granted_scopes", None)
        if granted is not None:
            return tuple(sorted(set(granted)))
        verified = getattr(creds, "_verified_granted_scopes", None)
        if verified is not None:
            return tuple(sorted(set(verified)))
        return ()

    @staticmethod
    def _sync_verified_grant_marker(creds: Any) -> tuple[str, ...]:
        """Synchronize `_verified_granted_scopes` with fresh provider-reported grants after refresh."""
        granted = getattr(creds, "granted_scopes", None)
        if granted is not None:
            normalized = tuple(sorted(set(granted)))
            try:
                creds._verified_granted_scopes = normalized
            except Exception:
                pass
            return normalized
        return GoogleOAuthAdapter._granted_scopes(creds)

    def has_required_scopes(self, creds: Any, required_scopes: Sequence[str] | None = None) -> bool:
        granted = set(self._granted_scopes(creds))
        needed = set(required_scopes or scopes_for_source(self.source))
        return needed.issubset(granted)

    def get_credentials(
        self,
        *,
        allow_refresh: bool = True,
        interactive: bool = False,
        open_browser: bool = True,
        port: int = 0,
    ) -> Any | None:
        """Load valid credentials, refreshing expired access tokens automatically."""
        creds = self._load_stored_credentials()
        if creds is not None:
            if getattr(creds, "valid", False) and self.has_required_scopes(creds):
                return creds
            if (
                allow_refresh
                and getattr(creds, "expired", False)
                and getattr(creds, "refresh_token", None)
            ):
                try:
                    creds.refresh(self._make_auth_request())
                    self._sync_verified_grant_marker(creds)
                    if self.has_required_scopes(creds):
                        self._save_credentials(creds)
                        return creds
                    # Persist narrowed grant state so stale persisted scopes are cleared from disk
                    self._save_credentials(creds)
                    logger.warning(
                        "Google OAuth refresh returned narrowed scopes for %s; reauthorization required.",
                        self.source.value,
                    )
                except Exception as exc:
                    logger.warning(
                        "Google OAuth refresh failed (%s); reauthorization required.",
                        type(exc).__name__,
                    )
                    creds = None

        if not interactive:
            return None

        return self._run_interactive_oauth(open_browser=open_browser, port=port)

    def _run_interactive_oauth(self, *, open_browser: bool = True, port: int = 0) -> Any:
        val = validate_google_credentials_file(self._credentials_path)
        if not val.valid:
            raise FileNotFoundError(
                "Invalid or missing Google OAuth client credentials file: "
                + "; ".join(val.errors)
            )

        if self._flow_runner is not None:
            creds = self._flow_runner(
                self._credentials_path,
                list(self._requested_scopes),
                port,
                open_browser,
            )
        else:
            from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore

            flow = InstalledAppFlow.from_client_secrets_file(
                str(self._credentials_path),
                scopes=list(self._requested_scopes),
            )
            creds = flow.run_local_server(
                host="127.0.0.1",
                port=port,
                open_browser=open_browser,
                prompt="select_account consent",
                access_type="offline",
                include_granted_scopes="true",
            )

        if not self.has_required_scopes(creds):
            granted = self._granted_scopes(creds)
            missing = sorted(set(scopes_for_source(self.source)) - set(granted))
            raise PermissionError(
                f"User did not grant required read-only scope(s): {', '.join(missing)}"
            )
        self._save_credentials(creds)
        return creds

    def status(self) -> AuthStatus:
        """Report current OAuth state without triggering interactive login."""
        creds_val = validate_google_credentials_file(self._credentials_path)
        if not creds_val.valid:
            return AuthStatus(
                source=self.source,
                state=AuthState.UNCONFIGURED,
                authenticated=False,
                detail="Missing or invalid Google OAuth client file.",
                scopes=self._requested_scopes,
                requires_interactive_action=True,
                next_action=f"Place Desktop OAuth client JSON at {self._credentials_path}",
                metadata={"credentials_validation": creds_val.to_dict()},
            )

        try:
            creds = self._load_stored_credentials()
        except Exception as exc:
            return AuthStatus(
                source=self.source,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"Failed to read Google token file: {exc}"),
                requires_interactive_action=True,
                next_action="Run Google OAuth reauthorization.",
            )

        if creds is None:
            return AuthStatus(
                source=self.source,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                detail="Google OAuth client configured; user authorization not yet completed.",
                scopes=self._requested_scopes,
                requires_interactive_action=True,
                next_action="Authorize Google read-only access in browser.",
            )

        if not self.has_required_scopes(creds):
            return AuthStatus(
                source=self.source,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                detail="Stored Google token does not include all required read-only scopes.",
                scopes=self._granted_scopes(creds),
                requires_interactive_action=True,
                next_action="Reauthorize with required read-only scope(s).",
            )

        if getattr(creds, "valid", False):
            return AuthStatus(
                source=self.source,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail="Valid Google OAuth token loaded.",
                scopes=self._granted_scopes(creds),
                requires_interactive_action=False,
            )

        if getattr(creds, "expired", False) and getattr(creds, "refresh_token", None):
            try:
                creds.refresh(self._make_auth_request())
                self._sync_verified_grant_marker(creds)
                self._save_credentials(creds)
                if not self.has_required_scopes(creds):
                    return AuthStatus(
                        source=self.source,
                        state=AuthState.NEEDS_LOGIN,
                        authenticated=False,
                        detail="Refreshed Google token no longer includes all required read-only scopes.",
                        scopes=self._granted_scopes(creds),
                        requires_interactive_action=True,
                        next_action="Reauthorize with required read-only scope(s).",
                    )
                return AuthStatus(
                    source=self.source,
                    state=AuthState.AUTHENTICATED,
                    authenticated=True,
                    detail="Refreshed expired Google access token successfully.",
                    scopes=self._granted_scopes(creds),
                    requires_interactive_action=False,
                )
            except Exception as exc:
                return AuthStatus(
                    source=self.source,
                    state=AuthState.EXPIRED,
                    authenticated=False,
                    detail=redact_sensitive_text(
                        f"Google refresh token expired or revoked ({type(exc).__name__})."
                    ),
                    scopes=self._granted_scopes(creds),
                    requires_interactive_action=True,
                    next_action="Run Google OAuth reauthorization.",
                )

        return AuthStatus(
            source=self.source,
            state=AuthState.EXPIRED,
            authenticated=False,
            detail="Google token expired and no refresh token is available.",
            scopes=self._granted_scopes(creds),
            requires_interactive_action=True,
            next_action="Run Google OAuth reauthorization.",
        )

    def authenticate(self, *, interactive: bool = True, open_browser: bool = True) -> AuthStatus:
        """Authorize or refresh Google OAuth credentials."""
        try:
            creds = self.get_credentials(
                allow_refresh=True,
                interactive=interactive,
                open_browser=open_browser,
            )
            if creds is None:
                return self.status()
            return AuthStatus(
                source=self.source,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail="Google OAuth read-only authorization succeeded.",
                scopes=self._granted_scopes(creds),
                requires_interactive_action=False,
            )
        except PermissionError as exc:
            return AuthStatus(
                source=self.source,
                state=AuthState.REVOKED,
                authenticated=False,
                detail=str(exc),
                requires_interactive_action=True,
                next_action="Grant requested read-only scope(s) in Google consent screen.",
            )
        except Exception as exc:
            return AuthStatus(
                source=self.source,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"Google OAuth authorization failed: {exc}"),
                requires_interactive_action=True,
            )

    def reauthorize(self, *, open_browser: bool = True) -> AuthStatus:
        """Force fresh OAuth consent (e.g., after token expiration or scope change)."""
        try:
            creds = self._run_interactive_oauth(open_browser=open_browser)
            return AuthStatus(
                source=self.source,
                state=AuthState.AUTHENTICATED,
                authenticated=True,
                detail="Google OAuth reauthorization succeeded.",
                scopes=self._granted_scopes(creds),
                requires_interactive_action=False,
            )
        except Exception as exc:
            return AuthStatus(
                source=self.source,
                state=AuthState.ERROR,
                authenticated=False,
                detail=redact_sensitive_text(f"Google OAuth reauthorization failed: {exc}"),
                requires_interactive_action=True,
            )

    def validate_connection(self) -> AuthStatus:
        """Validate token usability and granted scopes."""
        return self.status()

    def disconnect(self, *, revoke_remote: bool = True) -> AuthStatus:
        """Revoke token remotely when requested and remove local persisted token file."""
        revoked_remote = False
        creds = None
        try:
            creds = self._load_stored_credentials()
        except Exception:
            creds = None

        if revoke_remote and creds is not None:
            token_to_revoke = getattr(creds, "refresh_token", None) or getattr(creds, "token", None)
            if token_to_revoke:
                try:
                    resp = self._http_post(
                        GOOGLE_REVOKE_ENDPOINT,
                        data={"token": token_to_revoke},
                        headers={"content-type": "application/x-www-form-urlencoded"},
                        timeout=self._config.request_timeout_sec,
                    )
                    revoked_remote = getattr(resp, "status_code", 0) == 200
                except Exception:
                    revoked_remote = False

        if self._token_path.exists():
            self._token_path.unlink(missing_ok=True)

        return AuthStatus(
            source=self.source,
            state=AuthState.REVOKED,
            authenticated=False,
            detail=(
                "Revoked Google OAuth token remotely and removed local token."
                if revoked_remote
                else "Removed local Google OAuth token."
            ),
            metadata={"revoked_remote": revoked_remote},
        )
