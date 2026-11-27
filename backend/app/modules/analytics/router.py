from fastapi import APIRouter, Depends
from pydantic import AwareDatetime
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.identity import get_current_user_id
from app.modules.analytics.service import (
    AnalyticsOut,
    AnalyticsService,
    heatmap_report,
    mastery_map,
    readiness_report,
)

router = APIRouter(tags=["analytics"])


@router.get("/analytics", response_model=AnalyticsOut)
def analytics(
    start: AwareDatetime,
    end: AwareDatetime,
    subject_id: str | None = None,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
    user_id: str = Depends(get_current_user_id),
):
    return AnalyticsService(db, clock).build(user_id, start, end, subject_id)


@router.get("/analytics/readiness")
def readiness(db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
              user_id: str = Depends(get_current_user_id)):
    return readiness_report(db, clock, user_id)


@router.get("/analytics/heatmap")
def heatmap(start: AwareDatetime, end: AwareDatetime, db: Session = Depends(get_db),
            clock: Clock = Depends(get_clock), user_id: str = Depends(get_current_user_id)):
    return heatmap_report(db, clock, user_id, start, end)


@router.get("/analytics/mastery-map")
def mastery_map_endpoint(subject_id: str | None = None, db: Session = Depends(get_db),
                         clock: Clock = Depends(get_clock), user_id: str = Depends(get_current_user_id)):
    return mastery_map(db, clock, user_id, subject_id)
