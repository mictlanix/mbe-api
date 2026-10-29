from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, require_privilege
from app.core.errors import not_found
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.purchases import PurchaseOrder
from app.schemas import ListResponse
from app.schemas.purchases import (
    AddRequestLines,
    CostChange,
    OrderLineAdd,
    OrderLineUpdate,
    PurchaseOrderCreate,
    PurchaseOrderEmailRequest,
    PurchaseOrderResponse,
    PurchaseOrderSummary,
    PurchaseOrderUpdate,
    ToPurchaseCreate,
    ToPurchaseResponse,
)
from app.schemas.sales_quote import QuoteEmailResponse
from app.schemas.supplier_payments import PurchaseOrderBalance
from app.services import mail, purchase_cost_service, supplier_payment_service
from app.services import purchase_order_service as service

router = APIRouter()

_READ = require_privilege(SystemObject.PURCHASES_ORDERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.PURCHASES_ORDERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.PURCHASES_ORDERS, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.PURCHASES_ORDERS, AccessRight.DELETE)
_PAYMENTS_READ = require_privilege(SystemObject.SUPPLIER_PAYMENT, AccessRight.READ)


async def _order_or_404(db: AsyncSession, order_id: int):  # noqa: ANN202
    """Header only: the state-changing services read lines after they lock (research R3, R4)."""
    order = await service.get_order(db, order_id)
    if order is None:
        raise not_found(PurchaseOrder, order_id)
    return order


async def _response(db: AsyncSession, order) -> PurchaseOrderResponse:  # noqa: ANN001
    return PurchaseOrderResponse.model_validate(await service.attach(db, order))


@router.get('', response_model=ListResponse[PurchaseOrderSummary])
async def list_purchase_orders(
    everyone: bool = Query(False, alias='all'),
    order_status: str | None = Query(None, alias='status'),
    supplier: int | None = Query(None),
    creator: int | None = Query(None),
    order_id: int | None = Query(None, alias='id'),
    date_from: date | None = Query(None, alias='from'),
    date_to: date | None = Query(None, alias='to'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PurchaseOrderSummary]:
    items, total = await service.list_orders(
        db,
        current=current,
        everyone=everyone,
        state=order_status,
        supplier=supplier,
        creator=creator,
        order_id=order_id,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[PurchaseOrderSummary.model_validate(item) for item in items], total=total
    )


@router.post('', response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_order(
    data: PurchaseOrderCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    return await _response(db, await service.create(db, data, current=current))


@router.get('/to-purchase', response_model=ToPurchaseResponse)
async def to_purchase_by_supplier(
    facility: int | None = Query(None),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ToPurchaseResponse:
    """Pending marked lines of approved requests, per supplier and facility (FR-024)."""
    groups = await service.to_purchase_groups(db, facility=facility)
    return ToPurchaseResponse.model_validate({'groups': groups}, from_attributes=True)


@router.post(
    '/to-purchase', response_model=PurchaseOrderResponse, status_code=status.HTTP_201_CREATED
)
async def create_purchase_order_from_group(
    data: ToPurchaseCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    """One draft PO from a supplier and facility's pending lines (FR-025)."""
    order = await service.create_from_group(db, data.supplier, data.facility, current=current)
    return await _response(db, order)


@router.get('/cost-changes', response_model=ListResponse[CostChange])
async def list_cost_changes(
    product: int = Query(...),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CostChange]:
    """A product's average-cost changes and restores, newest first (FR-036)."""
    items, total = await purchase_cost_service.history(db, product, skip=skip, limit=limit)
    return ListResponse(items=[CostChange.model_validate(item) for item in items], total=total)


@router.get('/{order_id}', response_model=PurchaseOrderResponse)
async def get_purchase_order(
    order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    return await _response(db, await _order_or_404(db, order_id))


@router.put('/{order_id}', response_model=PurchaseOrderResponse)
async def update_purchase_order(
    order_id: int,
    data: PurchaseOrderUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    await _order_or_404(db, order_id)
    return await _response(db, await service.update(db, order_id, data, current=current))


@router.post('/{order_id}/lines', response_model=PurchaseOrderResponse)
async def add_purchase_order_line(
    order_id: int,
    data: OrderLineAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    await _order_or_404(db, order_id)
    return await _response(db, await service.add_line(db, order_id, data, current=current))


@router.put('/{order_id}/lines/{line_id}', response_model=PurchaseOrderResponse)
async def update_purchase_order_line(
    order_id: int,
    line_id: int,
    data: OrderLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    await _order_or_404(db, order_id)
    order = await service.update_line(db, order_id, line_id, data, current=current)
    return await _response(db, order)


@router.delete('/{order_id}/lines/{line_id}', response_model=PurchaseOrderResponse)
async def remove_purchase_order_line(
    order_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    await _order_or_404(db, order_id)
    return await _response(db, await service.remove_line(db, order_id, line_id, current=current))


@router.post('/{order_id}/request-lines', response_model=PurchaseOrderResponse)
async def add_purchase_request_lines(
    order_id: int,
    data: AddRequestLines,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    """An approved request's pending lines; lines taken meanwhile come back in `skipped`."""
    await _order_or_404(db, order_id)
    order = await service.add_request_lines(db, order_id, data.purchase_request, current=current)
    return await _response(db, order)


@router.post('/{order_id}/confirm', response_model=PurchaseOrderResponse)
async def confirm_purchase_order(
    order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    """Confirm, creating one draft receipt per destination warehouse (FR-027)."""
    await _order_or_404(db, order_id)
    return await _response(db, await service.confirm(db, order_id, current=current))


@router.post('/{order_id}/cancel', response_model=PurchaseOrderResponse)
async def cancel_purchase_order(
    order_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderResponse:
    await _order_or_404(db, order_id)
    return await _response(db, await service.cancel(db, order_id, current=current))


@router.get('/{order_id}/payments', response_model=PurchaseOrderBalance)
async def get_purchase_order_payments(
    order_id: int,
    _: CurrentUser = Depends(_PAYMENTS_READ),
    db: AsyncSession = Depends(get_db),
) -> PurchaseOrderBalance:
    """The PO's balance and its supplier-payment applications (spec 032, FR-019)."""
    order = await _order_or_404(db, order_id)
    return PurchaseOrderBalance.model_validate(
        await supplier_payment_service.purchase_order_balance(db, order)
    )


@router.get('/{order_id}/document', response_class=Response, responses=rendering.PDF_RESPONSE)
async def print_purchase_order_document(
    order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The letter-size orden de compra of a confirmed PO (FR-040)."""
    order = await _order_or_404(db, order_id)
    pdf, _context = await service.document(db, order)
    return rendering.pdf_response(pdf, f'orden-compra-{order.purchase_order_id:08d}.pdf')


@router.post('/{order_id}/email', response_model=QuoteEmailResponse)
async def email_purchase_order(
    order_id: int,
    data: PurchaseOrderEmailRequest,
    current: CurrentUser = Depends(_UPDATE),
    mailer: mail.Mailer | None = Depends(mail.get_mailer),
    db: AsyncSession = Depends(get_db),
) -> QuoteEmailResponse:
    """Send a confirmed PO's PDF to 1–10 addresses, and record the send (FR-041, FR-042)."""
    if mailer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Email is not configured'
        )
    order = await _order_or_404(db, order_id)
    sent_at = await service.email_order(
        db, order, data.to, data.message, mailer=mailer, current=current
    )
    return QuoteEmailResponse(recipients=data.to, sent_at=sent_at)
