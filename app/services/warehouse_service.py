from collections.abc import Iterable, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.enums import EntityStatus
from app.models.core import Facility, Warehouse
from app.schemas.core import WarehouseCreate, WarehouseUpdate
from app.services.fk_expansion import batch_fetch
from app.services.references import assert_not_referenced, assert_unique


async def _attach_relations(db: AsyncSession, warehouses: Sequence[Warehouse]) -> None:
    if not warehouses:
        return
    facilities_by_id = await batch_fetch(
        db, Facility, Facility.facility_id, (w.facility for w in warehouses)
    )
    for w in warehouses:
        # Separate key, not `facility`: see the note in facility_service._attach_relations —
        # clobbering the mapped FK breaks WarehouseSummary.facility for point-of-sale responses.
        w.__dict__['facility_detail'] = facilities_by_id.get(w.facility)


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

    # In-transit locations are warehouse rows so `stock_ledger.on_hand` reports their balances
    # with no new mechanism (spec 012, research R3). They are not places anyone picks from, so
    # they are kept out of every picker: choosing one on a sales order or an itinerary would
    # misfile stock into a virtual location. There is now one per facility, so this excludes all
    # of them by flag rather than the single configured id spec 012 had (spec 013, FR-012).
    virtual = Warehouse.in_transit.is_(False)
    base = base.where(virtual)
    count_q = count_q.where(virtual)

    if search:
        term = f'%{search}%'
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
    # Deliberately returns in-transit rows too. The refusal is the endpoint's job (403, not 404),
    # because "forbidden" and "not found" have to stay distinguishable — and a service function
    # that pretends a row does not exist is a trap for every future caller (spec 013, research R4).
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None:
        return None
    await _attach_relations(db, [warehouse])
    return warehouse


async def get_transit_warehouse(db: AsyncSession, facility: int) -> Warehouse | None:
    """The in-transit location belonging to `facility`, or `None` when it has none.

    `None` is a broken state, not a normal one: every facility gets one at creation and the
    migration backfilled the rest. Callers refuse rather than repairing it — the system never
    creates one on demand (FR-009a).
    """
    return (
        await db.execute(
            select(Warehouse).where(Warehouse.facility == facility, Warehouse.in_transit.is_(True))
        )
    ).scalar_one_or_none()


async def transit_warehouses_for(
    db: AsyncSession, dispatch_warehouses: Iterable[int]
) -> dict[int, int]:
    """Map each dispatch warehouse id to the in-transit warehouse id of the facility owning it.

    One self-join for the whole trip, not one lookup per line — the N+1 that `fk_expansion`
    exists to police. A dispatch warehouse missing from the result means its facility has no
    in-transit location; the caller raises rather than posting the movement elsewhere (FR-009).
    """
    ids = set(dispatch_warehouses)
    if not ids:
        return {}

    dispatch = aliased(Warehouse)
    transit = aliased(Warehouse)
    rows = (
        await db.execute(
            select(dispatch.warehouse_id, transit.warehouse_id)
            .join(
                transit,
                (transit.facility == dispatch.facility) & transit.in_transit.is_(True),
            )
            .where(dispatch.warehouse_id.in_(ids))
        )
    ).all()
    return {dispatch_id: transit_id for dispatch_id, transit_id in rows}


async def create_warehouse(db: AsyncSession, data: WarehouseCreate) -> Warehouse:
    await assert_unique(db, Warehouse, Warehouse.code, data.code, label='Warehouse code')
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
    if data.code is not None:
        await assert_unique(
            db,
            Warehouse,
            Warehouse.code,
            data.code,
            exclude_pk=warehouse.warehouse_id,
            label='Warehouse code',
        )
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
    await assert_not_referenced(db, warehouse)
    await db.delete(warehouse)
    await db.commit()
