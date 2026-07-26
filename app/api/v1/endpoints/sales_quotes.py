from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.sales_order import SalesOrderResponse
from app.schemas.sales_quote import (
    SalesQuoteCreate,
    SalesQuoteLineCreate,
    SalesQuoteLineUpdate,
    SalesQuoteResponse,
    SalesQuoteSummary,
    SalesQuoteUpdate,
)
from app.services import sales_quote_service

router = APIRouter()

_READ = require_privilege(SystemObject.SALES_QUOTES, AccessRight.READ)
_CREATE = require_privilege(SystemObject.SALES_QUOTES, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.SALES_QUOTES, AccessRight.UPDATE)
# Converting produces a sales order, so it needs the right to create one as well
_CONVERT = require_privilege(SystemObject.SALES_ORDERS, AccessRight.CREATE)


async def _quote_or_404(db: AsyncSession, sales_quote_id: int):  # noqa: ANN202
    quote = await sales_quote_service.get_quote(db, sales_quote_id)
    if quote is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales quote not found')
    return quote


@router.get('', response_model=ListResponse[SalesQuoteSummary])
async def list_sales_quotes(
    mine: bool = Query(False),
    customer: int | None = Query(None),
    salesperson: int | None = Query(None),
    quote_status: str | None = Query(None, alias='status'),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[SalesQuoteSummary]:
    items, total = await sales_quote_service.list_quotes(
        db,
        current=current,
        mine=mine,
        customer=customer,
        salesperson=salesperson,
        quote_status=quote_status,
        search=search,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[SalesQuoteSummary.model_validate(q) for q in items], total=total)


@router.post('', response_model=SalesQuoteResponse, status_code=status.HTTP_201_CREATED)
async def create_sales_quote(
    data: SalesQuoteCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await sales_quote_service.create_quote(db, data, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.get('/{sales_quote_id}', response_model=SalesQuoteResponse)
async def get_sales_quote(
    sales_quote_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    await sales_quote_service.attach_derived(db, quote)
    return SalesQuoteResponse.model_validate(quote)


@router.put('/{sales_quote_id}', response_model=SalesQuoteResponse)
async def update_sales_quote(
    sales_quote_id: int,
    data: SalesQuoteUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    quote = await sales_quote_service.update_quote(db, quote, data, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.post('/{sales_quote_id}/confirm', response_model=SalesQuoteResponse)
async def confirm_sales_quote(
    sales_quote_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    quote = await sales_quote_service.confirm_quote(db, quote, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.post('/{sales_quote_id}/cancel', response_model=SalesQuoteResponse)
async def cancel_sales_quote(
    sales_quote_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    quote = await sales_quote_service.cancel_quote(db, quote, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.post(
    '/{sales_quote_id}/duplicate',
    response_model=SalesQuoteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_sales_quote(
    sales_quote_id: int,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    copy = await sales_quote_service.duplicate_quote(db, quote, current=current)
    return SalesQuoteResponse.model_validate(copy)


@router.post(
    '/{sales_quote_id}/convert',
    response_model=SalesOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
async def convert_sales_quote(
    sales_quote_id: int,
    current: CurrentUser = Depends(_CONVERT),
    db: AsyncSession = Depends(get_db),
) -> SalesOrderResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    order = await sales_quote_service.convert_to_order(db, quote, current=current)
    return SalesOrderResponse.model_validate(order)


@router.post('/{sales_quote_id}/lines', response_model=SalesQuoteResponse)
async def add_sales_quote_line(
    sales_quote_id: int,
    data: SalesQuoteLineCreate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    quote = await sales_quote_service.add_line(db, quote, data, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.put('/{sales_quote_id}/lines/{line_id}', response_model=SalesQuoteResponse)
async def update_sales_quote_line(
    sales_quote_id: int,
    line_id: int,
    data: SalesQuoteLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    line = await sales_quote_service.get_line(db, quote, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    quote = await sales_quote_service.update_line(db, quote, line, data, current=current)
    return SalesQuoteResponse.model_validate(quote)


@router.delete('/{sales_quote_id}/lines/{line_id}', response_model=SalesQuoteResponse)
async def remove_sales_quote_line(
    sales_quote_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SalesQuoteResponse:
    quote = await _quote_or_404(db, sales_quote_id)
    line = await sales_quote_service.get_line(db, quote, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    quote = await sales_quote_service.remove_line(db, quote, line, current=current)
    return SalesQuoteResponse.model_validate(quote)
