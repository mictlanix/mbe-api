from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Facility
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import FacilityCreate, FacilityUpdate
from app.services.fk_expansion import batch_fetch
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, facilities: Sequence[Facility]) -> None:
    if not facilities:
        return
    postal_config = SAT_CATALOG_MAP["postal-codes"]
    postal_codes_by_id = await batch_fetch(
        db, SatPostalCode, SatPostalCode.sat_postal_code_id, (f.location for f in facilities)
    )
    for f in facilities:
        postal_row = postal_codes_by_id.get(f.location)
        f.__dict__["location"] = to_response(postal_row, postal_config) if postal_row else None


async def list_facilities(
    db: AsyncSession, *, skip: int = 0, limit: int = 20
) -> tuple[Sequence[Facility], int]:
    total: int = (await db.execute(select(func.count()).select_from(Facility))).scalar_one()
    items = (await db.execute(select(Facility).offset(skip).limit(limit))).scalars().all()
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
        disabled=data.disabled,
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
    if data.disabled is not None:
        facility.disabled = data.disabled
    await db.commit()
    await db.refresh(facility)
    await _attach_relations(db, [facility])
    return facility


async def delete_facility(db: AsyncSession, facility: Facility) -> None:
    await db.delete(facility)
    await db.commit()
