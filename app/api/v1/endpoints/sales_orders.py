from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.customer_payment import OrderApplicationResponse
from app.schemas.sales_order import (
    ProductLookupResponse,
    SalesOrderCreate,
    SalesOrderLineCreate,
    SalesOrderLineUpdate,
    SalesOrderResponse,
    SalesOrderSummary,
    SalesOrderUpdate,
)
from app.services import customer_payment_service, sales_order_service

router = APIRouter()

_READ = require_privilege(SystemObject.SALES_ORDERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.SALES_ORDERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.SALES_ORDERS, AccessRight.UPDATE)
# Payment data, so it answers to the payments privilege rather than the order's own (#134).
_PAYMENTS_READ = require_privilege(SystemObject.CUSTOMER_PAYMENTS, AccessRight.READ)


async def _order_or_404(db: AsyncSession, sales_order_id: int):  # noqa: ANN202
    order = await sales_order_service.get_order(db, sales_order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')
    return order


@router.get('', response_model=ListResponse[SalesOrderSummary])
async def list_sales_orders(
    mine: bool = Query(False),
    customer: int | None = Query(None),
    salesperson: int | None = Query(None),
    order_status: str | None = Query(None, alias='status'),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    facility: int | None = Query(None),
    point_sale: int | None = Query(None),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[SalesOrderSummary]:
    items, total = await sales_order_service.list_orders(
        db,
        current=current,
        mine=mine,
        customer=customer,
        salesperson=salesperson,
        order_status=order_status,
        date_from=date_from,
        date_to=date_to,
        facility=facility,
        point_sale=point_sale,
        search=search,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[SalesOrderSummary.model_validate(o) for o in items], total=total)


@router.get('/product-lookup', response_model=list[ProductLookupResponse])
async def lookup_products(
    pattern: str = Query(..., min_length=1),
    customer: int = Query(...),
    warehouse: int | None = Query(None),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[ProductLookupResponse]:
    rows = await sales_order_service.lookup_products(
        db, pattern=pattern, customer_id=customer, warehouse=warehouse
    )
    return [ProductLookupResponse.model_validate(r) for r in rows]


@router.post('', response_model=SalesOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_sales_order(
    data: SalesOrderCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await sales_order_service.create_order(db, data, current=current)
    return SalesOrderResponse.model_validate(order)


@router.get('/{sales_order_id}', response_model=SalesOrderResponse)
async def get_sales_order(
    sales_order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    await sales_order_service.attach_derived(db, order)
    return SalesOrderResponse.model_validate(order)


@router.put('/{sales_order_id}', response_model=SalesOrderResponse)
async def update_sales_order(
    sales_order_id: int,
    data: SalesOrderUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    order = await sales_order_service.update_order(db, order, data, current=current)
    return SalesOrderResponse.model_validate(order)


@router.get('/{sales_order_id}/payments', response_model=list[OrderApplicationResponse])
async def list_sales_order_payments(
    sales_order_id: int,
    _: CurrentUser = Depends(_PAYMENTS_READ),
    db: AsyncSession = Depends(get_db),
) -> list[OrderApplicationResponse]:
    """Includes cancelled applications — reversals stay visible, as on the payment side (#134)."""
    order = await _order_or_404(db, sales_order_id)
    rows = await customer_payment_service.list_order_applications(db, order.sales_order_id)
    return [OrderApplicationResponse.model_validate(r) for r in rows]


@router.post('/{sales_order_id}/confirm', response_model=SalesOrderResponse)
async def confirm_sales_order(
    sales_order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    order = await sales_order_service.confirm_order(db, order, current=current)
    return SalesOrderResponse.model_validate(order)


@router.post('/{sales_order_id}/cancel', response_model=SalesOrderResponse)
async def cancel_sales_order(
    sales_order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    order = await sales_order_service.cancel_order(db, order, current=current)
    return SalesOrderResponse.model_validate(order)


@router.post('/{sales_order_id}/lines', response_model=SalesOrderResponse)
async def add_sales_order_line(
    sales_order_id: int,
    data: SalesOrderLineCreate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    order = await sales_order_service.add_line(db, order, data, current=current)
    return SalesOrderResponse.model_validate(order)


@router.put('/{sales_order_id}/lines/{line_id}', response_model=SalesOrderResponse)
async def update_sales_order_line(
    sales_order_id: int,
    line_id: int,
    data: SalesOrderLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    line = await sales_order_service.get_line(db, order, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    order = await sales_order_service.update_line(db, order, line, data, current=current)
    return SalesOrderResponse.model_validate(order)


@router.delete('/{sales_order_id}/lines/{line_id}', response_model=SalesOrderResponse)
async def remove_sales_order_line(
    sales_order_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    order = await _order_or_404(db, sales_order_id)
    line = await sales_order_service.get_line(db, order, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    order = await sales_order_service.remove_line(db, order, line, current=current)
    return SalesOrderResponse.model_validate(order)
