"""Users and current-user resolution.

STUB for T01 (C1): local single-user mode. The user is resolved server-side and never
taken from the request body. Real local session auth/CSRF replaces ``get_current_user_id``
without changing module code; tests override it to exercise cross-user isolation.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import String, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.core.db import Base, TimestampMixin, get_db, new_id

LOCAL_USER_EMAIL = "local@ensora.local"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)


def ensure_user(db: Session, email: str, display_name: str = "") -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(email=email, display_name=display_name)
        db.add(user)
        db.flush()
    return user


def get_current_user_id(db: Session = Depends(get_db)) -> str:
    return ensure_user(db, LOCAL_USER_EMAIL, "Étudiant·e local·e").id
