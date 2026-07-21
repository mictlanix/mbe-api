from collections.abc import Sequence
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import ExchangeRate
from app.schemas.core import ExchangeRateCreate, ExchangeRateUpdate


async def list_exchange_rates(
    db: AsyncSession,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    base: int | None = None,
    target: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[ExchangeRate], int]:
    q = select(ExchangeRate)
    count_q = select(func.count()).select_from(ExchangeRate)

    if date_from is not None:
        q = q.where(ExchangeRate.date >= date_from)
        count_q = count_q.where(ExchangeRate.date >= date_from)
    if date_to is not None:
        q = q.where(ExchangeRate.date <= date_to)
        count_q = count_q.where(ExchangeRate.date <= date_to)
    if base is not None:
        q = q.where(ExchangeRate.base == base)
        count_q = count_q.where(ExchangeRate.base == base)
    if target is not None:
        q = q.where(ExchangeRate.target == target)
        count_q = count_q.where(ExchangeRate.target == target)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(q.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_exchange_rate(db: AsyncSession, exchange_rate_id: int) -> ExchangeRate | None:
    return await db.get(ExchangeRate, exchange_rate_id)


async def create_exchange_rate(db: AsyncSession, data: ExchangeRateCreate) -> ExchangeRate:
    existing = (
        await db.execute(
            select(ExchangeRate).where(
                ExchangeRate.date == data.date,
                ExchangeRate.base == data.base,
                ExchangeRate.target == data.target,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Exchange rate for this date and currency pair already exists',
        )
    er = ExchangeRate(date=data.date, rate=data.rate, base=data.base, target=data.target)
    db.add(er)
    await db.commit()
    await db.refresh(er)
    return er


async def update_exchange_rate(
    db: AsyncSession, er: ExchangeRate, data: ExchangeRateUpdate
) -> ExchangeRate:
    if data.date is not None:
        er.date = data.date
    if data.rate is not None:
        er.rate = data.rate
    if data.base is not None:
        er.base = data.base
    if data.target is not None:
        er.target = data.target
    await db.commit()
    await db.refresh(er)
    return er


async def delete_exchange_rate(db: AsyncSession, er: ExchangeRate) -> None:
    await db.delete(er)
    await db.commit()
