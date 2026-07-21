"""Reproduces GH #95: `_attach_relations` helpers expand a FK by writing to the ORM
instance's `__dict__`. Those instances are shared through the session identity map, so
writing over the mapped column itself corrupts every other response that reads the raw FK.
The expansion must land on a separate key and leave the mapped column untouched."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enums import AddressType, EntityStatus, FacilityType
from app.models.core import Address, Facility, Warehouse
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import (
    FacilityResponse,
    FacilitySummary,
    WarehouseResponse,
    WarehouseSummary,
)
from app.services import facility_service, warehouse_service


def _db_returning(*batches: list) -> AsyncMock:
    """A db whose successive `execute` calls return the given row batches in order."""

    def _result(rows: list) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(rows) for rows in batches])
    return db


def _address() -> Address:
    return Address(
        address_id=1,
        nickname=None,
        type=AddressType.OTHER,
        street='Reforma',
        exterior_number='100',
        interior_number=None,
        postal_code='55620',
        neighborhood='Centro',
        locality=None,
        borough='Cuauhtemoc',
        state='CDMX',
        city=None,
        country='MEX',
        url_address=None,
        comment=None,
        status=EntityStatus.ACTIVE,
    )


def _facility() -> Facility:
    return Facility(
        facility_id=1,
        code='S1',
        name='Main Store',
        type=FacilityType.STORE,
        location='55620',
        address=1,
        taxpayer='RFC123456789A',
        logo=None,
        receipt_message=None,
        default_batch=None,
        status=EntityStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_facility_expansion_leaves_mapped_location_intact() -> None:
    facility = _facility()
    db = _db_returning([SatPostalCode(sat_postal_code_id='55620', state='MEX')], [_address()])

    await facility_service._attach_relations(db, [facility])

    # the mapped column still holds the raw FK, so FacilitySummary keeps working
    assert facility.location == '55620'
    assert FacilitySummary.model_validate(facility).location == '55620'
    assert facility.address == 1
    assert FacilitySummary.model_validate(facility).address == 1
    # and the expanded values are reachable for the detail response
    response = FacilityResponse.model_validate(facility)
    assert response.location.id == '55620'
    assert response.address.address_id == 1


@pytest.mark.asyncio
async def test_warehouse_expansion_leaves_mapped_facility_intact() -> None:
    warehouse = Warehouse(
        warehouse_id=1,
        facility=1,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )
    db = _db_returning([_facility()])

    await warehouse_service._attach_relations(db, [warehouse])

    # WarehouseSummary is embedded in point-of-sale responses and reads the raw int
    assert warehouse.facility == 1
    assert WarehouseSummary.model_validate(warehouse).facility == 1
    assert WarehouseResponse.model_validate(warehouse).facility.facility_id == 1


@pytest.mark.asyncio
async def test_expanded_facility_does_not_corrupt_an_embedding_warehouse() -> None:
    """The original failure: one Facility instance reaching both services through the
    identity map, expanded by one and read raw by the other."""
    facility = _facility()

    await facility_service._attach_relations(
        _db_returning([SatPostalCode(sat_postal_code_id='55620', state='MEX')], [_address()]),
        [facility],
    )
    warehouse = Warehouse(
        warehouse_id=1,
        facility=1,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )
    # the very same (already expanded) instance is handed back by the second service
    await warehouse_service._attach_relations(_db_returning([facility]), [warehouse])

    assert WarehouseResponse.model_validate(warehouse).facility.location == '55620'
