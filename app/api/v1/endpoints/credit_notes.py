from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.credit_note import CreditNoteResponse
from app.services import credit_note_service

router = APIRouter()

_READ = require_privilege(SystemObject.CREDIT_PAYMENTS, AccessRight.READ)

# Redemption has no route here on purpose: it is an ordinary payment application against the
# note's backing payment — POST /customer-payments/{backing_payment_id}/applications (FR-070a).
# That keeps it bounded by the payment's unapplied amount, reversible, and correctable through the
# payments editor, with none of that logic duplicated.


@router.get('', response_model=ListResponse[CreditNoteResponse])
async def list_credit_notes(
    customer: int | None = Query(None),
    open_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CreditNoteResponse]:
    items, total = await credit_note_service.list_credit_notes(
        db, current=current, customer=customer, open_only=open_only, skip=skip, limit=limit
    )
    return ListResponse(items=[CreditNoteResponse.model_validate(n) for n in items], total=total)


@router.get('/{credit_note_id}', response_model=CreditNoteResponse)
async def get_credit_note(
    credit_note_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CreditNoteResponse:
    note = await credit_note_service.get_credit_note(db, credit_note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Credit note not found')
    await credit_note_service.attach_remaining(db, note)
    return CreditNoteResponse.model_validate(note)
