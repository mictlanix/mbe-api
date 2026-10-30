from fastapi import APIRouter, Depends, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.core.errors import not_found
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.models.core import PointSale
from app.schemas import ListResponse
from app.schemas.core import PointSaleCreate, PointSaleResponse, PointSaleUpdate
from app.services import point_sale_service

router = APIRouter()


@router.get('', response_model=ListResponse[PointSaleResponse])
async def list_points_of_sale(
    search: str | None = Query(None),
    facility: int | None = Query(None),
    warehouse: int | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.POINTS_OF_SALE, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PointSaleResponse]:
    items, total = await point_sale_service.list_point_sales(
        db,
        search=search,
        facility=facility,
        warehouse=warehouse,
        status=status,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=PointSaleResponse, status_code=http_status.HTTP_201_CREATED)
async def create_point_of_sale(
    data: PointSaleCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.POINTS_OF_SALE, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> PointSaleResponse:
    ps = await point_sale_service.create_point_sale(db, data)
    return PointSaleResponse.model_validate(ps)


@router.get('/{point_sale_id}', response_model=PointSaleResponse)
async def get_point_of_sale(
    point_sale_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.POINTS_OF_SALE, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> PointSaleResponse:
    ps = await point_sale_service.get_point_sale(db, point_sale_id)
    if ps is None:
        raise not_found(PointSale, point_sale_id)
    return PointSaleResponse.model_validate(ps)


@router.put('/{point_sale_id}', response_model=PointSaleResponse)
async def update_point_of_sale(
    point_sale_id: int,
    data: PointSaleUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.POINTS_OF_SALE, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> PointSaleResponse:
    ps = await point_sale_service.get_point_sale(db, point_sale_id)
    if ps is None:
        raise not_found(PointSale, point_sale_id)
    ps = await point_sale_service.update_point_sale(db, ps, data)
    return PointSaleResponse.model_validate(ps)


@router.delete('/{point_sale_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_point_of_sale(
    point_sale_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.POINTS_OF_SALE, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    ps = await point_sale_service.get_point_sale(db, point_sale_id)
    if ps is None:
        raise not_found(PointSale, point_sale_id)
    await point_sale_service.delete_point_sale(db, ps)
