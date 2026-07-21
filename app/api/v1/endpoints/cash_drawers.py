from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.core import CashDrawerCreate, CashDrawerResponse, CashDrawerUpdate
from app.services import cash_drawer_service

router = APIRouter()


@router.get('', response_model=ListResponse[CashDrawerResponse])
async def list_cash_drawers(
    search: str | None = Query(None),
    facility: int | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.CASH_DRAWERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[CashDrawerResponse]:
    items, total = await cash_drawer_service.list_cash_drawers(
        db, search=search, facility=facility, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=CashDrawerResponse, status_code=http_status.HTTP_201_CREATED)
async def create_cash_drawer(
    data: CashDrawerCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.CASH_DRAWERS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> CashDrawerResponse:
    cd = await cash_drawer_service.create_cash_drawer(db, data)
    return CashDrawerResponse.model_validate(cd)


@router.get('/{cash_drawer_id}', response_model=CashDrawerResponse)
async def get_cash_drawer(
    cash_drawer_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.CASH_DRAWERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> CashDrawerResponse:
    cd = await cash_drawer_service.get_cash_drawer(db, cash_drawer_id)
    if cd is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Cash drawer not found'
        )
    return CashDrawerResponse.model_validate(cd)


@router.put('/{cash_drawer_id}', response_model=CashDrawerResponse)
async def update_cash_drawer(
    cash_drawer_id: int,
    data: CashDrawerUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.CASH_DRAWERS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> CashDrawerResponse:
    cd = await cash_drawer_service.get_cash_drawer(db, cash_drawer_id)
    if cd is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Cash drawer not found'
        )
    cd = await cash_drawer_service.update_cash_drawer(db, cd, data)
    return CashDrawerResponse.model_validate(cd)


@router.delete('/{cash_drawer_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_cash_drawer(
    cash_drawer_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.CASH_DRAWERS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    cd = await cash_drawer_service.get_cash_drawer(db, cash_drawer_id)
    if cd is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Cash drawer not found'
        )
    await cash_drawer_service.delete_cash_drawer(db, cd)
