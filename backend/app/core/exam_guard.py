"""Shared FastAPI dependency enforcing exam-mode isolation (R21).

During an active timed exam attempt, notes, annotations, search, citations,
AI Tutor and Learning Studio endpoints must be blocked server-side with HTTP 403.
"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import Forbidden
from app.core.identity import get_current_user_id


def require_not_exam_mode(
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    user_id: str = Depends(get_current_user_id),
) -> None:
    """Reject access with 403 exam_mode_active while an exam attempt is active."""
    from app.modules.learning.service import LearningService

    state = LearningService(db, clock).exam_mode(user_id)
    if state.active:
        raise Forbidden(
            "Disabled during an active exam attempt",
            code="exam_mode_active",
            details={"attempt_id": state.attempt_id, "expires_at": state.expires_at.isoformat() if state.expires_at else None},
        )
