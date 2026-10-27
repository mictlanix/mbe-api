from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.customer_payment import ReversalRequest
from app.schemas.supplier_payments import (
    ApplicationsCreate,
    PaymentStatus,
    SupplierBalance,
    SupplierPaymentCreate,
    SupplierPaymentResponse,
    SupplierPaymentSummary,
)
from app.services import supplier_payment_service as service

router = APIRouter()

_READ = require_privilege(SystemObject.SUPPLIER_PAYMENT, AccessRight.READ)
_CREATE = require_privilege(SystemObject.SUPPLIER_PAYMENT, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.SUPPLIER_PAYMENT, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.SUPPLIER_PAYMENT, AccessRight.DELETE)


@router.get('', response_model=ListResponse[SupplierPaymentSummary])
async def list_supplier_payments(
    supplier: int | None = Query(None),
    facility: int | None = Query(None),
    method: int | None = Query(None),
    payment_status: PaymentStatus | None = Query(None, alias='status'),
    date_from: date | None = Query(None, alias='from'),
    date_to: date | None = Query(None, alias='to'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[SupplierPaymentSummary]:
    """Newest first, across facilities (FR-019, FR-022)."""
    items, total = await service.list_payments(
        db,
        supplier=supplier,
        facility=facility,
        method=method,
        state=payment_status,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[SupplierPaymentSummary.model_validate(item) for item in items], total=total
    )


@router.post('', response_model=SupplierPaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier_payment(
    data: SupplierPaymentCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierPaymentResponse:
    """A numbered payment, with its applications if any; a cash one leaves the caller's drawer."""
    payment = await service.create_payment(db, data, current=current)
    return SupplierPaymentResponse.model_validate(payment)


@router.get('/suppliers/{supplier_id}/balance', response_model=SupplierBalance)
async def get_supplier_balance(
    supplier_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> SupplierBalance:
    """The POs with a balance, the payments with credit, and the totals (FR-019)."""
    return SupplierBalance.model_validate(await service.supplier_balance(db, supplier_id))


@router.get('/{payment_id}', response_model=SupplierPaymentResponse)
async def get_supplier_payment(
    payment_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> SupplierPaymentResponse:
    payment = await service.get_payment(db, payment_id)
    if payment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Supplier payment not found'
        )
    return SupplierPaymentResponse.model_validate(payment)


@router.post('/{payment_id}/applications', response_model=SupplierPaymentResponse)
async def apply_supplier_payment(
    payment_id: int,
    data: ApplicationsCreate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierPaymentResponse:
    """Apply credit to confirmed POs of the payment's supplier, all or nothing (FR-014)."""
    payment = await service.apply_payment(db, payment_id, data, current=current)
    return SupplierPaymentResponse.model_validate(payment)


@router.post(
    '/{payment_id}/applications/{application_id}/reverse', response_model=SupplierPaymentResponse
)
async def reverse_supplier_payment_application(
    payment_id: int,
    application_id: int,
    data: ReversalRequest,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> SupplierPaymentResponse:
    """Reverse one application, with a reason (FR-015)."""
    payment = await service.reverse_application(
        db, payment_id, application_id, data.reason, current=current
    )
    return SupplierPaymentResponse.model_validate(payment)


@router.post('/{payment_id}/cancel', response_model=SupplierPaymentResponse)
async def cancel_supplier_payment(
    payment_id: int,
    data: ReversalRequest,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> SupplierPaymentResponse:
    """Cancel, reversing every live application (FR-016)."""
    payment = await service.cancel_payment(db, payment_id, data.reason, current=current)
    return SupplierPaymentResponse.model_validate(payment)
