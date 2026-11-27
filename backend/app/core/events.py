"""Transactional outbox for durable domain events (docs/ARCHITECTURE.md § Consistency).

Events are written in the same transaction as the change. Payloads carry IDs and
revisions only, never private content.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base, UTCDateTime, new_id, utcnow

SCHEMA_VERSION = 1


class OutboxEvent(Base):
    __tablename__ = "outbox"

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=SCHEMA_VERSION, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


def record_event(
    db: Session,
    *,
    user_id: str,
    type: str,
    entity_id: str,
    revision: int,
    payload: dict[str, Any] | None = None,
) -> OutboxEvent:
    evt = OutboxEvent(
        user_id=user_id,
        type=type,
        entity_id=entity_id,
        revision=revision,
        payload=payload or {},
    )
    db.add(evt)
    return evt
