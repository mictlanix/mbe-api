from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.product import (
    ProductCreate,
    ProductListItem,
    ProductMergeRequest,
    ProductResponse,
    ProductUpdate,
)
from app.services import product_service

router = APIRouter()


@router.get("", response_model=ListResponse[ProductListItem])
async def list_products(
    search: str | None = Query(None),
    label: int | None = Query(None),
    deactivated: bool | None = Query(None),
    stockable: bool | None = Query(None),
    salable: bool | None = Query(None),
    purchasable: bool | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ProductListItem]:
    items, total = await product_service.list_products(
        db,
        search=search,
        label=label,
        deactivated=deactivated,
        stockable=stockable,
        salable=salable,
        purchasable=purchasable,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.create_product(db, data, settings)
    return ProductResponse.model_validate(product)


@router.post("/merge", status_code=status.HTTP_204_NO_CONTENT)
async def merge_products(
    data: ProductMergeRequest,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await product_service.merge_products(db, data)


@router.get("/{product_id}", response_model=ProductResponse)
async def get_product(
    product_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    return ProductResponse.model_validate(product)


@router.put("/{product_id}", response_model=ProductResponse)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    product = await product_service.update_product(db, product, data)
    return ProductResponse.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    await product_service.delete_product(db, product)
