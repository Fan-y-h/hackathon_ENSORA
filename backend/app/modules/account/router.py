from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import ValidationFailed
from app.core.identity import get_current_user_id
from app.modules.account.data_rights import CONFIRMATION, delete_user, export_user
from app.modules.account.domain import STUDENT_STATUS_LABELS
from app.modules.account.models import Profile
from app.modules.account.schemas import ProfileOut, ProfilePatch, StudentStatusOptionOut
from app.modules.account.service import AccountService

router = APIRouter(tags=["profile"])


def _sources_runtime(request: Request):
    sources = getattr(request.app.state, "sources_app", None)
    return getattr(getattr(sources, "state", None), "runtime", None)


@router.get("/profile", response_model=ProfileOut)
def get_profile(request: Request, db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    return initialize_profile(db, user_id, _sources_runtime(request))


def initialize_profile(db: Session, user_id: str, runtime=None) -> ProfileOut:
    account = AccountService(db)
    if runtime is not None and db.scalar(select(Profile.id).where(Profile.user_id == user_id)) is None:
        source = runtime.current_profile()
        fields = {}
        for key, value in (("display_name", source.display_name), ("cohort", (source.study_year or "").upper()),
                           ("track", source.specialization), ("academic_year", source.academic_year)):
            if value:
                try:
                    ProfilePatch.model_validate({key: value})
                except ValidationError:
                    continue
                fields[key] = value
        if fields:
            return account.patch(user_id, ProfilePatch(**fields))
    return account.get(user_id)


@router.patch("/profile", response_model=ProfileOut)
def patch_profile(
    body: ProfilePatch,
    request: Request,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    profile = AccountService(db).patch(user_id, body)
    runtime = _sources_runtime(request)
    if runtime is not None:
        mirrored = {"display_name": "display_name", "cohort": "study_year", "track": "specialization",
                    "academic_year": "academic_year", "timezone": "timezone"}
        changes = {target: getattr(profile, field) for field, target in mirrored.items()
                   if field in body.model_fields_set}
        if changes:
            runtime.update_profile(**changes)
    return profile


@router.get("/profile/student-statuses", response_model=list[StudentStatusOptionOut])
def list_student_statuses():
    return [StudentStatusOptionOut(status=k, label=v) for k, v in STUDENT_STATUS_LABELS.items()]


class DeleteAccount(BaseModel):
    confirm: str


@router.post("/account/export")
def export_account(db: Session = Depends(get_db), clock: Clock = Depends(get_clock),
                   user_id: str = Depends(get_current_user_id)):
    """All personal data (marks included) as JSON. Synchronous here; the contract's async job
    variant is unnecessary for a local single-user store."""
    return export_user(db, user_id, clock.now())


@router.delete("/account", status_code=204)
def delete_account(body: DeleteAccount, db: Session = Depends(get_db),
                   user_id: str = Depends(get_current_user_id)):
    if body.confirm != CONFIRMATION:
        raise ValidationFailed(f'Type "{CONFIRMATION}" to confirm', code="confirmation_required")
    delete_user(db, user_id)
    return Response(status_code=204)
