"""Runtime configuration management for Ensora+ P1 source connectors.

Reuses the private user runtime configuration at ~/.config/ensora-plus/sources.env
without overwriting existing valid configuration values and without logging secrets.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

# Official institutional and project defaults (prompt-mandated source of truth)
DEFAULT_ENSAI_HOME_URL = "https://ensai.fr/"
DEFAULT_MYENSAI_URL = "https://genes.sharepoint.com/sites/MyEnsai"
DEFAULT_CAS_LOGIN_URL = "https://ssocas.ensai.fr/cas/login"
DEFAULT_MOODLE_URL = "https://foad-moodle.ensai.fr/"
DEFAULT_PAMPLEMOUSSE_URL = "https://23ed753f.leos.fr/index.php"

DEFAULT_GOOGLE_CLOUD_PROJECT_ID = "ensora-516015"
DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
DEFAULT_GOOGLE_CALENDAR_ID = "primary"

GOOGLE_SCOPE_CALENDAR_READONLY = "https://www.googleapis.com/auth/calendar.readonly"
GOOGLE_SCOPE_DRIVE_READONLY = "https://www.googleapis.com/auth/drive.readonly"

ALLOWED_ENSAI_AUTH_ORIGINS: frozenset[str] = frozenset(
    {
        "https://ssocas.ensai.fr",
        "https://foad-moodle.ensai.fr",
        "https://23ed753f.leos.fr",
        "https://genes.sharepoint.com",
        "https://login.microsoftonline.com",
    }
)

SENSITIVE_ENV_FRAGMENTS: tuple[str, ...] = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "COOKIE",
    "TICKET",
    "API_KEY",
    "PRIVATE_KEY",
)


def default_config_root() -> Path:
    """Return private Ensora+ configuration directory (~/.config/ensora-plus)."""
    override = os.environ.get("ENSORA_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "ensora-plus"


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv-style file safely without mutating os.environ."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        if key:
            values[key] = val
    return values


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_float(value: str | None, default: float) -> float:
    if value is None or value == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _as_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


@dataclass
class SourcesRuntimeConfig:
    """Normalized runtime configuration loaded from sources.env + environment."""

    config_dir: Path = field(default_factory=default_config_root)
    sources_env_path: Path = field(init=False)
    ensai_profile_dir: Path = field(init=False)
    secrets_dir: Path = field(init=False)
    google_credentials_path: Path = field(init=False)
    google_token_path: Path = field(init=False)

    # Institutional URLs
    ensai_home_url: str = DEFAULT_ENSAI_HOME_URL
    myensai_url: str = DEFAULT_MYENSAI_URL
    cas_login_url: str = DEFAULT_CAS_LOGIN_URL
    moodle_base_url: str = DEFAULT_MOODLE_URL
    pamplemousse_base_url: str = DEFAULT_PAMPLEMOUSSE_URL
    moodle_protected_url: str | None = None
    pamplemousse_protected_url: str | None = None

    # Optional Moodle Web Services token reference (never stored in Git)
    moodle_ws_enabled: bool = False
    moodle_auth_mode: str = "browser"
    pamplemousse_auth_mode: str = "browser"
    google_calendar_include_personal_details: bool = False

    # ENSAI Playwright policy
    ensai_headless: bool = False
    ensai_allow_automated_credential_submit: bool = False
    ensai_use_shared_session: bool = True
    ensai_timeout_ms: int = 30_000
    ensai_username_hint: str | None = None

    # Google Cloud & OAuth
    google_cloud_project_id: str = DEFAULT_GOOGLE_CLOUD_PROJECT_ID
    ensora_drive_root_folder_id: str = DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
    google_calendar_id: str = DEFAULT_GOOGLE_CALENDAR_ID
    enable_ensora_drive: bool = True
    enable_google_calendar: bool = True
    enable_moodle: bool = True
    enable_pamplemousse: bool = True
    google_separate_source_tokens: bool = False

    # Operational safety limits
    request_timeout_sec: float = 20.0
    rate_limit_min_interval_sec: float = 0.5
    max_retries: int = 3
    max_download_bytes: int = 100 * 1024 * 1024  # 100 MiB

    # Raw non-sensitive keys discovered (keys only, values never dumped)
    present_env_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.config_dir = self.config_dir.expanduser()
        self.sources_env_path = self.config_dir / "sources.env"
        self.ensai_profile_dir = self.config_dir / "sessions" / "ensai"
        self.secrets_dir = self.config_dir / "secrets"
        self.google_credentials_path = self.secrets_dir / "google_credentials.json"
        self.google_token_path = self.secrets_dir / "google_token.json"

    @property
    def enabled_google_scopes(self) -> tuple[str, ...]:
        """Return least-privilege read-only Google scopes for enabled sources only."""
        scopes: list[str] = []
        if self.enable_google_calendar:
            scopes.append(GOOGLE_SCOPE_CALENDAR_READONLY)
        if self.enable_ensora_drive:
            scopes.append(GOOGLE_SCOPE_DRIVE_READONLY)
        return tuple(scopes)

    def google_token_path_for(self, scope_group: str | None = None) -> Path:
        """Return token file path, optionally separated per Google source."""
        if not self.google_separate_source_tokens or not scope_group:
            return self.google_token_path
        safe_group = "".join(ch for ch in scope_group.lower() if ch.isalnum() or ch in ("_", "-"))
        return self.secrets_dir / f"google_token_{safe_group}.json"

    def ensure_private_directories(self) -> None:
        """Create private runtime config directories with owner-only permissions."""
        for directory in (self.config_dir, self.ensai_profile_dir, self.secrets_dir):
            directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass

    def redacted_summary(self) -> dict[str, object]:
        """Return non-sensitive diagnostic summary suitable for logs and UI."""
        return {
            "config_dir": str(self.config_dir),
            "sources_env_exists": self.sources_env_path.is_file(),
            "ensai_profile_dir_exists": self.ensai_profile_dir.is_dir(),
            "google_credentials_exists": self.google_credentials_path.is_file(),
            "google_token_exists": self.google_token_path.is_file(),
            "google_cloud_project_id": self.google_cloud_project_id,
            "ensora_drive_root_folder_id": self.ensora_drive_root_folder_id,
            "google_calendar_id": self.google_calendar_id,
            "moodle_base_url": self.moodle_base_url,
            "pamplemousse_base_url": self.pamplemousse_base_url,
            "cas_login_url": self.cas_login_url,
            "ensai_auto_login": self.ensai_allow_automated_credential_submit,
            "ensai_use_shared_session": self.ensai_use_shared_session,
            "enabled_sources": {
                "moodle": self.enable_moodle,
                "pamplemousse": self.enable_pamplemousse,
                "ensora_drive": self.enable_ensora_drive,
                "google_calendar": self.enable_google_calendar,
            },
            "enabled_google_scopes": list(self.enabled_google_scopes),
            "present_env_keys": [
                k for k in self.present_env_keys if not is_sensitive_key(k)
            ],
        }

    @classmethod
    def load(
        cls,
        *,
        config_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> "SourcesRuntimeConfig":
        """Load config from ~/.config/ensora-plus/sources.env with env overrides."""
        base_dir = (config_dir or default_config_root()).expanduser()
        env_path = base_dir / "sources.env"
        file_values = _parse_env_file(env_path)
        env = dict(os.environ if environ is None else environ)

        def pick(key: str, default: str | None = None) -> str | None:
            if key in env and env[key] != "":
                return env[key]
            if key in file_values and file_values[key] != "":
                return file_values[key]
            return default

        cfg = cls(
            config_dir=base_dir,
            ensai_home_url=pick("ENSAI_HOME_URL", DEFAULT_ENSAI_HOME_URL) or DEFAULT_ENSAI_HOME_URL,
            myensai_url=pick("MYENSAI_URL", DEFAULT_MYENSAI_URL) or DEFAULT_MYENSAI_URL,
            cas_login_url=pick("ENSAI_CAS_LOGIN_URL", DEFAULT_CAS_LOGIN_URL) or DEFAULT_CAS_LOGIN_URL,
            moodle_base_url=pick("MOODLE_BASE_URL", DEFAULT_MOODLE_URL) or DEFAULT_MOODLE_URL,
            pamplemousse_base_url=pick("PAMPLEMOUSSE_BASE_URL", DEFAULT_PAMPLEMOUSSE_URL)
            or DEFAULT_PAMPLEMOUSSE_URL,
            moodle_protected_url=pick("MOODLE_PROTECTED_URL"),
            pamplemousse_protected_url=pick("PAMPLEMOUSSE_PROTECTED_URL"),
            moodle_ws_enabled=_as_bool(pick("MOODLE_WS_ENABLED"), False),
            ensai_headless=_as_bool(pick("ENSAI_HEADLESS"), False),
            ensai_allow_automated_credential_submit=_as_bool(
                pick("ENSAI_AUTO_LOGIN") or pick("ENSAI_ALLOW_AUTO_CREDENTIAL_SUBMIT"),
                False,
            ),
            ensai_use_shared_session=_as_bool(pick("ENSAI_SHARED_SESSION"), True),
            ensai_timeout_ms=_as_int(pick("ENSAI_TIMEOUT_MS"), 30_000),
            ensai_username_hint=pick("ENSAI_USERNAME") or pick("ENSAI_USER"),
            google_cloud_project_id=pick(
                "GOOGLE_CLOUD_PROJECT_ID", DEFAULT_GOOGLE_CLOUD_PROJECT_ID
            )
            or DEFAULT_GOOGLE_CLOUD_PROJECT_ID,
            ensora_drive_root_folder_id=pick(
                "ENSORA_DRIVE_ROOT_FOLDER_ID", DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID
            )
            or DEFAULT_ENSORA_DRIVE_ROOT_FOLDER_ID,
            google_calendar_id=pick("GOOGLE_CALENDAR_ID", DEFAULT_GOOGLE_CALENDAR_ID)
            or DEFAULT_GOOGLE_CALENDAR_ID,
            enable_ensora_drive=_as_bool(pick("ENABLE_ENSORA_DRIVE"), True),
            enable_google_calendar=_as_bool(pick("ENABLE_GOOGLE_CALENDAR"), True),
            enable_moodle=_as_bool(pick("ENABLE_MOODLE"), True),
            enable_pamplemousse=_as_bool(pick("ENABLE_PAMPLEMOUSSE"), True),
            google_separate_source_tokens=_as_bool(
                pick("GOOGLE_SEPARATE_SOURCE_TOKENS"), False
            ),
            request_timeout_sec=_as_float(pick("SOURCE_REQUEST_TIMEOUT_SEC"), 20.0),
            rate_limit_min_interval_sec=_as_float(pick("SOURCE_RATE_LIMIT_SEC"), 0.5),
            max_retries=_as_int(pick("SOURCE_MAX_RETRIES"), 3),
            max_download_bytes=_as_int(pick("SOURCE_MAX_DOWNLOAD_BYTES"), 100 * 1024 * 1024),
            present_env_keys=tuple(sorted(file_values.keys())),
        )

        # Allow explicit custom private paths from sources.env while preserving defaults
        custom_profile = pick("ENSAI_BROWSER_PROFILE_DIR")
        if custom_profile:
            cfg.ensai_profile_dir = Path(custom_profile).expanduser()
        custom_creds = pick("GOOGLE_CREDENTIALS_PATH")
        if custom_creds:
            cfg.google_credentials_path = Path(custom_creds).expanduser()
        custom_token = pick("GOOGLE_TOKEN_PATH")
        if custom_token:
            cfg.google_token_path = Path(custom_token).expanduser()

        return cfg

    def ensure_missing_defaults_in_env_file(self) -> list[str]:
        """Append ONLY missing non-sensitive configuration keys to sources.env.

        Existing keys and values are strictly preserved verbatim.
        Returns the list of keys added.
        """
        self.ensure_private_directories()
        existing = _parse_env_file(self.sources_env_path)
        defaults = {
            "ENSAI_HOME_URL": self.ensai_home_url,
            "MYENSAI_URL": self.myensai_url,
            "ENSAI_CAS_LOGIN_URL": self.cas_login_url,
            "MOODLE_BASE_URL": self.moodle_base_url,
            "PAMPLEMOUSSE_BASE_URL": self.pamplemousse_base_url,
            "ENSAI_BROWSER_PROFILE_DIR": str(self.ensai_profile_dir),
            "GOOGLE_CLOUD_PROJECT_ID": self.google_cloud_project_id,
            "ENSORA_DRIVE_ROOT_FOLDER_ID": self.ensora_drive_root_folder_id,
            "GOOGLE_CALENDAR_ID": self.google_calendar_id,
            "GOOGLE_CREDENTIALS_PATH": str(self.google_credentials_path),
            "GOOGLE_TOKEN_PATH": str(self.google_token_path),
            "ENABLE_MOODLE": "true" if self.enable_moodle else "false",
            "ENABLE_PAMPLEMOUSSE": "true" if self.enable_pamplemousse else "false",
            "ENABLE_ENSORA_DRIVE": "true" if self.enable_ensora_drive else "false",
            "ENABLE_GOOGLE_CALENDAR": "true" if self.enable_google_calendar else "false",
        }
        added: list[str] = []
        lines_to_append: list[str] = []
        for key, value in defaults.items():
            if key not in existing or existing[key] == "":
                lines_to_append.append(f"{key}={value}")
                added.append(key)

        if not self.sources_env_path.exists():
            header = [
                "# Ensora+ private source configuration (auto-generated non-secret defaults)",
                "# Institutional passwords and OAuth tokens are NOT stored here.",
            ]
            content = "\n".join(header + lines_to_append) + "\n"
            self.sources_env_path.write_text(content, encoding="utf-8")
            try:
                os.chmod(self.sources_env_path, 0o600)
            except OSError:
                pass
        elif lines_to_append:
            existing_text = self.sources_env_path.read_text(encoding="utf-8", errors="replace")
            sep = "" if existing_text.endswith("\n") or existing_text == "" else "\n"
            append_block = sep + "\n".join(lines_to_append) + "\n"
            with self.sources_env_path.open("a", encoding="utf-8") as fh:
                fh.write(append_block)
            try:
                os.chmod(self.sources_env_path, 0o600)
            except OSError:
                pass
        return added

    def set_non_sensitive_env_flag(self, key: str, value: str) -> None:
        """Atomically update a single non-sensitive boolean/policy flag in sources.env.

        Strictly preserves all other lines, comments, and private settings verbatim.
        Refuses any key name matching sensitive secret/password/token patterns.
        """
        clean_key = key.strip().upper()
        if not clean_key or not clean_key.replace("_", "").isalnum():
            raise ValueError("Invalid configuration key name.")
        if is_sensitive_key(clean_key):
            raise ValueError(
                f"Refusing to write sensitive key {clean_key!r} to plaintext sources.env; use OS keyring."
            )
        if "\n" in value or "\r" in value:
            raise ValueError("Configuration values must be single-line.")
        self.ensure_private_directories()
        lines: list[str] = []
        replaced = False
        if self.sources_env_path.is_file():
            for raw in self.sources_env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = raw.strip()
                candidate = stripped[len("export ") :].strip() if stripped.startswith("export ") else stripped
                if "=" in candidate and not stripped.startswith("#"):
                    k = candidate.split("=", 1)[0].strip()
                    if k == clean_key:
                        lines.append(f"{clean_key}={value}")
                        replaced = True
                        continue
                lines.append(raw)
        if not replaced:
            lines.append(f"{clean_key}={value}")

        tmp_path = self.sources_env_path.with_name(f".{self.sources_env_path.name}.tmp.{os.getpid()}")
        fd = os.open(
            str(tmp_path),
            os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                os.fchmod(fh.fileno(), 0o600)
                fh.write("\n".join(lines) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.sources_env_path)
            try:
                os.chmod(self.sources_env_path, 0o600)
            except OSError:
                pass
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)


def is_sensitive_key(key: str) -> bool:
    """Return True if a configuration key name could hold sensitive data."""
    upper = key.upper()
    return any(fragment in upper for fragment in SENSITIVE_ENV_FRAGMENTS)
