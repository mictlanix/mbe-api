from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.product_price import ProductPriceCreate, ProductPriceResponse, ProductPriceUpdate
from app.services import product_price_service

router = APIRouter()


@router.get("", response_model=ListResponse[ProductPriceResponse])
async def list_product_prices(
    product: int | None = Query(None),
    price_list: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRICING, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ProductPriceResponse]:
    items, total = await product_price_service.list_product_prices(
        db, product=product, price_list=price_list, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=ProductPriceResponse, status_code=status.HTTP_201_CREATED)
async def create_product_price(
    data: ProductPriceCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRICING, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> ProductPriceResponse:
    pp = await product_price_service.create_product_price(db, data)
    return ProductPriceResponse.model_validate(pp)


@router.get("/{product_price_id}", response_model=ProductPriceResponse)
async def get_product_price(
    product_price_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRICING, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ProductPriceResponse:
    pp = await product_price_service.get_product_price(db, product_price_id)
    if pp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product price not found"
        )
    return ProductPriceResponse.model_validate(pp)


@router.put("/{product_price_id}", response_model=ProductPriceResponse)
async def update_product_price(
    product_price_id: int,
    data: ProductPriceUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRICING, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ProductPriceResponse:
    pp = await product_price_service.get_product_price(db, product_price_id)
    if pp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product price not found"
        )
    pp = await product_price_service.update_product_price(db, pp, data)
    return ProductPriceResponse.model_validate(pp)


@router.delete("/{product_price_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product_price(
    product_price_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRICING, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    pp = await product_price_service.get_product_price(db, product_price_id)
    if pp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Product price not found"
        )
    await product_price_service.delete_product_price(db, pp)
