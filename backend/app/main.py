"""Composition root (integration-owned; minimal bootstrap for the agenda/profile slice)."""

from __future__ import annotations

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.common import Clock, SystemClock
from app.core.config import load_settings
from app.core.db import Database
from app.core.errors import install_error_handlers
from app.modules.account.router import router as account_router
from app.modules.analytics.router import router as analytics_router
from app.modules.grades.router import router as grades_router
from app.modules.learning.router import router as learning_router
from app.modules.notifications.router import router as notifications_router
from app.modules.planner.router import router as planner_router
from app.modules.workspace.router import router as workspace_router

API_PREFIX = "/api/v1"


def create_app(database_url: str | None = None, clock: Clock | None = None) -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="Ensora+ API", version="0.1.0")
    app.state.db = Database(database_url or settings.database_url)
    app.state.db.create_schema()
    app.state.clock = clock or SystemClock()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
        allow_headers=["Content-Type", "If-Match", "Idempotency-Key", "X-Request-ID"],
        allow_credentials=True,
    )
    install_error_handlers(app)

    api = APIRouter(prefix=API_PREFIX)

    @api.get("/health", tags=["health"])
    def health(request: Request):
        try:
            with request.app.state.db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception:  # pragma: no cover - reported, not raised
            db_ok = False
        return {"status": "ok" if db_ok else "degraded", "database": db_ok}

    api.include_router(account_router)
    api.include_router(planner_router)
    api.include_router(learning_router)
    api.include_router(grades_router)
    api.include_router(analytics_router)
    api.include_router(workspace_router)
    api.include_router(notifications_router)
    app.include_router(api)
    return app
# Run with: uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
