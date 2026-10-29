from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, can_see_every_facility, require_privilege
from app.core.errors import not_found
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


async def _sees_every_facility(db: AsyncSession, current: CurrentUser) -> bool:
    """A note is a customer payment: another facility's is PaymentsEditor (100) territory, as
    on `/customer-payments` (#271, spec 034 FR-013)."""
    return await can_see_every_facility(db, current, SystemObject.PAYMENTS_EDITOR)


@router.get('', response_model=ListResponse[CreditNoteResponse])
async def list_credit_notes(
    customer: int | None = Query(None),
    customer_payment: int | None = Query(
        None,
        description='The backing payment, whose id a credit-note ticket prints under its barcode',
    ),
    facility: int | None = Query(None, description="Defaults to the caller's facility"),
    open_only: bool = Query(
        False,
        description='Only notes with more than 0.01 left that were not paid back in cash, and '
        "not the walk-in customer's: the ones that can be spent",
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CreditNoteResponse]:
    if (
        facility is not None
        and facility != current.facility_id
        and not await _sees_every_facility(db, current)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Another facility's credit notes require PaymentsEditor read",
        )
    items, total = await credit_note_service.list_credit_notes(
        db,
        current=current,
        customer=customer,
        customer_payment=customer_payment,
        facility=facility,
        open_only=open_only,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[CreditNoteResponse.model_validate(n) for n in items], total=total)


@router.get('/{credit_note_id}', response_model=CreditNoteResponse)
async def get_credit_note(
    credit_note_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CreditNoteResponse:
    """404 rather than 403 for another facility's note, so its existence isn't revealed."""
    note = await credit_note_service.get_credit_note(db, credit_note_id)
    if note is None or (
        note.facility != current.facility_id and not await _sees_every_facility(db, current)
    ):
        raise not_found('credit_note', credit_note_id)
    return CreditNoteResponse.model_validate(note)
