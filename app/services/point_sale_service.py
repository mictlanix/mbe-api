from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import PointSale
from app.schemas.core import PointSaleCreate, PointSaleUpdate


async def list_point_sales(
    db: AsyncSession,
    *,
    store: int | None = None,
    warehouse: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PointSale], int]:
    base = select(PointSale)
    count_q = select(func.count()).select_from(PointSale)
    if store is not None:
        base = base.where(PointSale.store == store)
        count_q = count_q.where(PointSale.store == store)
    if warehouse is not None:
        base = base.where(PointSale.warehouse == warehouse)
        count_q = count_q.where(PointSale.warehouse == warehouse)
    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_point_sale(db: AsyncSession, point_sale_id: int) -> PointSale | None:
    return await db.get(PointSale, point_sale_id)


async def create_point_sale(db: AsyncSession, data: PointSaleCreate) -> PointSale:
    ps = PointSale(
        store=data.store,
        code=data.code,
        name=data.name,
        warehouse=data.warehouse,
        comment=data.comment,
        disabled=data.disabled,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    return ps


async def update_point_sale(db: AsyncSession, ps: PointSale, data: PointSaleUpdate) -> PointSale:
    if data.store is not None:
        ps.store = data.store
    if data.code is not None:
        ps.code = data.code
    if data.name is not None:
        ps.name = data.name
    if data.warehouse is not None:
        ps.warehouse = data.warehouse
    if data.comment is not None:
        ps.comment = data.comment
    if data.disabled is not None:
        ps.disabled = data.disabled
    await db.commit()
    await db.refresh(ps)
    return ps


async def delete_point_sale(db: AsyncSession, ps: PointSale) -> None:
    await db.delete(ps)
    await db.commit()
