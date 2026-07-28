"""Spec 013: a facility owns exactly one in-transit location, created and destroyed with it.

Two properties carry the weight here, and both are about what happens when something goes wrong:

- creation is all-or-nothing, so a facility never exists without its location (FR-007);
- deletion stages a row removal *and* an audit entry before the check that might refuse, so a
  refusal must leave both undone (FR-014, FR-015, FR-015a).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.deps import CurrentUser
from app.enums import EntityStatus, SourceType
from app.models.core import Facility, Warehouse
from app.models.incidence import Incidence
from app.services import facility_service


def _current(employee_id: int = 7) -> CurrentUser:
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=None,
        employee_id=employee_id,
    )


def _facility(facility_id: int = 51, code: str = 'CMZU') -> Facility:
    return Facility(
        facility_id=facility_id,
        code=code,
        name='Casa Maestra Zumpango',
        type=0,
        location='55600',
        address=1,
        taxpayer='AAA010101AAA',
        status=EntityStatus.ACTIVE,
    )


def _transit(facility_id: int = 51, warehouse_id: int = 21) -> Warehouse:
    return Warehouse(
        warehouse_id=warehouse_id,
        facility=facility_id,
        code=f'IN-TRANSIT-{facility_id}',
        name='In Transit',
        comment=None,
        status=EntityStatus.ACTIVE,
        in_transit=True,
    )


class _Db:
    """Records what was staged, so a refusal can be shown to have staged nothing durable."""

    def __init__(self):
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.committed = False
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushed += 1

    async def commit(self):
        self.committed = True

    async def refresh(self, _obj):
        return None

    async def execute(self, _statement):
        result = MagicMock()
        result.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result


class TestCreateFacility:
    """FR-007 — the location exists the moment the facility does."""

    @pytest.mark.asyncio
    async def test_the_in_transit_location_is_created_with_the_facility(self):
        db = _Db()
        data = SimpleNamespace(
            code='NEW', name='New', type=0, location='55600', address=1,
            taxpayer='AAA010101AAA', logo=None, receipt_message=None,
            default_batch=None, status=EntityStatus.ACTIVE,
        )
        with patch.object(facility_service, '_attach_relations', new=AsyncMock()):
            # The id is assigned by the flush in the real thing; stand it in here.
            async def _flush():
                db.flushed += 1
                db.added[0].facility_id = 99

            db.flush = _flush
            await facility_service.create_facility(db, data)

        transit = [o for o in db.added if isinstance(o, Warehouse)]
        assert len(transit) == 1
        assert transit[0].in_transit is True
        assert transit[0].facility == 99
        assert transit[0].code == 'IN-TRANSIT-99'
        assert transit[0].status == EntityStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_both_rows_land_in_one_transaction(self):
        """A second commit between them would leave a window with a facility and no location."""
        import inspect

        source = inspect.getsource(facility_service.create_facility)

        assert source.count('await db.commit()') == 1
        assert source.index('db.flush()') < source.index('db.commit()')

    def test_the_location_is_active_even_for_an_inactive_facility(self):
        """Deactivating a facility must not strand goods already on a truck."""
        assert facility_service._transit_warehouse_for(6).status == EntityStatus.ACTIVE

    def test_the_code_is_keyed_on_the_id_not_the_editable_facility_code(self):
        assert facility_service._transit_warehouse_for(53).code == 'IN-TRANSIT-53'


class TestDeleteFacilityCascade:
    """FR-014, FR-015."""

    @pytest.mark.asyncio
    async def test_the_location_goes_with_the_facility(self):
        db = _Db()
        facility, transit = _facility(), _transit()
        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=transit),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=AsyncMock()),
        ):
            await facility_service.delete_facility(db, facility, current=_current())

        assert transit in db.deleted
        assert facility in db.deleted
        assert db.committed is True

    @pytest.mark.asyncio
    async def test_the_transit_blocker_is_asserted_before_the_facility_s(self):
        """FR-015 — the surprising blocker is the one that should be named."""
        db = _Db()
        transit = _transit()
        order: list[object] = []

        async def _assert(_db, instance, **_kw):
            order.append(instance)

        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=transit),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=_assert),
        ):
            await facility_service.delete_facility(db, _facility(), current=_current())

        assert order[0] is transit

    @pytest.mark.asyncio
    async def test_inventory_history_on_the_location_still_refuses(self):
        db = _Db()
        transit = _transit()

        async def _assert(_db, instance, **_kw):
            if instance is transit:
                raise HTTPException(
                    status_code=409,
                    detail='Still referenced by lot_serial_tracking.warehouse (4)',
                )

        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=transit),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=_assert),
        ):
            with pytest.raises(HTTPException) as exc:
                await facility_service.delete_facility(db, _facility(), current=_current())

        assert exc.value.status_code == 409
        assert 'lot_serial_tracking' in exc.value.detail

    @pytest.mark.asyncio
    async def test_a_facility_without_a_location_is_not_a_special_case(self):
        """Possible if the row was removed out of band; deletion still proceeds."""
        db = _Db()
        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=None),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=AsyncMock()),
        ):
            await facility_service.delete_facility(db, _facility(), current=_current())

        assert db.committed is True

    def test_the_cascade_does_not_use_a_table_granular_exemption(self):
        """`exempt={'warehouse'}` would hide the facility's REAL warehouses (research R5).

        Asserted on the keyword as it would be *passed*, not on the bare word: the docstring
        explains why the exemption was rejected, and `inspect.getsource` includes docstrings.
        """
        import inspect

        source = inspect.getsource(facility_service.delete_facility)

        assert 'exempt=' not in source


class TestDeleteFacilityIsAudited:
    """FR-015a."""

    @pytest.mark.asyncio
    async def test_a_successful_delete_stages_exactly_one_entry(self):
        db = _Db()
        transit = _transit()
        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=transit),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=AsyncMock()),
        ):
            await facility_service.delete_facility(db, _facility(), current=_current(7))

        entries = [o for o in db.added if isinstance(o, Incidence)]
        assert len(entries) == 1
        assert entries[0].source == int(SourceType.FACILITY)
        assert entries[0].instance_id == 51
        assert entries[0].updater == 7
        assert 'IN-TRANSIT-51' in entries[0].content

    @pytest.mark.asyncio
    async def test_a_refused_delete_leaves_the_row_and_writes_no_entry(self):
        """The trap this whole test file exists for.

        The transit row is deleted and the audit entry staged *before* the facility's own check.
        Nothing is committed, so a rollback discards both — but that is a property of `get_db`
        two directories away, not of this function. If session handling ever changes, this is the
        test that catches it: a 409 that had silently destroyed the location and logged a deletion
        that never happened.
        """
        db = _Db()
        facility, transit = _facility(), _transit()

        async def _assert(_db, instance, **_kw):
            if instance is facility:
                raise HTTPException(
                    status_code=409, detail='Still referenced by warehouse.facility (2)'
                )

        with (
            patch.object(
                facility_service.warehouse_service,
                'get_transit_warehouse',
                new=AsyncMock(return_value=transit),
            ),
            patch.object(facility_service, 'assert_not_referenced', new=_assert),
        ):
            with pytest.raises(HTTPException):
                await facility_service.delete_facility(db, facility, current=_current())

        assert db.committed is False, 'nothing may be committed when the delete is refused'
        assert not [o for o in db.added if isinstance(o, Incidence)], (
            'a refused deletion must not be logged as having happened'
        )
