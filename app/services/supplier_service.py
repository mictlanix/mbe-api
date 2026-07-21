from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.supplier import Supplier
from app.schemas.supplier import SupplierCreate, SupplierUpdate
from app.services.references import assert_not_referenced


async def list_suppliers(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Supplier], int]:
    base = select(Supplier)
    count_q = select(func.count()).select_from(Supplier)

    if search:
        term = f'%{search}%'
        condition = or_(
            Supplier.code.ilike(term),
            Supplier.name.ilike(term),
            Supplier.zone.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_supplier(db: AsyncSession, supplier_id: int) -> Supplier | None:
    return await db.get(Supplier, supplier_id)


async def create_supplier(db: AsyncSession, data: SupplierCreate) -> Supplier:
    supplier = Supplier(
        code=data.code,
        name=data.name,
        zone=data.zone,
        credit_limit=data.credit_limit,
        credit_days=data.credit_days,
        comment=data.comment,
    )
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def update_supplier(db: AsyncSession, supplier: Supplier, data: SupplierUpdate) -> Supplier:
    if data.code is not None:
        supplier.code = data.code
    if data.name is not None:
        supplier.name = data.name
    if data.zone is not None:
        supplier.zone = data.zone
    if data.credit_limit is not None:
        supplier.credit_limit = data.credit_limit
    if data.credit_days is not None:
        supplier.credit_days = data.credit_days
    if data.comment is not None:
        supplier.comment = data.comment
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def delete_supplier(db: AsyncSession, supplier: Supplier) -> None:
    await assert_not_referenced(db, supplier)
    await db.delete(supplier)
    await db.commit()
