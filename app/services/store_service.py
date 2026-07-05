from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Store
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import StoreCreate, StoreUpdate
from app.services.fk_expansion import batch_fetch
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, stores: Sequence[Store]) -> None:
    if not stores:
        return
    postal_config = SAT_CATALOG_MAP["postal-codes"]
    postal_codes_by_id = await batch_fetch(
        db, SatPostalCode, SatPostalCode.sat_postal_code_id, (s.location for s in stores)
    )
    for s in stores:
        postal_row = postal_codes_by_id.get(s.location)
        s.__dict__["location"] = to_response(postal_row, postal_config) if postal_row else None


async def list_stores(
    db: AsyncSession, *, skip: int = 0, limit: int = 20
) -> tuple[Sequence[Store], int]:
    total: int = (await db.execute(select(func.count()).select_from(Store))).scalar_one()
    items = (await db.execute(select(Store).offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_store(db: AsyncSession, store_id: int) -> Store | None:
    store = await db.get(Store, store_id)
    if store is None:
        return None
    await _attach_relations(db, [store])
    return store


async def create_store(db: AsyncSession, data: StoreCreate) -> Store:
    store = Store(
        code=data.code,
        name=data.name,
        location=data.location,
        address=data.address,
        taxpayer=data.taxpayer,
        logo=data.logo,
        receipt_message=data.receipt_message,
        default_batch=data.default_batch,
        disabled=data.disabled,
    )
    db.add(store)
    await db.commit()
    await db.refresh(store)
    await _attach_relations(db, [store])
    return store


async def update_store(db: AsyncSession, store: Store, data: StoreUpdate) -> Store:
    if data.code is not None:
        store.code = data.code
    if data.name is not None:
        store.name = data.name
    if data.location is not None:
        store.location = data.location
    if data.address is not None:
        store.address = data.address
    if data.taxpayer is not None:
        store.taxpayer = data.taxpayer
    if data.logo is not None:
        store.logo = data.logo
    if data.receipt_message is not None:
        store.receipt_message = data.receipt_message
    if data.default_batch is not None:
        store.default_batch = data.default_batch
    if data.disabled is not None:
        store.disabled = data.disabled
    await db.commit()
    await db.refresh(store)
    await _attach_relations(db, [store])
    return store


async def delete_store(db: AsyncSession, store: Store) -> None:
    await db.delete(store)
    await db.commit()
