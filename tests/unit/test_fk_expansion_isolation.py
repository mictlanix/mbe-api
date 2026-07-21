"""Reproduces GH #95: `_attach_relations` helpers expand a FK by writing to the ORM
instance's `__dict__`. Those instances are shared through the session identity map, so
writing over the mapped column itself corrupts every other response that reads the raw FK.
The expansion must land on a separate key and leave the mapped column untouched."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enums import EntityStatus, FacilityType
from app.models.core import Facility, Warehouse
from app.models.sat_catalog import SatPostalCode
from app.schemas.core import (
    FacilityResponse,
    FacilitySummary,
    WarehouseResponse,
    WarehouseSummary,
)
from app.services import facility_service, warehouse_service


def _db_returning(rows: list) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _facility() -> Facility:
    return Facility(
        facility_id=1,
        code="S1",
        name="Main Store",
        type=FacilityType.STORE,
        location="55620",
        address=1,
        taxpayer="RFC123456789A",
        logo=None,
        receipt_message=None,
        default_batch=None,
        status=EntityStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_facility_expansion_leaves_mapped_location_intact() -> None:
    facility = _facility()
    db = _db_returning([SatPostalCode(sat_postal_code_id="55620", state="MEX")])

    await facility_service._attach_relations(db, [facility])

    # the mapped column still holds the raw FK, so FacilitySummary keeps working
    assert facility.location == "55620"
    assert FacilitySummary.model_validate(facility).location == "55620"
    # and the expanded value is reachable for the detail response
    assert FacilityResponse.model_validate(facility).location.id == "55620"


@pytest.mark.asyncio
async def test_warehouse_expansion_leaves_mapped_facility_intact() -> None:
    warehouse = Warehouse(
        warehouse_id=1, facility=1, code="WH1", name="Main", comment=None,
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
        _db_returning([SatPostalCode(sat_postal_code_id="55620", state="MEX")]), [facility]
    )
    warehouse = Warehouse(
        warehouse_id=1, facility=1, code="WH1", name="Main", comment=None,
        status=EntityStatus.ACTIVE,
    )
    # the very same (already expanded) instance is handed back by the second service
    await warehouse_service._attach_relations(_db_returning([facility]), [warehouse])

    assert WarehouseResponse.model_validate(warehouse).facility.location == "55620"
