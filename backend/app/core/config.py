"""Runtime configuration read from environment variables (see docs/DEPLOYMENT.md)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_REPO_DIR = _BACKEND_DIR.parent


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_url: str
    frontend_origin: str
    demo_mode: bool


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", _REPO_DIR / "data" / "runtime"))
    default_db = f"sqlite:///{(data_dir / 'ensora.db').as_posix()}"
    return Settings(
        app_env=os.environ.get("APP_ENV", "development"),
        database_url=os.environ.get("DATABASE_URL", default_db),
        frontend_origin=os.environ.get("FRONTEND_ORIGIN", "http://127.0.0.1:5173"),
        demo_mode=os.environ.get("DEMO_MODE", "false").lower() == "true",
    )
