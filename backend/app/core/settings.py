"""Private runtime configuration for the local application."""
import os
from pathlib import Path

from dotenv import dotenv_values
from pydantic import Field, HttpUrl, SecretStr, field_validator

from ..contracts.sources import Contract

CONFIG = Path.home() / ".config/ensora-plus"
DATA = Path.home() / ".local/share/ensora-plus"


class ProtectedImportPath(ValueError):
    """A local import would expose private application storage."""


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.chmod(0o700)


class Settings(Contract):
    config_dir: Path = CONFIG
    local_data_dir: Path = DATA
    ensai_home_url: HttpUrl = HttpUrl("https://ensai.fr/")
    ensai_portal_url: HttpUrl = HttpUrl("https://genes.sharepoint.com/sites/MyEnsai")
    ensai_cas_url: HttpUrl = HttpUrl("https://ssocas.ensai.fr/cas/login")
    ensai_username: SecretStr | None = Field(default=None, exclude=True)
    ensai_auth_mode: str = "browser"
    ensai_shared_session: bool = True
    ensai_auto_login: bool = False
    ensai_timeout_ms: int = Field(default=30_000, gt=0)
    ensai_profile_dir: Path = CONFIG / "sessions/ensai"
    ensai_keyring_service: str = "ensora-plus"
    moodle_base_url: HttpUrl = HttpUrl("https://foad-moodle.ensai.fr/")
    moodle_auth_mode: str = "browser"
    moodle_protected_url: HttpUrl | None = None
    pamplemousse_base_url: HttpUrl = HttpUrl("https://23ed753f.leos.fr/index.php")
    pamplemousse_auth_mode: str = "browser"
    pamplemousse_protected_url: HttpUrl | None = None
    google_credentials_path: Path = CONFIG / "secrets/google_credentials.json"
    google_token_path: Path = CONFIG / "secrets/google_token.json"
    google_project_id: str = "ensora-516015"
    ensora_drive_folder_id: str = "1bwHMmvVUOI7CJyeiVhvkofspfZdFNZuO"
    google_calendar_id: str = "primary"
    google_calendar_include_personal_details: bool = False
    academic_year: str | None = None
    study_year: str | None = None
    specialization: str | None = None
    browser_headless: bool = False
    request_delay_seconds: float = Field(default=1, ge=0, le=60)
    sync_on_startup: bool = False
    sync_interval_minutes: int = Field(default=0, ge=0)
    sync_concurrency: int = Field(default=2, ge=1, le=8)
    max_document_bytes: int = Field(default=25 * 1024 * 1024, gt=0)
    adapter_factory: str | None = "backend.app.modules.ingestion.codex_bridge:build_components"
    frontend_origin: HttpUrl = HttpUrl("http://127.0.0.1:5173")
    app_env: str = "local"
    log_level: str = "INFO"

    @field_validator("config_dir", "local_data_dir", "ensai_profile_dir",
                     "google_credentials_path", "google_token_path", mode="after")
    @classmethod
    def expand_path(cls, path: Path) -> Path:
        return path.expanduser().absolute()

    @field_validator("google_project_id")
    @classmethod
    def existing_project(cls, value: str) -> str:
        if value != "ensora-516015":
            raise ValueError("Expected the existing Ensora project")
        return value

    @classmethod
    def load(cls, config_path: Path | None = None):
        path = (config_path or Path(os.environ.get("ENSORA_SOURCES_ENV", CONFIG / "sources.env"))).expanduser()
        values = dotenv_values(path, interpolate=False) if path.is_file() else {}
        data = {"config_dir": path.parent}
        aliases = {
            "GOOGLE_PROJECT_ID": "GOOGLE_CLOUD_PROJECT_ID",
            "ENSORA_DRIVE_FOLDER_ID": "ENSORA_DRIVE_ROOT_FOLDER_ID",
            "ENSAI_CAS_URL": "ENSAI_CAS_LOGIN_URL",
            "ENSAI_PORTAL_URL": "MYENSAI_URL",
            "ENSAI_AUTO_LOGIN": "ENSAI_ALLOW_AUTO_CREDENTIAL_SUBMIT",
        }
        for field in cls.model_fields:
            key = field.upper()
            alias = aliases.get(key, key)
            value = os.environ.get(key, os.environ.get(alias, values.get(key, values.get(alias))))
            if value not in (None, ""):
                data[field] = value
        data.setdefault("ensai_profile_dir", path.parent / "sessions/ensai")
        for field, default, legacy in (
            ("google_credentials_path", "google_credentials.json", "google-oauth.json"),
            ("google_token_path", "google_token.json", "google-token.json"),
        ):
            directory = path.parent / "secrets"
            candidate = directory / default
            data.setdefault(field, directory / legacy if not candidate.exists() and
                            (directory / legacy).exists() else candidate)
        return cls.model_validate(data)

    def validate_local_import_path(self, path: str | Path) -> Path:
        selected = Path(path).expanduser().resolve()
        for candidate in (
            self.config_dir, self.local_data_dir, self.ensai_profile_dir,
            self.google_credentials_path, self.google_token_path,
        ):
            protected = candidate.resolve()
            if selected.is_relative_to(protected) or protected.is_relative_to(selected):
                raise ProtectedImportPath("Selected path overlaps private application storage")
        return selected

    def prepare(self) -> None:
        for directory in (self.config_dir, self.config_dir / "secrets",
                          self.config_dir / "sessions", self.local_data_dir,
                          self.local_data_dir / "blobs"):
            private_directory(directory)
        path = self.config_dir / "sources.env"
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("# Optional environment overrides. Store credentials in the keyring.\n"
                         "SYNC_ON_STARTUP=false\nSYNC_INTERVAL_MINUTES=0\n")
