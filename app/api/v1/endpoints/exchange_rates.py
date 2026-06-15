from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.core import ExchangeRateCreate, ExchangeRateResponse, ExchangeRateUpdate
from app.services import exchange_rate_service

router = APIRouter()


@router.get("", response_model=ListResponse[ExchangeRateResponse])
async def list_exchange_rates(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    base: int | None = Query(None),
    target: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ExchangeRateResponse]:
    items, total = await exchange_rate_service.list_exchange_rates(
        db, date_from=date_from, date_to=date_to, base=base, target=target, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=ExchangeRateResponse, status_code=status.HTTP_201_CREATED)
async def create_exchange_rate(
    data: ExchangeRateCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExchangeRateResponse:
    er = await exchange_rate_service.create_exchange_rate(db, data)
    return ExchangeRateResponse.model_validate(er)


@router.get("/{exchange_rate_id}", response_model=ExchangeRateResponse)
async def get_exchange_rate(
    exchange_rate_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExchangeRateResponse:
    er = await exchange_rate_service.get_exchange_rate(db, exchange_rate_id)
    if er is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exchange rate not found")
    return ExchangeRateResponse.model_validate(er)


@router.put("/{exchange_rate_id}", response_model=ExchangeRateResponse)
async def update_exchange_rate(
    exchange_rate_id: int,
    data: ExchangeRateUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ExchangeRateResponse:
    er = await exchange_rate_service.get_exchange_rate(db, exchange_rate_id)
    if er is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exchange rate not found")
    er = await exchange_rate_service.update_exchange_rate(db, er, data)
    return ExchangeRateResponse.model_validate(er)


@router.delete("/{exchange_rate_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_exchange_rate(
    exchange_rate_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    er = await exchange_rate_service.get_exchange_rate(db, exchange_rate_id)
    if er is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exchange rate not found")
    await exchange_rate_service.delete_exchange_rate(db, er)
