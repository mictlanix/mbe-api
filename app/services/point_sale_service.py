from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Facility, PointSale, Warehouse
from app.schemas.core import PointSaleCreate, PointSaleUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, point_sales: Sequence[PointSale]) -> None:
    if not point_sales:
        return
    facilities_by_id = await batch_fetch(
        db, Facility, Facility.facility_id, (p.facility for p in point_sales)
    )
    warehouses_by_id = await batch_fetch(
        db, Warehouse, Warehouse.warehouse_id, (p.warehouse for p in point_sales)
    )
    for p in point_sales:
        p.__dict__['facility'] = facilities_by_id.get(p.facility)
        p.__dict__['warehouse'] = warehouses_by_id.get(p.warehouse)


async def list_point_sales(
    db: AsyncSession,
    *,
    search: str | None = None,
    facility: int | None = None,
    warehouse: int | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PointSale], int]:
    base = select(PointSale)
    count_q = select(func.count()).select_from(PointSale)
    if search:
        term = f'%{search}%'
        condition = or_(PointSale.code.ilike(term), PointSale.name.ilike(term))
        base = base.where(condition)
        count_q = count_q.where(condition)
    if facility is not None:
        base = base.where(PointSale.facility == facility)
        count_q = count_q.where(PointSale.facility == facility)
    if warehouse is not None:
        base = base.where(PointSale.warehouse == warehouse)
        count_q = count_q.where(PointSale.warehouse == warehouse)
    if status is not None:
        base = base.where(PointSale.status == status)
        count_q = count_q.where(PointSale.status == status)
    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_point_sale(db: AsyncSession, point_sale_id: int) -> PointSale | None:
    ps = await db.get(PointSale, point_sale_id)
    if ps is None:
        return None
    await _attach_relations(db, [ps])
    return ps


async def create_point_sale(db: AsyncSession, data: PointSaleCreate) -> PointSale:
    ps = PointSale(
        facility=data.facility,
        code=data.code,
        name=data.name,
        warehouse=data.warehouse,
        comment=data.comment,
        status=data.status,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    await _attach_relations(db, [ps])
    return ps


async def update_point_sale(db: AsyncSession, ps: PointSale, data: PointSaleUpdate) -> PointSale:
    if data.facility is not None:
        ps.facility = data.facility
    if data.code is not None:
        ps.code = data.code
    if data.name is not None:
        ps.name = data.name
    if data.warehouse is not None:
        ps.warehouse = data.warehouse
    if data.comment is not None:
        ps.comment = data.comment
    if data.status is not None:
        ps.status = data.status
    await db.commit()
    await db.refresh(ps)
    await _attach_relations(db, [ps])
    return ps


async def delete_point_sale(db: AsyncSession, ps: PointSale) -> None:
    await db.delete(ps)
    await db.commit()
