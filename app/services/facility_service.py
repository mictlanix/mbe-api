from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import EntityStatus, SourceType
from app.models.core import Address, Facility, Warehouse
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import FacilityCreate, FacilityUpdate
from app.services import incidences, warehouse_service
from app.services.fk_expansion import batch_fetch
from app.services.references import assert_not_referenced
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def _attach_relations(db: AsyncSession, facilities: Sequence[Facility]) -> None:
    if not facilities:
        return
    postal_config = SAT_CATALOG_MAP['postal-codes']
    postal_codes_by_id = await batch_fetch(
        db, SatPostalCode, SatPostalCode.sat_postal_code_id, (f.location for f in facilities)
    )
    addresses_by_id = await batch_fetch(
        db, Address, Address.address_id, (f.address for f in facilities)
    )
    for f in facilities:
        postal_row = postal_codes_by_id.get(f.location)
        # Written under a separate key: `location` is a mapped column and these instances are
        # shared through the session identity map, so overwriting it corrupts every other
        # response that reads the raw FK (FacilitySummary.location).
        f.__dict__['location_detail'] = (
            to_response(postal_row, postal_config) if postal_row else None
        )
        f.__dict__['address_detail'] = addresses_by_id.get(f.address)


def _transit_warehouse_for(facility_id: int) -> Warehouse:
    """The in-transit location every facility owns (spec 013, FR-001, FR-007).

    Coded on `facility_id` rather than the facility's code: codes are editable, and a renamed
    facility would otherwise strand its warehouse code. `ACTIVE` regardless of the facility's own
    status, so deactivating a facility cannot strand goods already on a truck.
    """
    return Warehouse(
        facility=facility_id,
        code=f'IN-TRANSIT-{facility_id}',
        name='In Transit',
        comment=(
            'Virtual location holding goods between itinerary departure and delivery '
            '(migration 011)'
        ),
        status=EntityStatus.ACTIVE,
        in_transit=True,
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
    # Flushed, not committed: the in-transit location needs the facility's id, and both rows have
    # to land in one transaction. A facility must never exist without its in-transit location —
    # the alternative is a facility that looks fine until its first dispatch is refused (FR-007).
    await db.flush()
    db.add(_transit_warehouse_for(facility.facility_id))
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


async def delete_facility(db: AsyncSession, facility: Facility, *, current: CurrentUser) -> None:
    """Remove a facility and the in-transit location the system created for it (FR-014).

    The cascade is delete-then-flush rather than an `exempt` on `assert_not_referenced`: that
    parameter is table-granular, so exempting `warehouse` would hide the facility's *real*
    warehouses too and turn a correct 409 into a foreign-key 500 (research R5).

    Ordered deliberately. The transit location's own blockers are asserted first because they are
    the surprising ones — a caller who sees `warehouse.facility (3)` knows what to do, and one who
    sees inventory history on a location they never created needs telling specifically (FR-015).

    Nothing is committed until every check has passed. `get_db` never commits on an exception, so
    a refusal discards the staged delete and the staged audit entry together.
    """
    transit = await warehouse_service.get_transit_warehouse(db, facility.facility_id)
    if transit is not None:
        await assert_not_referenced(db, transit)
        await db.delete(transit)
        # Flushed so the facility's own reference count below sees the row already gone.
        await db.flush()

    await assert_not_referenced(db, facility)

    incidences.record(
        db,
        source=SourceType.FACILITY,
        instance_id=facility.facility_id,
        updater=current.employee_id,
        reason=f'Facility {facility.code} deleted',
        context=(
            f'In-transit location {transit.code} removed with it'
            if transit is not None
            else 'Facility had no in-transit location'
        ),
    )
    await db.delete(facility)
    await db.commit()
