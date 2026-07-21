from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.core import WarehouseCreate, WarehouseResponse, WarehouseUpdate
from app.services import warehouse_service

router = APIRouter()


@router.get("", response_model=ListResponse[WarehouseResponse])
async def list_warehouses(
    search: str | None = Query(None),
    facility: int | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.WAREHOUSES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[WarehouseResponse]:
    items, total = await warehouse_service.list_warehouses(
        db, search=search, facility=facility, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=WarehouseResponse, status_code=http_status.HTTP_201_CREATED)
async def create_warehouse(
    data: WarehouseCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.WAREHOUSES, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> WarehouseResponse:
    warehouse = await warehouse_service.create_warehouse(db, data)
    return WarehouseResponse.model_validate(warehouse)


@router.get("/{warehouse_id}", response_model=WarehouseResponse)
async def get_warehouse(
    warehouse_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.WAREHOUSES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> WarehouseResponse:
    warehouse = await warehouse_service.get_warehouse(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Warehouse not found"
        )
    return WarehouseResponse.model_validate(warehouse)


@router.put("/{warehouse_id}", response_model=WarehouseResponse)
async def update_warehouse(
    warehouse_id: int,
    data: WarehouseUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.WAREHOUSES, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> WarehouseResponse:
    warehouse = await warehouse_service.get_warehouse(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Warehouse not found"
        )
    warehouse = await warehouse_service.update_warehouse(db, warehouse, data)
    return WarehouseResponse.model_validate(warehouse)


@router.delete("/{warehouse_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_warehouse(
    warehouse_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.WAREHOUSES, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    warehouse = await warehouse_service.get_warehouse(db, warehouse_id)
    if warehouse is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Warehouse not found"
        )
    await warehouse_service.delete_warehouse(db, warehouse)
