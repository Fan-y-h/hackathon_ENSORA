from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.common import Clock, get_clock
from app.core.db import get_db
from app.core.errors import ValidationFailed
from app.core.identity import get_current_user_id
from app.modules.account.data_rights import CONFIRMATION, delete_user, export_user
from app.modules.account.schemas import ProfileOut, ProfilePatch
from app.modules.account.service import AccountService

router = APIRouter(tags=["profile"])


@router.get("/profile", response_model=ProfileOut)
def get_profile(db: Session = Depends(get_db), user_id: str = Depends(get_current_user_id)):
    return AccountService(db).get(user_id)


@router.patch("/profile", response_model=ProfileOut)
def patch_profile(
    body: ProfilePatch,
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
):
    return AccountService(db).patch(user_id, body)


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
