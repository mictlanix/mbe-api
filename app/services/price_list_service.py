from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import PriceList
from app.schemas.product import PriceListCreate, PriceListUpdate
from app.services.references import assert_not_referenced


async def list_price_lists(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PriceList], int]:
    base = select(PriceList)
    count_q = select(func.count()).select_from(PriceList)

    if search:
        term = f'%{search}%'
        base = base.where(PriceList.name.ilike(term))
        count_q = count_q.where(PriceList.name.ilike(term))

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_price_list(db: AsyncSession, price_list_id: int) -> PriceList | None:
    return await db.get(PriceList, price_list_id)


async def create_price_list(db: AsyncSession, data: PriceListCreate) -> PriceList:
    pl = PriceList(
        name=data.name,
        high_profit_margin=data.high_profit_margin,
        low_profit_margin=data.low_profit_margin,
    )
    db.add(pl)
    await db.commit()
    await db.refresh(pl)
    return pl


async def update_price_list(db: AsyncSession, pl: PriceList, data: PriceListUpdate) -> PriceList:
    if data.name is not None:
        pl.name = data.name
    if data.high_profit_margin is not None:
        pl.high_profit_margin = data.high_profit_margin
    if data.low_profit_margin is not None:
        pl.low_profit_margin = data.low_profit_margin
    await db.commit()
    await db.refresh(pl)
    return pl


async def delete_price_list(db: AsyncSession, pl: PriceList) -> None:
    await assert_not_referenced(db, pl)
    await db.delete(pl)
    await db.commit()
