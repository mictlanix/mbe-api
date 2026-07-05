from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import CashDrawer, Store
from app.schemas.core import CashDrawerCreate, CashDrawerUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, cash_drawers: Sequence[CashDrawer]) -> None:
    if not cash_drawers:
        return
    stores_by_id = await batch_fetch(db, Store, Store.store_id, (c.store for c in cash_drawers))
    for c in cash_drawers:
        c.__dict__["store"] = stores_by_id.get(c.store)


async def list_cash_drawers(
    db: AsyncSession,
    *,
    store: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CashDrawer], int]:
    base = select(CashDrawer)
    count_q = select(func.count()).select_from(CashDrawer)
    if store is not None:
        base = base.where(CashDrawer.store == store)
        count_q = count_q.where(CashDrawer.store == store)
    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_cash_drawer(db: AsyncSession, cash_drawer_id: int) -> CashDrawer | None:
    cd = await db.get(CashDrawer, cash_drawer_id)
    if cd is None:
        return None
    await _attach_relations(db, [cd])
    return cd


async def create_cash_drawer(db: AsyncSession, data: CashDrawerCreate) -> CashDrawer:
    cd = CashDrawer(
        store=data.store,
        code=data.code,
        name=data.name,
        comment=data.comment,
        disabled=data.disabled,
    )
    db.add(cd)
    await db.commit()
    await db.refresh(cd)
    await _attach_relations(db, [cd])
    return cd


async def update_cash_drawer(
    db: AsyncSession, cd: CashDrawer, data: CashDrawerUpdate
) -> CashDrawer:
    if data.store is not None:
        cd.store = data.store
    if data.code is not None:
        cd.code = data.code
    if data.name is not None:
        cd.name = data.name
    if data.comment is not None:
        cd.comment = data.comment
    if data.disabled is not None:
        cd.disabled = data.disabled
    await db.commit()
    await db.refresh(cd)
    await _attach_relations(db, [cd])
    return cd


async def delete_cash_drawer(db: AsyncSession, cd: CashDrawer) -> None:
    await db.delete(cd)
    await db.commit()
