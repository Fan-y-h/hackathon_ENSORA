"""Small shared helpers: clock, pagination and optimistic revision checks."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated, Generic, Protocol, TypeVar

from fastapi import Request
from pydantic import AfterValidator, BaseModel

from app.core.errors import Conflict, ValidationFailed

T = TypeVar("T")

def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC)


# Output timestamps are always normalized to UTC (docs/API_CONTRACTS.md).
UtcDatetime = Annotated[datetime, AfterValidator(_to_utc)]

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def get_clock(request: Request) -> Clock:
    return request.app.state.clock


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = int(json.loads(base64.urlsafe_b64decode(cursor.encode()))["o"])
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValidationFailed("Invalid cursor") from exc
    if offset < 0:
        raise ValidationFailed("Invalid cursor")
    return offset


def paginate(items: Sequence[T], limit: int, cursor: str | None) -> tuple[list[T], str | None]:
    offset = decode_cursor(cursor)
    window = list(items[offset : offset + limit])
    nxt = encode_cursor(offset + limit) if offset + limit < len(items) else None
    return window, nxt


def check_revision(current: int, expected: int | None) -> None:
    if expected is not None and expected != current:
        raise Conflict(
            "Stale revision",
            details={"expected_revision": expected, "current_revision": current},
            code="stale_revision",
        )
