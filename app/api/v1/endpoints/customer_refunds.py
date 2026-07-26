from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.customer_refund import (
    CustomerRefundConfirm,
    CustomerRefundCreate,
    CustomerRefundLineUpdate,
    CustomerRefundResponse,
    CustomerRefundSummary,
)
from app.services import customer_refund_service

router = APIRouter()

_READ = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.UPDATE)
# Confirming a refund moves stock and money, so it has its own system object
_CONFIRM = require_privilege(SystemObject.CUSTOMER_REFUND_CONFIRM, AccessRight.UPDATE)


async def _refund_or_404(db: AsyncSession, customer_refund_id: int):  # noqa: ANN202
    refund = await customer_refund_service.get_refund(db, customer_refund_id)
    if refund is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Refund not found')
    return refund


@router.get('', response_model=ListResponse[CustomerRefundSummary])
async def list_customer_refunds(
    customer: int | None = Query(None),
    sales_order: int | None = Query(None),
    refund_status: str | None = Query(None, alias='status'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerRefundSummary]:
    items, total = await customer_refund_service.list_refunds(
        db,
        current=current,
        customer=customer,
        sales_order=sales_order,
        refund_status=refund_status,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[CustomerRefundSummary.model_validate(r) for r in items], total=total
    )


@router.post('', response_model=CustomerRefundResponse, status_code=status.HTTP_201_CREATED)
async def open_customer_refund(
    data: CustomerRefundCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await customer_refund_service.open_refund(db, data.sales_order, current=current)
    return CustomerRefundResponse.model_validate(refund)


@router.get('/{customer_refund_id}', response_model=CustomerRefundResponse)
async def get_customer_refund(
    customer_refund_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, customer_refund_id)
    await customer_refund_service.attach_derived(db, refund)
    return CustomerRefundResponse.model_validate(refund)


@router.put('/{customer_refund_id}/lines/{line_id}', response_model=CustomerRefundResponse)
async def update_customer_refund_line(
    customer_refund_id: int,
    line_id: int,
    data: CustomerRefundLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, customer_refund_id)
    line = await customer_refund_service.get_line(db, refund, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    refund = await customer_refund_service.update_line(db, refund, line, data, current=current)
    return CustomerRefundResponse.model_validate(refund)


@router.post('/{customer_refund_id}/confirm', response_model=CustomerRefundResponse)
async def confirm_customer_refund(
    customer_refund_id: int,
    data: CustomerRefundConfirm,
    current: CurrentUser = Depends(_CONFIRM),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, customer_refund_id)
    refund = await customer_refund_service.confirm_refund(
        db, refund, payout=data.payout, current=current
    )
    return CustomerRefundResponse.model_validate(refund)


@router.post('/{customer_refund_id}/cancel', response_model=CustomerRefundResponse)
async def cancel_customer_refund(
    customer_refund_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, customer_refund_id)
    refund = await customer_refund_service.cancel_refund(db, refund, current=current)
    return CustomerRefundResponse.model_validate(refund)
