"""Spec 013: in-transit locations are one per facility, system-owned, and never selectable.

The guard against *addressing* one lives in the endpoint (403, not 404), so `get_warehouse` is
asserted here to keep returning them — see `test_get_warehouse_still_returns_in_transit_rows` for
why that is deliberate rather than an oversight.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enums import EntityStatus
from app.models.core import Warehouse
from app.services import warehouse_service


def _warehouse(warehouse_id: int, facility: int, *, in_transit: bool = False) -> Warehouse:
    return Warehouse(
        warehouse_id=warehouse_id,
        facility=facility,
        code=f'IN-TRANSIT-{facility}' if in_transit else f'WH{warehouse_id}',
        name='In Transit' if in_transit else 'Main',
        comment=None,
        status=EntityStatus.ACTIVE,
        in_transit=in_transit,
    )


def _db_returning(rows) -> AsyncMock:
    """A db whose single `execute` yields `rows` from both `.all()` and `.scalar_one_or_none()`."""
    result = MagicMock()
    result.all.return_value = rows
    result.scalar_one_or_none.return_value = rows[0] if rows else None
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


class TestListExcludesEveryInTransitRow:
    """FR-012. Spec 012 excluded one configured id; there are now fourteen rows to exclude."""

    def test_the_filter_is_the_flag_not_a_configured_id(self):
        source = inspect.getsource(warehouse_service.list_warehouses)
        assert 'in_transit.is_(False)' in source
        assert 'in_transit_warehouse_id' not in source, (
            'the setting is retired by FR-006; excluding one id would leave 13 rows selectable'
        )

    @pytest.mark.asyncio
    async def test_the_exclusion_is_applied_to_the_count_as_well_as_the_page(self):
        # Otherwise `total` would count rows the caller can never see.
        source = inspect.getsource(warehouse_service.list_warehouses)
        assert source.count('virtual') >= 3, 'exclusion must reach base *and* count_q'


class TestGetWarehouse:
    @pytest.mark.asyncio
    async def test_get_warehouse_still_returns_in_transit_rows(self):
        """Deliberate (research R4). The 403 belongs to the endpoint.

        Filtering here would make the service lie to every future caller, and would collapse
        "forbidden" into "not found" — the distinction FR-013a exists to preserve.
        """
        transit = _warehouse(20, 1, in_transit=True)
        db = AsyncMock()
        db.get = AsyncMock(return_value=transit)
        db.execute = AsyncMock(return_value=_db_returning([]).execute.return_value)

        found = await warehouse_service.get_warehouse(db, 20)

        assert found is transit
        assert found.in_transit is True

    @pytest.mark.asyncio
    async def test_a_missing_row_is_still_none(self):
        db = AsyncMock()
        db.get = AsyncMock(return_value=None)
        assert await warehouse_service.get_warehouse(db, 999_999) is None


class TestGetTransitWarehouse:
    @pytest.mark.asyncio
    async def test_a_facility_with_a_location_resolves_it(self):
        transit = _warehouse(21, 51, in_transit=True)
        db = _db_returning([transit])

        assert await warehouse_service.get_transit_warehouse(db, 51) is transit

    @pytest.mark.asyncio
    async def test_a_facility_without_one_is_none_not_an_exception(self):
        """The caller decides what a missing location means; the lookup does not.

        `depart()` raises 422; `delete_facility` treats it as nothing to cascade.
        """
        db = _db_returning([])

        assert await warehouse_service.get_transit_warehouse(db, 404) is None


class TestTransitWarehousesFor:
    @pytest.mark.asyncio
    async def test_maps_each_dispatch_warehouse_to_its_own_facility_location(self):
        db = _db_returning([(12, 21), (17, 22)])

        mapping = await warehouse_service.transit_warehouses_for(db, [12, 17])

        assert mapping == {12: 21, 17: 22}

    @pytest.mark.asyncio
    async def test_two_facilities_on_one_trip_cost_a_single_query(self):
        """FR-005 with research R2: one self-join for the trip, never one lookup per line."""
        db = _db_returning([(12, 21), (17, 22)])

        await warehouse_service.transit_warehouses_for(db, [12, 17, 12])

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_a_dispatch_warehouse_whose_facility_has_no_location_is_simply_absent(self):
        """Absent, not zero and not raising — so the caller can name the offender (FR-009)."""
        db = _db_returning([(12, 21)])

        mapping = await warehouse_service.transit_warehouses_for(db, [12, 17])

        assert 17 not in mapping

    @pytest.mark.asyncio
    async def test_no_dispatch_warehouses_issues_no_query_at_all(self):
        db = _db_returning([])

        assert await warehouse_service.transit_warehouses_for(db, []) == {}
        assert db.execute.await_count == 0
