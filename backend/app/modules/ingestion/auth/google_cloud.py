"""Google Cloud provisioning automation helper for project ensora-516015.

Uses only official Google Cloud CLI commands (`gcloud`) and validates the
local OAuth desktop client file at ~/.config/ensora-plus/secrets/google_credentials.json.
Never creates a new Google Cloud project and never uses browser automation
to bypass Google Cloud Console security controls.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..config import DEFAULT_GOOGLE_CLOUD_PROJECT_ID, SourcesRuntimeConfig

GOOGLE_DRIVE_API = "drive.googleapis.com"
GOOGLE_CALENDAR_API = "calendar-json.googleapis.com"
REQUIRED_GOOGLE_APIS: tuple[str, ...] = (GOOGLE_DRIVE_API, GOOGLE_CALENDAR_API)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[list[str]], CommandResult]


def _default_runner(cmd: list[str]) -> CommandResult:
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return CommandResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


@dataclass(frozen=True)
class CredentialsFileValidation:
    """Validation status for google_credentials.json without exposing secrets."""

    exists: bool
    valid: bool
    client_type: str | None
    client_id_suffix: str | None
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["errors"] = list(self.errors)
        return data


def validate_google_credentials_file(path: Path) -> CredentialsFileValidation:
    """Validate structure of an OAuth Desktop/Web client JSON file safely."""
    if not path.is_file():
        return CredentialsFileValidation(
            exists=False,
            valid=False,
            client_type=None,
            client_id_suffix=None,
            errors=(f"Missing OAuth client file at {path}",),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return CredentialsFileValidation(
            exists=True,
            valid=False,
            client_type=None,
            client_id_suffix=None,
            errors=(f"Invalid JSON in OAuth client file: {type(exc).__name__}",),
        )

    if not isinstance(payload, dict):
        return CredentialsFileValidation(
            exists=True,
            valid=False,
            client_type=None,
            client_id_suffix=None,
            errors=("OAuth client file root must be a JSON object.",),
        )

    client_type = "installed" if "installed" in payload else ("web" if "web" in payload else None)
    if client_type is None or not isinstance(payload.get(client_type), dict):
        return CredentialsFileValidation(
            exists=True,
            valid=False,
            client_type=None,
            client_id_suffix=None,
            errors=("Expected top-level 'installed' (Desktop app) or 'web' OAuth client config.",),
        )

    section = payload[client_type]
    errors: list[str] = []
    for required_key in ("client_id", "auth_uri", "token_uri"):
        val = section.get(required_key)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"Missing required field '{required_key}' in '{client_type}' section.")

    client_id = str(section.get("client_id") or "")
    suffix = f"...{client_id[-12:]}" if len(client_id) > 12 else None

    if client_type == "web":
        redirects = section.get("redirect_uris") or []
        if not any("localhost" in str(r) or "127.0.0.1" in str(r) for r in redirects):
            errors.append(
                "Web OAuth client lacks a localhost redirect URI; prefer a Desktop ('installed') client."
            )

    return CredentialsFileValidation(
        exists=True,
        valid=len(errors) == 0,
        client_type=client_type,
        client_id_suffix=suffix,
        errors=tuple(errors),
    )


@dataclass(frozen=True)
class GoogleCloudProvisioningReport:
    """Structured developer-side Google Cloud readiness & guided setup report."""

    project_id: str
    gcloud_installed: bool
    gcloud_authenticated: bool
    active_account: str | None
    project_accessible: bool
    enabled_apis: tuple[str, ...]
    missing_apis: tuple[str, ...]
    credentials_validation: CredentialsFileValidation
    completed_steps: tuple[str, ...]
    guided_next_steps: tuple[str, ...]
    console_links: dict[str, str] = field(default_factory=dict)

    @property
    def ready_for_oauth(self) -> bool:
        return self.credentials_validation.valid and len(self.missing_apis) == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "gcloud_installed": self.gcloud_installed,
            "gcloud_authenticated": self.gcloud_authenticated,
            "active_account": self.active_account,
            "project_accessible": self.project_accessible,
            "enabled_apis": list(self.enabled_apis),
            "missing_apis": list(self.missing_apis),
            "credentials_validation": self.credentials_validation.to_dict(),
            "completed_steps": list(self.completed_steps),
            "guided_next_steps": list(self.guided_next_steps),
            "console_links": self.console_links,
            "ready_for_oauth": self.ready_for_oauth,
        }


def _mask_account_email(account: str | None) -> str | None:
    """Mask personal Google email in diagnostic outputs (e.g. d***@ensai.fr)."""
    if not account:
        return None
    if "@" not in account:
        return f"{account[:1]}***"
    local, domain = account.split("@", 1)
    prefix = local[:1] if local else ""
    return f"{prefix}***@{domain}"


class GoogleCloudSetupHelper:
    """Inspects and automates officially supported Google Cloud setup for ensora-516015."""

    def __init__(
        self,
        config: SourcesRuntimeConfig,
        *,
        command_runner: CommandRunner | None = None,
        which_fn: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._config = config
        self._project_id = config.google_cloud_project_id or DEFAULT_GOOGLE_CLOUD_PROJECT_ID
        self._run = command_runner or _default_runner
        self._which = which_fn

    def project_console_links(self) -> dict[str, str]:
        p = self._project_id
        return {
            "project_dashboard": f"https://console.cloud.google.com/home/dashboard?project={p}",
            "api_library": f"https://console.cloud.google.com/apis/library?project={p}",
            "oauth_consent_screen": f"https://console.cloud.google.com/apis/credentials/consent?project={p}",
            "oauth_credentials": f"https://console.cloud.google.com/apis/credentials?project={p}",
        }

    def inspect(self) -> GoogleCloudProvisioningReport:
        """Audit gcloud CLI, auth, project access, enabled APIs, and local credentials."""
        completed: list[str] = []
        next_steps: list[str] = []
        links = self.project_console_links()

        creds_val = validate_google_credentials_file(self._config.google_credentials_path)
        if creds_val.valid:
            completed.append("Valid Google OAuth client file detected.")

        gcloud_path = self._which("gcloud")
        gcloud_installed = gcloud_path is not None
        gcloud_authenticated = False
        active_account: str | None = None
        project_accessible = False
        enabled_apis: set[str] = set()

        if gcloud_installed:
            completed.append("Google Cloud CLI (gcloud) detected.")
            auth_res = self._run(["gcloud", "auth", "list", "--format=json"])
            if auth_res.returncode == 0:
                try:
                    accounts = json.loads(auth_res.stdout or "[]")
                    for acct in accounts:
                        if acct.get("status") == "ACTIVE":
                            gcloud_authenticated = True
                            active_account = _mask_account_email(acct.get("account"))
                            break
                except json.JSONDecodeError:
                    pass
            if gcloud_authenticated:
                completed.append("gcloud developer account authenticated.")
            else:
                next_steps.append("Authenticate developer CLI: gcloud auth login")

            if gcloud_authenticated:
                proj_res = self._run(
                    ["gcloud", "projects", "describe", self._project_id, "--format=json"]
                )
                if proj_res.returncode == 0:
                    project_accessible = True
                    completed.append(f"Verified access to existing project '{self._project_id}'.")
                else:
                    next_steps.append(
                        f"Verify account permissions for existing project '{self._project_id}' "
                        f"at {links['project_dashboard']}"
                    )

            if project_accessible:
                svc_res = self._run(
                    [
                        "gcloud",
                        "services",
                        "list",
                        "--enabled",
                        f"--project={self._project_id}",
                        "--format=json",
                    ]
                )
                if svc_res.returncode == 0:
                    try:
                        services = json.loads(svc_res.stdout or "[]")
                        for svc in services:
                            name = (svc.get("config") or {}).get("name") or svc.get("name", "")
                            for req in REQUIRED_GOOGLE_APIS:
                                if name.endswith(req):
                                    enabled_apis.add(req)
                    except json.JSONDecodeError:
                        pass
        else:
            if not creds_val.valid:
                next_steps.append(
                    f"Optional: install gcloud CLI to check/enable APIs automatically, or enable "
                    f"Google Drive API & Google Calendar API in Console: {links['api_library']}"
                )

        required_for_enabled: list[str] = []
        if self._config.enable_ensora_drive:
            required_for_enabled.append(GOOGLE_DRIVE_API)
        if self._config.enable_google_calendar:
            required_for_enabled.append(GOOGLE_CALENDAR_API)

        missing_apis = tuple(api for api in required_for_enabled if api not in enabled_apis)
        if project_accessible and not missing_apis:
            completed.append("Required Google APIs (Drive & Calendar) are enabled.")
        elif project_accessible and missing_apis:
            apis_arg = " ".join(missing_apis)
            next_steps.append(
                f"Enable missing APIs (after explicit approval): "
                f"gcloud services enable {apis_arg} --project={self._project_id}"
            )
        elif not gcloud_installed and creds_val.valid:
            # If user already provided valid credentials without gcloud installed, don't block OAuth
            missing_apis = ()

        if not creds_val.valid:
            next_steps.append(
                f"Configure OAuth consent screen (External/Testing + test user email) at "
                f"{links['oauth_consent_screen']}"
            )
            next_steps.append(
                f"Create an OAuth 2.0 Client ID (Application type: Desktop app) at "
                f"{links['oauth_credentials']} and download the JSON to "
                f"{self._config.google_credentials_path}"
            )

        return GoogleCloudProvisioningReport(
            project_id=self._project_id,
            gcloud_installed=gcloud_installed,
            gcloud_authenticated=gcloud_authenticated,
            active_account=active_account,
            project_accessible=project_accessible,
            enabled_apis=tuple(sorted(enabled_apis)),
            missing_apis=missing_apis,
            credentials_validation=creds_val,
            completed_steps=tuple(completed),
            guided_next_steps=tuple(next_steps),
            console_links=links,
        )

    def enable_required_apis(
        self,
        *,
        approved: bool = False,
        apis: list[str] | tuple[str, ...] | None = None,
    ) -> CommandResult:
        """Enable allowlisted Google APIs on ensora-516015 via official gcloud command.

        Requires explicit `approved=True` caller confirmation. When `apis` is supplied,
        enables ONLY that exact allowlisted subset (matching the user approval prompt).
        """
        if not approved:
            raise PermissionError(
                "Enabling Google Cloud APIs requires explicit approval (approved=True)."
            )
        if self._which("gcloud") is None:
            raise RuntimeError("gcloud CLI is not installed.")

        target_apis: list[str]
        if apis is not None:
            disallowed = [a for a in apis if a not in REQUIRED_GOOGLE_APIS]
            if disallowed:
                raise ValueError(f"Disallowed Google Cloud API(s): {', '.join(disallowed)}")
            target_apis = list(dict.fromkeys(apis))
        else:
            target_apis = []
            if self._config.enable_ensora_drive:
                target_apis.append(GOOGLE_DRIVE_API)
            if self._config.enable_google_calendar:
                target_apis.append(GOOGLE_CALENDAR_API)

        if not target_apis:
            return CommandResult(returncode=0, stdout="No Google APIs requested.", stderr="")

        return self._run(
            ["gcloud", "services", "enable", *target_apis, f"--project={self._project_id}"]
        )
