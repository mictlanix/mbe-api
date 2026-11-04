from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, can_see_every_facility, require_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
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
from app.services import customer_refund_service, print_contexts

router = APIRouter()

_READ = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.CUSTOMER_REFUNDS, AccessRight.UPDATE)
# Confirming a refund moves stock and money, so it has its own system object
_CONFIRM = require_privilege(SystemObject.CUSTOMER_REFUND_CONFIRM, AccessRight.UPDATE)


async def _sees_every_facility(db: AsyncSession, current: CurrentUser) -> bool:
    """A refund is its processing store's; another store's is SearchCreditsFromAllStores (101)
    territory, as another store's sales orders are (spec 034)."""
    return await can_see_every_facility(db, current, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)


async def _refund_or_404(  # noqa: ANN202
    db: AsyncSession, current: CurrentUser, customer_refund_id: int
):
    """404 rather than 403 for another facility's refund, so its existence isn't revealed."""
    refund = await customer_refund_service.get_refund(db, customer_refund_id)
    if refund is None or (
        refund.facility != current.facility_id and not await _sees_every_facility(db, current)
    ):
        raise not_found('customer_refund', customer_refund_id, detail='Refund not found')
    return refund


@router.get('', response_model=ListResponse[CustomerRefundSummary])
async def list_customer_refunds(
    customer: int | None = Query(None),
    sales_order: int | None = Query(None),
    refund_status: str | None = Query(None, alias='status'),
    search: str | None = Query(
        None,
        description='A number matches the refund id, its folio or the source order id; text '
        "matches the customer's name",
    ),
    mine: bool = Query(False, description='Only refunds the caller created or last updated'),
    facility: int | None = Query(None, description="Defaults to the caller's facility"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CustomerRefundSummary]:
    if (
        facility is not None
        and facility != current.facility_id
        and not await _sees_every_facility(db, current)
    ):
        raise DomainError(
            ErrorCode.FACILITY_OTHER_FACILITY,
            "Another facility's refunds require SearchCreditsFromAllStores read",
            {
                'resource': 'customer_refund',
                'required': [
                    {
                        'system_object': SystemObject.SEARCH_CREDITS_FROM_ALL_STORES,
                        'right': AccessRight.READ,
                    }
                ],
            },
        )
    items, total = await customer_refund_service.list_refunds(
        db,
        current=current,
        customer=customer,
        sales_order=sales_order,
        refund_status=refund_status,
        search=search,
        mine=mine,
        facility=facility,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[CustomerRefundSummary.model_validate(r) for r in items], total=total)


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
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, current, customer_refund_id)
    await customer_refund_service.attach_derived(db, refund)
    return CustomerRefundResponse.model_validate(refund)


@router.get(
    '/{customer_refund_id}/ticket', response_class=Response, responses=rendering.PDF_RESPONSE
)
async def print_customer_refund_ticket(
    customer_refund_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """A confirmed refund's receipt (#287). An open or cancelled refund has none: 409, as legacy
    sends an unconfirmed refund back to its editor."""
    refund = await _refund_or_404(db, current, customer_refund_id)
    if not refund.completed:
        raise DomainError(
            ErrorCode.REFUND_NOT_CONFIRMED, 'Only a confirmed refund has a receipt to print'
        )
    await customer_refund_service.attach_derived(db, refund)
    template, context = await print_contexts.refund_ticket_context(db, refund)
    pdf = await rendering.render_pdf(template, context)
    return rendering.pdf_response(pdf, f'devolucion-{refund.customer_refund_id:08d}.pdf')


@router.put('/{customer_refund_id}/lines/{line_id}', response_model=CustomerRefundResponse)
async def update_customer_refund_line(
    customer_refund_id: int,
    line_id: int,
    data: CustomerRefundLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, current, customer_refund_id)
    line = await customer_refund_service.get_line(db, refund, line_id)
    if line is None:
        raise not_found('customer_refund_line', line_id, detail='Line not found')
    refund = await customer_refund_service.update_line(db, refund, line, data, current=current)
    return CustomerRefundResponse.model_validate(refund)


@router.post('/{customer_refund_id}/confirm', response_model=CustomerRefundResponse)
async def confirm_customer_refund(
    customer_refund_id: int,
    data: CustomerRefundConfirm,
    current: CurrentUser = Depends(_CONFIRM),
    db: AsyncSession = Depends(get_db),
) -> CustomerRefundResponse:
    refund = await _refund_or_404(db, current, customer_refund_id)
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
    refund = await _refund_or_404(db, current, customer_refund_id)
    refund = await customer_refund_service.cancel_refund(db, refund, current=current)
    return CustomerRefundResponse.model_validate(refund)
