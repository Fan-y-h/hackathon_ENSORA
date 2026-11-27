from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import Depends, Header
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.identity import ensure_user, get_current_user_id
from app.main import create_app

# Sunday 29 Nov 2026, 13:00 Paris time.
NOW = datetime(2026, 11, 29, 12, 0, tzinfo=UTC)


class FixedClock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        self._now = now


def clock_of(client) -> FixedClock:
    return client.app.state.clock


def _test_user(x_test_user: str = Header(default="alice"), db: Session = Depends(get_db)) -> str:
    """Test-only identity: lets a test act as several users to check isolation."""
    return ensure_user(db, f"{x_test_user}@test.local", x_test_user).id


def make_app(database_url: str = "sqlite://"):
    app = create_app(database_url, clock=FixedClock())
    app.dependency_overrides[get_current_user_id] = _test_user
    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(make_app())


@pytest.fixture
def as_bob() -> dict[str, str]:
    return {"X-Test-User": "bob"}
