from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.customer_payment import (
    ApplicationCreate,
    ApplicationResponse,
    CustomerPaymentCreate,
    CustomerPaymentResponse,
    OutstandingOrderResponse,
    RejectionRequest,
    ReversalRequest,
)
from app.services import customer_payment_service

router = APIRouter()

_READ = require_privilege(SystemObject.CUSTOMER_PAYMENTS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.CUSTOMER_PAYMENTS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.CUSTOMER_PAYMENTS, AccessRight.UPDATE)
_VERIFY_READ = require_privilege(SystemObject.PAYMENTS_VERIFICATION, AccessRight.READ)
_VERIFY_UPDATE = require_privilege(SystemObject.PAYMENTS_VERIFICATION, AccessRight.UPDATE)
_EDITOR_READ = require_privilege(SystemObject.PAYMENTS_EDITOR, AccessRight.READ)


async def _payment_or_404(db: AsyncSession, customer_payment_id: int):  # noqa: ANN202
    payment = await customer_payment_service.get_payment(db, customer_payment_id)
    if payment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Payment not found')
    return payment


@router.get('', response_model=ListResponse[CustomerPaymentResponse])
async def list_customer_payments(
    customer: int | None = Query(None),
    cash_session: int | None = Query(None),
    facility: int | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    method: int | None = Query(None),
    verified: bool | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerPaymentResponse]:
    items, total = await customer_payment_service.list_payments(
        db,
        current=current,
        customer=customer,
        cash_session=cash_session,
        facility=facility,
        date_from=date_from,
        date_to=date_to,
        method=method,
        verified=verified,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[CustomerPaymentResponse.model_validate(p) for p in items], total=total
    )


@router.get('/unverified', response_model=ListResponse[CustomerPaymentResponse])
async def list_unverified_payments(
    facility: int | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    method: int | None = Query(None),
    amount_min: Decimal | None = Query(None),
    amount_max: Decimal | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_VERIFY_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerPaymentResponse]:
    items, total = await customer_payment_service.list_payments(
        db,
        current=current,
        facility=facility,
        date_from=date_from,
        date_to=date_to,
        method=method,
        amount_min=amount_min,
        amount_max=amount_max,
        unverified_only=True,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[CustomerPaymentResponse.model_validate(p) for p in items], total=total
    )


@router.get('/search', response_model=ListResponse[CustomerPaymentResponse])
async def search_payments_across_facilities(
    customer: int | None = Query(None),
    reference: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_EDITOR_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerPaymentResponse]:
    """Payments editor search — crosses facilities, so it is gated by PaymentsEditor (100)."""
    items, total = await customer_payment_service.list_payments(
        db,
        current=current,
        customer=customer,
        reference=reference,
        date_from=date_from,
        date_to=date_to,
        cross_facility=True,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[CustomerPaymentResponse.model_validate(p) for p in items], total=total
    )


@router.get('/outstanding-orders', response_model=ListResponse[OutstandingOrderResponse])
async def list_outstanding_orders(
    search: str | None = Query(None),
    customer: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[OutstandingOrderResponse]:
    rows, total = await customer_payment_service.search_outstanding(
        db, current=current, search=search, customer=customer, skip=skip, limit=limit
    )
    return ListResponse(
        items=[OutstandingOrderResponse.model_validate(r) for r in rows], total=total
    )


@router.post('', response_model=CustomerPaymentResponse, status_code=status.HTTP_201_CREATED)
async def create_customer_payment(
    data: CustomerPaymentCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerPaymentResponse:
    payment = await customer_payment_service.create_payment(db, data, current=current)
    return CustomerPaymentResponse.model_validate(payment)


@router.get('/{customer_payment_id}', response_model=CustomerPaymentResponse)
async def get_customer_payment(
    customer_payment_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CustomerPaymentResponse:
    payment = await _payment_or_404(db, customer_payment_id)
    await customer_payment_service.attach_unapplied(db, payment)
    return CustomerPaymentResponse.model_validate(payment)


@router.get('/{customer_payment_id}/applications', response_model=list[ApplicationResponse])
async def list_payment_applications(
    customer_payment_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[ApplicationResponse]:
    """Includes cancelled applications — reversals stay visible (FR-045, FR-073)."""
    payment = await _payment_or_404(db, customer_payment_id)
    rows = await customer_payment_service.list_applications(db, payment.customer_payment_id)
    return [ApplicationResponse.model_validate(r) for r in rows]


@router.post(
    '/{customer_payment_id}/applications',
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def apply_customer_payment(
    customer_payment_id: int,
    data: ApplicationCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    payment = await _payment_or_404(db, customer_payment_id)
    application = await customer_payment_service.apply_payment(db, payment, data, current=current)
    return ApplicationResponse.model_validate(application)


@router.post(
    '/{customer_payment_id}/applications/{application_id}/reverse',
    response_model=ApplicationResponse,
)
async def reverse_customer_payment_application(
    customer_payment_id: int,
    application_id: int,
    data: ReversalRequest,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ApplicationResponse:
    payment = await _payment_or_404(db, customer_payment_id)
    application = await customer_payment_service.get_application(db, payment, application_id)
    if application is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Application not found')
    application = await customer_payment_service.reverse_application(
        db, payment, application, reason=data.reason, current=current
    )
    return ApplicationResponse.model_validate(application)


@router.post('/{customer_payment_id}/verify', response_model=CustomerPaymentResponse)
async def verify_customer_payment(
    customer_payment_id: int,
    current: CurrentUser = Depends(_VERIFY_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerPaymentResponse:
    payment = await _payment_or_404(db, customer_payment_id)
    payment = await customer_payment_service.verify_payment(db, payment, current=current)
    return CustomerPaymentResponse.model_validate(payment)


@router.post('/{customer_payment_id}/reject', response_model=CustomerPaymentResponse)
async def reject_customer_payment(
    customer_payment_id: int,
    data: RejectionRequest,
    current: CurrentUser = Depends(_VERIFY_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerPaymentResponse:
    payment = await _payment_or_404(db, customer_payment_id)
    payment = await customer_payment_service.reject_payment(
        db, payment, reason=data.reason, current=current
    )
    return CustomerPaymentResponse.model_validate(payment)
