from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.inventory import ProductStock, StockRow
from app.services import stock_query_service

router = APIRouter()

_READ = require_privilege(SystemObject.WAREHOUSE_STOCK_REPORT, AccessRight.READ)


@router.get('', response_model=ListResponse[StockRow])
async def list_stock(
    warehouse: int = Query(...),
    q: str | None = Query(None),
    include_zero: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[StockRow]:
    items, total = await stock_query_service.list_stock(
        db, warehouse=warehouse, q=q, include_zero=include_zero, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.get('/products/{product}', response_model=ProductStock)
async def get_product_stock(
    product: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ProductStock:
    return await stock_query_service.product_stock(db, product)
