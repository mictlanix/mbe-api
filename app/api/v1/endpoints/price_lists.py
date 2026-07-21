from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.product import PriceListCreate, PriceListResponse, PriceListUpdate
from app.services import price_list_service

router = APIRouter()


@router.get('', response_model=ListResponse[PriceListResponse])
async def list_price_lists(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PriceListResponse]:
    items, total = await price_list_service.list_price_lists(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=PriceListResponse, status_code=status.HTTP_201_CREATED)
async def create_price_list(
    data: PriceListCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PriceListResponse:
    pl = await price_list_service.create_price_list(db, data)
    return PriceListResponse.model_validate(pl)


@router.get('/{price_list_id}', response_model=PriceListResponse)
async def get_price_list(
    price_list_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PriceListResponse:
    pl = await price_list_service.get_price_list(db, price_list_id)
    if pl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Price list not found')
    return PriceListResponse.model_validate(pl)


@router.put('/{price_list_id}', response_model=PriceListResponse)
async def update_price_list(
    price_list_id: int,
    data: PriceListUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PriceListResponse:
    pl = await price_list_service.get_price_list(db, price_list_id)
    if pl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Price list not found')
    pl = await price_list_service.update_price_list(db, pl, data)
    return PriceListResponse.model_validate(pl)


@router.delete('/{price_list_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_price_list(
    price_list_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    pl = await price_list_service.get_price_list(db, price_list_id)
    if pl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Price list not found')
    await price_list_service.delete_price_list(db, pl)
