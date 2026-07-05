from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import ProductionSite, Store
from app.schemas.core import ProductionSiteCreate, ProductionSiteUpdate
from app.services.fk_expansion import batch_fetch


async def _attach_relations(db: AsyncSession, sites: Sequence[ProductionSite]) -> None:
    if not sites:
        return
    stores_by_id = await batch_fetch(db, Store, Store.store_id, (s.store for s in sites))
    for s in sites:
        s.__dict__["store"] = stores_by_id.get(s.store)


async def list_production_sites(
    db: AsyncSession,
    *,
    store: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[ProductionSite], int]:
    base = select(ProductionSite)
    count_q = select(func.count()).select_from(ProductionSite)

    if store is not None:
        base = base.where(ProductionSite.store == store)
        count_q = count_q.where(ProductionSite.store == store)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_production_site(db: AsyncSession, production_site_id: int) -> ProductionSite | None:
    ps = await db.get(ProductionSite, production_site_id)
    if ps is None:
        return None
    await _attach_relations(db, [ps])
    return ps


async def create_production_site(db: AsyncSession, data: ProductionSiteCreate) -> ProductionSite:
    ps = ProductionSite(
        store=data.store,
        code=data.code,
        name=data.name,
        comment=data.comment,
        disabled=int(data.disabled) if data.disabled is not None else None,
    )
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    await _attach_relations(db, [ps])
    return ps


async def update_production_site(
    db: AsyncSession, ps: ProductionSite, data: ProductionSiteUpdate
) -> ProductionSite:
    if data.store is not None:
        ps.store = data.store
    if data.code is not None:
        ps.code = data.code
    if data.name is not None:
        ps.name = data.name
    if data.comment is not None:
        ps.comment = data.comment
    if data.disabled is not None:
        ps.disabled = int(data.disabled)
    await db.commit()
    await db.refresh(ps)
    await _attach_relations(db, [ps])
    return ps


async def delete_production_site(db: AsyncSession, ps: ProductionSite) -> None:
    await db.delete(ps)
    await db.commit()
