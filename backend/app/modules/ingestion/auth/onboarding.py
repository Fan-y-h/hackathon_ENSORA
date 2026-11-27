"""Unified single-workflow setup and onboarding orchestrator for P1 sources.

Detects all already-completed configuration steps before requesting any
additional user input, auto-generates missing non-secret defaults in
~/.config/ensora-plus/sources.env, and coordinates ENSAI + Google OAuth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import SourcesRuntimeConfig
from ..contracts import AuthStatus, SourceKind
from ..credentials import SecureCredentialManager
from .ensai import EnsaiAuthAdapter, PlaywrightEnvironmentStatus
from .google_cloud import GoogleCloudProvisioningReport, GoogleCloudSetupHelper
from .google_oauth import GoogleOAuthAdapter


@dataclass(frozen=True)
class OnboardingReport:
    """End-to-end onboarding state and remaining human actions, if any."""

    config_summary: dict[str, Any]
    added_env_keys: tuple[str, ...]
    playwright_status: PlaywrightEnvironmentStatus
    google_cloud_report: GoogleCloudProvisioningReport
    source_statuses: dict[str, AuthStatus]
    completed_steps: tuple[str, ...]
    remaining_actions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def all_enabled_ready(self) -> bool:
        return all(st.authenticated for st in self.source_statuses.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_summary": self.config_summary,
            "added_env_keys": list(self.added_env_keys),
            "playwright_status": self.playwright_status.to_dict(),
            "google_cloud_report": self.google_cloud_report.to_dict(),
            "source_statuses": {k: v.to_dict() for k, v in self.source_statuses.items()},
            "completed_steps": list(self.completed_steps),
            "remaining_actions": list(self.remaining_actions),
            "all_enabled_ready": self.all_enabled_ready,
        }


class OnboardingOrchestrator:
    """Single-entry onboarding coordinator minimizing human intervention."""

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        credential_manager: SecureCredentialManager | None = None,
        ensai_auth: EnsaiAuthAdapter | None = None,
        google_cloud: GoogleCloudSetupHelper | None = None,
        drive_oauth: GoogleOAuthAdapter | None = None,
        calendar_oauth: GoogleOAuthAdapter | None = None,
    ) -> None:
        self._config = config
        self._credentials = credential_manager or SecureCredentialManager()
        self._ensai_auth = ensai_auth or EnsaiAuthAdapter(config, self._credentials)
        self._google_cloud = google_cloud or GoogleCloudSetupHelper(config)
        self._drive_oauth = drive_oauth or GoogleOAuthAdapter(
            config, source=SourceKind.ENSORA_DRIVE
        )
        self._calendar_oauth = calendar_oauth or GoogleOAuthAdapter(
            config, source=SourceKind.GOOGLE_CALENDAR
        )

    def _local_only_source_status(self, source: SourceKind) -> AuthStatus:
        """Inspect local setup files strictly offline without browser launches or token refreshes."""
        from ..contracts import AuthState

        if source in (SourceKind.MOODLE, SourceKind.PAMPLEMOUSSE):
            pdir = self._ensai_auth.profile_dir_for(source)
            has_profile = pdir.is_dir() and any(
                c.name not in (".ensora.lock", ".ensora_profile.lock") for c in pdir.iterdir()
            )
            return AuthStatus(
                source=source,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                detail=(
                    "Local ENSAI browser profile present (run consent-gated validate to verify live session)."
                    if has_profile
                    else "No local ENSAI browser profile found yet."
                ),
                requires_interactive_action=not has_profile,
                next_action=(
                    "Validate session after granting read consent."
                    if has_profile
                    else f"Run ENSAI authentication for {source.value}."
                ),
                metadata={"local_only": True, "profile_present": has_profile},
            )

        oauth = self._drive_oauth if source == SourceKind.ENSORA_DRIVE else self._calendar_oauth
        has_creds = self._config.google_credentials_path.is_file()
        has_token = oauth.token_path.is_file()
        if not has_creds:
            return AuthStatus(
                source=source,
                state=AuthState.UNCONFIGURED,
                authenticated=False,
                detail="Missing local Google OAuth client file (local-only check).",
                requires_interactive_action=True,
                next_action=f"Place Desktop OAuth client JSON at {self._config.google_credentials_path}",
                metadata={"local_only": True},
            )
        if not has_token:
            return AuthStatus(
                source=source,
                state=AuthState.NEEDS_LOGIN,
                authenticated=False,
                detail="Google OAuth client present; no local token yet (local-only check).",
                requires_interactive_action=True,
                next_action="Authorize Google read-only access in browser.",
                metadata={"local_only": True},
            )
        return AuthStatus(
            source=source,
            state=AuthState.NEEDS_LOGIN,
            authenticated=False,
            detail="Local Google OAuth token file present (run consent-gated validate to verify live access).",
            requires_interactive_action=False,
            metadata={"local_only": True, "token_present": True},
        )

    def inspect_only(self, *, allow_network: bool = False) -> OnboardingReport:
        """Audit setup state strictly locally by default (no browser launches or network refresh)."""
        pw_status = self._ensai_auth.environment_status()
        gcp_report = (
            self._google_cloud.inspect()
            if allow_network
            else GoogleCloudSetupHelper(self._config, which_fn=lambda _: None).inspect()
        )
        statuses: dict[str, AuthStatus] = {}
        completed: list[str] = []
        remaining: list[str] = []

        if self._config.sources_env_path.is_file():
            completed.append(f"Detected private config file: {self._config.sources_env_path}")
        else:
            remaining.append(
                f"Generate default private config at {self._config.sources_env_path} (automatic during setup)."
            )

        if pw_status.playwright_installed and pw_status.chromium_installed:
            completed.append("Playwright and Chromium ready.")
        elif self._config.enable_moodle or self._config.enable_pamplemousse:
            remaining.append(pw_status.detail)

        for enabled, src, check_fn in (
            (self._config.enable_moodle, SourceKind.MOODLE, lambda: self._ensai_auth.check_source_connection(SourceKind.MOODLE, headless=True)),
            (self._config.enable_pamplemousse, SourceKind.PAMPLEMOUSSE, lambda: self._ensai_auth.check_source_connection(SourceKind.PAMPLEMOUSSE, headless=True)),
            (self._config.enable_ensora_drive, SourceKind.ENSORA_DRIVE, lambda: self._drive_oauth.status()),
            (self._config.enable_google_calendar, SourceKind.GOOGLE_CALENDAR, lambda: self._calendar_oauth.status()),
        ):
            if not enabled:
                continue
            st = check_fn() if allow_network else self._local_only_source_status(src)
            statuses[src.value] = st
            if st.authenticated:
                completed.append(f"{src.value} authenticated.")
            elif st.next_action:
                remaining.append(f"{src.value}: {st.next_action}")

        remaining.extend(step for step in gcp_report.guided_next_steps if step not in remaining)

        return OnboardingReport(
            config_summary=self._config.redacted_summary(),
            added_env_keys=(),
            playwright_status=pw_status,
            google_cloud_report=gcp_report,
            source_statuses=statuses,
            completed_steps=tuple(completed),
            remaining_actions=tuple(remaining),
        )

    def run_setup(
        self,
        *,
        interactive: bool = True,
        approve_gcp_api_enable: bool = False,
        prompt_ensai_credentials: bool = False,
        consented_sources: set[SourceKind] | frozenset[SourceKind] | None = None,
        allow_network: bool | None = None,
    ) -> OnboardingReport:
        """Run automated setup strictly respecting per-source durable consent.

        When `allow_network` is False (default when `interactive=False` and `consented_sources`
        is omitted), never launches browsers or contacts external providers.
        """
        added_keys = tuple(self._config.ensure_missing_defaults_in_env_file())
        pw_status = self._ensai_auth.environment_status()
        net_allowed = interactive if allow_network is None else allow_network
        gcp_report = (
            self._google_cloud.inspect()
            if net_allowed
            else GoogleCloudSetupHelper(self._config, which_fn=lambda _: None).inspect()
        )
        completed: list[str] = list(gcp_report.completed_steps)
        remaining: list[str] = []

        if added_keys:
            completed.append(f"Added {len(added_keys)} missing default keys to sources.env.")

        if (
            net_allowed
            and approve_gcp_api_enable
            and gcp_report.gcloud_installed
            and gcp_report.project_accessible
            and gcp_report.missing_apis
        ):
            res = self._google_cloud.enable_required_apis(
                approved=True, apis=gcp_report.missing_apis
            )
            if res.returncode == 0:
                completed.append("Enabled required Google Cloud APIs on ensora-516015.")
                gcp_report = self._google_cloud.inspect()
            else:
                remaining.append("Failed enabling Google APIs via gcloud.")

        if prompt_ensai_credentials and interactive and not self._credentials.has_ensai_credentials():
            self._credentials.prompt_missing_ensai_credentials(
                username_hint=self._config.ensai_username_hint,
                persist=True,
            )
            completed.append("Stored ENSAI credentials securely in OS keyring.")

        def _is_consented(src: SourceKind) -> bool:
            if not net_allowed:
                return False
            if consented_sources is None:
                return False
            return src in consented_sources

        statuses: dict[str, AuthStatus] = {}

        # ENSAI sources: probe only when explicitly consented
        if self._config.enable_moodle:
            if _is_consented(SourceKind.MOODLE):
                m_status = self._ensai_auth.check_source_connection(SourceKind.MOODLE, headless=True)
                if not m_status.authenticated and interactive:
                    m_status = self._ensai_auth.authenticate(SourceKind.MOODLE, interactive=True)
            else:
                m_status = self._local_only_source_status(SourceKind.MOODLE)
            statuses[SourceKind.MOODLE.value] = m_status
            if not m_status.authenticated and m_status.next_action:
                remaining.append(f"Moodle: {m_status.next_action}")

        if self._config.enable_pamplemousse:
            if _is_consented(SourceKind.PAMPLEMOUSSE):
                p_status = self._ensai_auth.check_source_connection(
                    SourceKind.PAMPLEMOUSSE, headless=True
                )
                if not p_status.authenticated and interactive:
                    p_status = self._ensai_auth.authenticate(
                        SourceKind.PAMPLEMOUSSE, interactive=True
                    )
            else:
                p_status = self._local_only_source_status(SourceKind.PAMPLEMOUSSE)
            statuses[SourceKind.PAMPLEMOUSSE.value] = p_status
            if not p_status.authenticated and p_status.next_action:
                remaining.append(f"Pamplemousse: {p_status.next_action}")

        # Google sources: probe/refresh only when explicitly consented
        if self._config.enable_ensora_drive:
            if _is_consented(SourceKind.ENSORA_DRIVE):
                d_status = self._drive_oauth.status()
                if not d_status.authenticated and interactive and gcp_report.credentials_validation.valid:
                    d_status = self._drive_oauth.authenticate(interactive=True)
            else:
                d_status = self._local_only_source_status(SourceKind.ENSORA_DRIVE)
            statuses[SourceKind.ENSORA_DRIVE.value] = d_status
            if not d_status.authenticated and d_status.next_action:
                remaining.append(f"Ensora Drive: {d_status.next_action}")

        if self._config.enable_google_calendar:
            if _is_consented(SourceKind.GOOGLE_CALENDAR):
                c_status = self._calendar_oauth.status()
                if not c_status.authenticated and interactive and gcp_report.credentials_validation.valid:
                    c_status = self._calendar_oauth.authenticate(interactive=True)
            else:
                c_status = self._local_only_source_status(SourceKind.GOOGLE_CALENDAR)
            statuses[SourceKind.GOOGLE_CALENDAR.value] = c_status
            if not c_status.authenticated and c_status.next_action:
                remaining.append(f"Google Calendar: {c_status.next_action}")

        for step in gcp_report.guided_next_steps:
            if step not in remaining:
                remaining.append(step)

        return OnboardingReport(
            config_summary=self._config.redacted_summary(),
            added_env_keys=added_keys,
            playwright_status=pw_status,
            google_cloud_report=gcp_report,
            source_statuses=statuses,
            completed_steps=tuple(dict.fromkeys(completed)),
            remaining_actions=tuple(dict.fromkeys(remaining)),
        )
