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
    cors_origins: tuple[str, ...]
    demo_mode: bool


def _expand_local_origins(primary: str) -> tuple[str, ...]:
    base = primary.rstrip("/")
    origins = [base]
    for candidate in (
        base.replace("127.0.0.1", "localhost"),
        base.replace("localhost", "127.0.0.1"),
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ):
        if candidate not in origins:
            origins.append(candidate)
    return tuple(origins)


def load_settings() -> Settings:
    data_dir = Path(os.environ.get("DATA_DIR", _REPO_DIR / "data" / "runtime"))
    default_db = f"sqlite:///{(data_dir / 'ensora.db').as_posix()}"
    frontend_origin = os.environ.get("FRONTEND_ORIGIN", "http://127.0.0.1:5173").rstrip("/")
    return Settings(
        app_env=os.environ.get("APP_ENV", "development"),
        database_url=os.environ.get("DATABASE_URL", default_db),
        frontend_origin=frontend_origin,
        cors_origins=_expand_local_origins(frontend_origin),
        demo_mode=os.environ.get("DEMO_MODE", "false").lower() == "true",
    )
