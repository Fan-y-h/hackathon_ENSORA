"""Composition root (integration-owned).

One FastAPI process serves:

* ``/api/v1/...``          planner, learning, grades, analytics, workspace, notifications;
* ``/api/v1/sources/...``  the P1 data-engineering API (connections, sync, jobs, documents,
  academic records), mounted from ``backend.app.modules.ingestion.api``;
* ``POST /api/v1/agenda/import-from-sources``: bridge copying P1 calendar events into the agenda.

The two parts keep separate SQLite databases (``data/runtime/ensora.db`` and the P1 store in
``~/.local/share/ensora-plus``); they only meet through the bridge.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from app.core.common import Clock, SystemClock
from app.core.config import demo_root, load_settings
from app.core.db import Database
from app.core.errors import install_error_handlers
from app.core.exam_guard import require_not_exam_mode
from app.core.identity import LOCAL_USER_EMAIL, ensure_user
from app.modules.account.router import initialize_profile
from app.modules.account.router import router as account_router
from app.modules.ai.router import router as ai_router
from app.modules.analytics.router import router as analytics_router
from app.modules.catalog.router import router as catalog_router
from app.modules.grades.router import router as grades_router
from app.modules.grades.source_results import router as source_results_router
from app.modules.learning.router import router as learning_router
from app.modules.notifications.router import router as notifications_router
from app.modules.planner.router import router as planner_router
from app.modules.planner.source_bridge import AgendaAutoImporter
from app.modules.planner.source_bridge import router as source_bridge_router
from app.modules.workspace.router import router as workspace_router

API_PREFIX = "/api/v1"
SOURCES_PREFIX = f"{API_PREFIX}/sources"

logger = logging.getLogger(__name__)

# P1 modules import themselves as ``backend.app.*``: the repository root must be importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.append(str(_REPO_ROOT))


def _build_sources_app(p1_settings, p1_components):
    """Build the P1 sub-application, or ``None`` when its dependencies are unavailable."""
    try:
        from backend.app.modules.ingestion.api import create_app as create_sources_app
    except Exception:  # pragma: no cover - depends on optional runtime packages
        logger.exception("P1 sources API unavailable")
        return None
    sources_app = create_sources_app(
        p1_settings, p1_components, prefix="", managed_runtime=True,
        document_guard=require_not_exam_mode,
    )
    install_error_handlers(sources_app)
    return sources_app


def create_app(
    database_url: str | None = None,
    clock: Clock | None = None,
    *,
    enable_sources: bool | None = None,
    p1_settings=None,
    p1_components=None,
) -> FastAPI:
    """``enable_sources`` defaults to ``True`` only for the real runtime (no explicit
    ``database_url``), so unit tests never touch the user's P1 store or browser profile."""
    settings = load_settings()
    if enable_sources is None:
        enable_sources = database_url is None
    if settings.demo_mode and enable_sources:
        from backend.app.core.runtime import Components
        from backend.app.core.settings import Settings as P1Settings

        root = demo_root()
        config = root / "config"
        p1_settings = P1Settings(
            config_dir=config, local_data_dir=root / "sources",
            ensai_profile_dir=config / "sessions/ensai",
            google_credentials_path=config / "secrets/google_credentials.json",
            google_token_path=config / "secrets/google_token.json",
            adapter_factory=None,
        )
        p1_components = Components()
    sources_app = _build_sources_app(p1_settings, p1_components) if enable_sources else None

    @asynccontextmanager
    async def lifespan(app_: FastAPI):
        runtime = None
        importer = None
        if sources_app is not None:
            try:
                from backend.app.core.runtime import Runtime
                from backend.app.core.settings import Settings as P1Settings

                runtime = Runtime(p1_settings or P1Settings.load(), p1_components).__enter__()
                sources_app.state.runtime = runtime
                if not settings.demo_mode and os.environ.get("ENSORA_PAUSE_BACKGROUND_SYNC") != "true":
                    runtime.start_schedule()
                # Every finished sync (manual or scheduled) reaches the agenda without a click.
                importer = AgendaAutoImporter(runtime.engine, app_.state.db.session_factory, app_.state.clock)
                importer.start()
                app_.state.agenda_importer = importer
            except Exception:
                # Planner & co. keep working; /api/v1/sources/* answers 503.
                logger.exception("P1 source runtime failed to start")
                runtime = None
        try:
            with app_.state.db.session_factory.begin() as db:
                local_user = ensure_user(db, LOCAL_USER_EMAIL, "Étudiant·e local·e")
                initialize_profile(db, local_user.id, runtime)
            yield
        finally:
            ai_runtime = getattr(app_.state, "ai_runtime", None)
            if ai_runtime is not None:
                ai_runtime.close()
                del app_.state.ai_runtime
            if importer is not None:
                importer.stop()
            if runtime is not None:
                runtime.__exit__(None, None, None)

    app = FastAPI(title="Ensora+ API", version="0.2.0", lifespan=lifespan)
    app.state.db = Database(settings.database_url if settings.demo_mode else database_url or settings.database_url)
    app.state.db.create_schema()
    app.state.clock = clock or SystemClock()
    app.state.sources_app = sources_app
    app.state.demo_mode = settings.demo_mode

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "If-Match", "Idempotency-Key", "X-Request-ID"],
        allow_credentials=True,
    )
    install_error_handlers(app)

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    api = APIRouter(prefix=API_PREFIX)

    @api.get("/runtime-mode", tags=["health"])
    def runtime_mode() -> dict[str, Literal["real", "demo"]]:
        return {"mode": "demo" if settings.demo_mode else "real"}

    @api.get("/health", tags=["health"])
    def health(request: Request):
        try:
            with request.app.state.db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # pragma: no cover - reported, not raised
            db_ok = False
        sources = request.app.state.sources_app
        sources_ready = sources is not None and getattr(sources.state, "runtime", None) is not None
        return {"status": "ok" if db_ok else "degraded", "database": db_ok, "sources": sources_ready}

    api.include_router(account_router)
    api.include_router(ai_router)
    api.include_router(catalog_router)
    api.include_router(planner_router)
    api.include_router(source_bridge_router)
    api.include_router(learning_router)
    api.include_router(grades_router)
    api.include_router(source_results_router)
    api.include_router(analytics_router)
    api.include_router(workspace_router)
    api.include_router(notifications_router)
    app.include_router(api)
    if sources_app is not None:
        sources_app.state.db = app.state.db
        sources_app.state.clock = app.state.clock
        app.mount(SOURCES_PREFIX, sources_app)
    return app


# Run with: uvicorn app.main:create_app --factory --app-dir backend --host 127.0.0.1 --port 8000
