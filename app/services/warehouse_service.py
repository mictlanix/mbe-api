from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Store, Warehouse
from app.schemas.core import WarehouseCreate, WarehouseUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, warehouses: Sequence[Warehouse]) -> None:
    if not warehouses:
        return
    stores_by_id = await batch_fetch(db, Store, Store.store_id, (w.store for w in warehouses))
    for w in warehouses:
        w.__dict__["store"] = stores_by_id.get(w.store)


async def list_warehouses(
    db: AsyncSession,
    *,
    store: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Warehouse], int]:
    base = select(Warehouse)
    count_q = select(func.count()).select_from(Warehouse)

    if store is not None:
        base = base.where(Warehouse.store == store)
        count_q = count_q.where(Warehouse.store == store)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_warehouse(db: AsyncSession, warehouse_id: int) -> Warehouse | None:
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None:
        return None
    await _attach_relations(db, [warehouse])
    return warehouse


async def create_warehouse(db: AsyncSession, data: WarehouseCreate) -> Warehouse:
    warehouse = Warehouse(
        store=data.store,
        code=data.code,
        name=data.name,
        comment=data.comment,
        disabled=int(data.disabled) if data.disabled is not None else None,
    )
    db.add(warehouse)
    await db.commit()
    await db.refresh(warehouse)
    await _attach_relations(db, [warehouse])
    return warehouse


async def update_warehouse(
    db: AsyncSession, warehouse: Warehouse, data: WarehouseUpdate
) -> Warehouse:
    if data.store is not None:
        warehouse.store = data.store
    if data.code is not None:
        warehouse.code = data.code
    if data.name is not None:
        warehouse.name = data.name
    if data.comment is not None:
        warehouse.comment = data.comment
    if data.disabled is not None:
        warehouse.disabled = int(data.disabled)
    await db.commit()
    await db.refresh(warehouse)
    await _attach_relations(db, [warehouse])
    return warehouse


async def delete_warehouse(db: AsyncSession, warehouse: Warehouse) -> None:
    await db.delete(warehouse)
    await db.commit()
