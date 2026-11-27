from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.notifications.service import (
    NotificationList,
    NotificationOut,
    NotificationPatch,
    NotificationService,
)

router = APIRouter(tags=["notifications"])


def service(db: Session = Depends(get_db), clock: Clock = Depends(get_clock)) -> NotificationService:
    return NotificationService(db, clock)


@router.get("/notifications", response_model=NotificationList)
def list_notifications(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=200),
    svc: NotificationService = Depends(service),
    user_id: str = Depends(get_current_user_id),
):
    return svc.list(user_id, unread_only, limit)


@router.patch("/notifications/{notification_id}", response_model=NotificationOut)
def patch_notification(
    notification_id: str,
    body: NotificationPatch,
    svc: NotificationService = Depends(service),
    user_id: str = Depends(get_current_user_id),
):
    return svc.mark(user_id, notification_id, body.read)


@router.post("/notifications/read-all")
def read_all(svc: NotificationService = Depends(service), user_id: str = Depends(get_current_user_id)):
    return {"marked_read": svc.read_all(user_id)}
