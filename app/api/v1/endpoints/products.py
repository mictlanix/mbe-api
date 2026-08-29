from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.product import (
    ProductCreate,
    ProductLabelFacet,
    ProductListItem,
    ProductMergePreviewItem,
    ProductMergePreviewResponse,
    ProductMergeRequest,
    ProductMissingPriceFacet,
    ProductResponse,
    ProductUpdate,
)
from app.services import image_service, product_service

router = APIRouter()


def _photo_url(filename: str | None) -> str | None:
    return image_service.image_url(filename)


@router.get('', response_model=ListResponse[ProductListItem])
async def list_products(
    search: str | None = Query(None),
    label: list[int] | None = Query(None),
    status: EntityStatus | None = Query(None),
    stockable: bool | None = Query(None),
    salable: bool | None = Query(None),
    purchasable: bool | None = Query(None),
    supplier: int | None = Query(None),
    missing_price_list: int | None = Query(
        None, description='Only products with no price on this price list (#184)'
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ProductListItem]:
    items, total = await product_service.list_products(
        db,
        search=search,
        label=label,
        status=status,
        stockable=stockable,
        salable=salable,
        purchasable=purchasable,
        supplier=supplier,
        missing_price_list=missing_price_list,
        skip=skip,
        limit=limit,
    )
    responses = []
    for item in items:
        response = ProductListItem.model_validate(item)
        response.photo = _photo_url(item.photo)
        responses.append(response)
    return ListResponse(items=responses, total=total)


@router.get('/labels/facets', response_model=list[ProductLabelFacet])
async def get_product_label_facets(
    search: str | None = Query(None),
    label: list[int] | None = Query(None),
    status: EntityStatus | None = Query(None),
    stockable: bool | None = Query(None),
    salable: bool | None = Query(None),
    purchasable: bool | None = Query(None),
    supplier: int | None = Query(None),
    missing_price_list: int | None = Query(
        None, description='Only products with no price on this price list (#184)'
    ),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> list[ProductLabelFacet]:
    rows = await product_service.get_label_facets(
        db,
        search=search,
        label=label,
        status=status,
        stockable=stockable,
        salable=salable,
        purchasable=purchasable,
        supplier=supplier,
        missing_price_list=missing_price_list,
    )
    return [ProductLabelFacet(label_id=row.label_id, count=row.count) for row in rows]


@router.get('/prices/missing-facets', response_model=list[ProductMissingPriceFacet])
async def get_product_missing_price_facets(
    search: str | None = Query(None),
    label: list[int] | None = Query(None),
    status: EntityStatus | None = Query(None),
    stockable: bool | None = Query(None),
    salable: bool | None = Query(None),
    purchasable: bool | None = Query(None),
    supplier: int | None = Query(None),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> list[ProductMissingPriceFacet]:
    """One row per price list: how many matching products still have no price on it (#184).

    Read by the same privilege as the products list rather than by `PRICING`, because that is
    what it counts — products, narrowed by the product filters, with the price lists supplying
    only the columns to count against.
    """
    rows = await product_service.get_missing_price_facets(
        db,
        search=search,
        label=label,
        status=status,
        stockable=stockable,
        salable=salable,
        purchasable=purchasable,
        supplier=supplier,
    )
    return [
        ProductMissingPriceFacet(price_list=price_list, missing_count=missing)
        for price_list, missing in rows
    ]


@router.post('', response_model=ProductResponse, status_code=http_status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.create_product(db, data, settings)
    response = ProductResponse.model_validate(product)
    response.photo = _photo_url(product.photo)
    return response


@router.post('/merge', status_code=http_status.HTTP_204_NO_CONTENT)
async def merge_products(
    data: ProductMergeRequest,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    await product_service.merge_products(db, data)


@router.get('/merge/preview', response_model=ProductMergePreviewResponse)
async def preview_product_merge(
    product_id: int = Query(...),
    duplicate_id: int = Query(...),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ProductMergePreviewResponse:
    references = await product_service.preview_merge(
        db, ProductMergeRequest(product_id=product_id, duplicate_id=duplicate_id)
    )
    items = [ProductMergePreviewItem(category=name, count=count) for name, count in references]
    return ProductMergePreviewResponse(items=items, total=sum(item.count for item in items))


@router.post('/{product_id}/image', response_model=ProductResponse)
async def upload_product_image(
    product_id: int,
    file: UploadFile = File(...),
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Product not found')
    content = await file.read()
    try:
        filename = await image_service.process_and_save_image(content, settings.images_dir)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    product = await product_service.update_product(db, product, ProductUpdate(photo=filename))
    response = ProductResponse.model_validate(product)
    response.photo = _photo_url(product.photo)
    return response


@router.get('/{product_id}', response_model=ProductResponse)
async def get_product(
    product_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Product not found')
    response = ProductResponse.model_validate(product)
    response.photo = _photo_url(product.photo)
    return response


@router.put('/{product_id}', response_model=ProductResponse)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ProductResponse:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Product not found')
    product = await product_service.update_product(db, product, data)
    response = ProductResponse.model_validate(product)
    response.photo = _photo_url(product.photo)
    return response


@router.delete('/{product_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.PRODUCTS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    product = await product_service.get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Product not found')
    await product_service.delete_product(db, product)
