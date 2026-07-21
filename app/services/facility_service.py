from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.core import Facility
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import FacilityCreate, FacilityUpdate
from app.services.fk_expansion import batch_fetch
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, facilities: Sequence[Facility]) -> None:
    if not facilities:
        return
    postal_config = SAT_CATALOG_MAP['postal-codes']
    postal_codes_by_id = await batch_fetch(
        db, SatPostalCode, SatPostalCode.sat_postal_code_id, (f.location for f in facilities)
    )
    for f in facilities:
        postal_row = postal_codes_by_id.get(f.location)
        # Written under a separate key: `location` is a mapped column and these instances are
        # shared through the session identity map, so overwriting it corrupts every other
        # response that reads the raw FK (FacilitySummary.location).
        f.__dict__['location_detail'] = (
            to_response(postal_row, postal_config) if postal_row else None
        )


async def list_facilities(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: EntityStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Facility], int]:
    base = select(Facility)
    count_q = select(func.count()).select_from(Facility)

    if search:
        term = f'%{search}%'
        condition = or_(Facility.code.ilike(term), Facility.name.ilike(term))
        base = base.where(condition)
        count_q = count_q.where(condition)

    if status is not None:
        base = base.where(Facility.status == status)
        count_q = count_q.where(Facility.status == status)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_relations(db, items)
    return items, total


async def get_facility(db: AsyncSession, facility_id: int) -> Facility | None:
    facility = await db.get(Facility, facility_id)
    if facility is None:
        return None
    await _attach_relations(db, [facility])
    return facility


async def create_facility(db: AsyncSession, data: FacilityCreate) -> Facility:
    facility = Facility(
        code=data.code,
        name=data.name,
        type=data.type,
        location=data.location,
        address=data.address,
        taxpayer=data.taxpayer,
        logo=data.logo,
        receipt_message=data.receipt_message,
        default_batch=data.default_batch,
        status=data.status,
    )
    db.add(facility)
    await db.commit()
    await db.refresh(facility)
    await _attach_relations(db, [facility])
    return facility


async def update_facility(db: AsyncSession, facility: Facility, data: FacilityUpdate) -> Facility:
    if data.code is not None:
        facility.code = data.code
    if data.name is not None:
        facility.name = data.name
    if data.type is not None:
        facility.type = data.type
    if data.location is not None:
        facility.location = data.location
    if data.address is not None:
        facility.address = data.address
    if data.taxpayer is not None:
        facility.taxpayer = data.taxpayer
    if data.logo is not None:
        facility.logo = data.logo
    if data.receipt_message is not None:
        facility.receipt_message = data.receipt_message
    if data.default_batch is not None:
        facility.default_batch = data.default_batch
    if data.status is not None:
        facility.status = data.status
    await db.commit()
    await db.refresh(facility)
    await _attach_relations(db, [facility])
    return facility


async def delete_facility(db: AsyncSession, facility: Facility) -> None:
    await db.delete(facility)
    await db.commit()
