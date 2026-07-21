from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Facility, Warehouse
from app.schemas.core import WarehouseCreate, WarehouseUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, warehouses: Sequence[Warehouse]) -> None:
    if not warehouses:
        return
    facilities_by_id = await batch_fetch(
        db, Facility, Facility.facility_id, (w.facility for w in warehouses)
    )
    for w in warehouses:
        # Separate key, not `facility`: see the note in facility_service._attach_relations —
        # clobbering the mapped FK breaks WarehouseSummary.facility for point-of-sale responses.
        w.__dict__["facility_detail"] = facilities_by_id.get(w.facility)


async def list_warehouses(
    db: AsyncSession,
    *,
    search: str | None = None,
    facility: int | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Warehouse], int]:
    base = select(Warehouse)
    count_q = select(func.count()).select_from(Warehouse)

    if search:
        term = f"%{search}%"
        condition = or_(Warehouse.code.ilike(term), Warehouse.name.ilike(term))
        base = base.where(condition)
        count_q = count_q.where(condition)

    if facility is not None:
        base = base.where(Warehouse.facility == facility)
        count_q = count_q.where(Warehouse.facility == facility)
    if status is not None:
        base = base.where(Warehouse.status == status)
        count_q = count_q.where(Warehouse.status == status)

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
        facility=data.facility,
        code=data.code,
        name=data.name,
        comment=data.comment,
        status=data.status,
    )
    db.add(warehouse)
    await db.commit()
    await db.refresh(warehouse)
    await _attach_relations(db, [warehouse])
    return warehouse


async def update_warehouse(
    db: AsyncSession, warehouse: Warehouse, data: WarehouseUpdate
) -> Warehouse:
    if data.facility is not None:
        warehouse.facility = data.facility
    if data.code is not None:
        warehouse.code = data.code
    if data.name is not None:
        warehouse.name = data.name
    if data.comment is not None:
        warehouse.comment = data.comment
    if data.status is not None:
        warehouse.status = data.status
    await db.commit()
    await db.refresh(warehouse)
    await _attach_relations(db, [warehouse])
    return warehouse


async def delete_warehouse(db: AsyncSession, warehouse: Warehouse) -> None:
    await db.delete(warehouse)
    await db.commit()
