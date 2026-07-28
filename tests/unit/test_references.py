"""The shared referential guard behind every delete endpoint (#107).

The referencing tables are derived from the mapped metadata, so these tests use real models
and assert against the real foreign keys rather than a hand-written list.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.enums import EntityStatus, FacilityType
from app.models.core import Facility, Warehouse
from app.models.product import Product
from app.models.user import User
from app.services.references import assert_not_referenced, find_blocking_references


def _db_counting(rows: list[tuple[str, int]]) -> AsyncMock:
    """A db whose single union query reports these `(table.column, rows)` counts."""
    result = MagicMock()
    result.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


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
async def test_reports_blockers_largest_first() -> None:
    db = _db_counting(
        [('cash_drawer.facility', 2), ('warehouse.facility', 9), ('sales_order.facility', 0)]
    )

    blockers = await find_blocking_references(db, _facility())

    assert blockers == [('warehouse.facility', 9), ('cash_drawer.facility', 2)]


@pytest.mark.asyncio
async def test_no_blockers_when_every_count_is_zero() -> None:
    db = _db_counting([('warehouse.facility', 0), ('cash_drawer.facility', 0)])

    assert await find_blocking_references(db, _facility()) == []
    await assert_not_referenced(db, _facility())  # does not raise


@pytest.mark.asyncio
async def test_names_the_blocking_tables_in_the_error() -> None:
    db = _db_counting([('warehouse.facility', 9), ('cash_drawer.facility', 2)])

    with pytest.raises(HTTPException) as exc:
        await assert_not_referenced(db, _facility())

    assert exc.value.status_code == 409
    assert 'warehouse.facility (9)' in exc.value.detail
    assert 'cash_drawer.facility (2)' in exc.value.detail


@pytest.mark.asyncio
async def test_long_blocker_lists_are_truncated() -> None:
    db = _db_counting([(f't{i}', i) for i in range(1, 9)])

    with pytest.raises(HTTPException) as exc:
        await assert_not_referenced(db, _facility())

    assert 'and 3 more' in exc.value.detail


@pytest.mark.asyncio
async def test_exempt_tables_are_not_queried() -> None:
    """The product/user cascades are deliberate, so their rows must not block the delete."""
    db = _db_counting([])
    product = Product(product_id=1, code='P1', name='Widget')

    await find_blocking_references(db, product, exempt=frozenset({'product_price'}))

    compiled = str(db.execute.await_args.args[0])
    assert 'product_price' not in compiled
    assert 'customer_discount' in compiled  # a genuine referencing table is still checked


@pytest.mark.asyncio
async def test_unsaved_instance_has_no_blockers() -> None:
    db = _db_counting([])

    assert await find_blocking_references(db, Facility()) == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_has_nothing_left_to_check_once_its_cascades_are_exempt() -> None:
    """`user_settings` and `access_privilege` are the only tables referencing `user`, so
    exempting the cascade leaves no blocker to look for and the delete always proceeds."""
    db = _db_counting([])
    user = User(user_id='tester')

    blockers = await find_blocking_references(
        db, user, exempt=frozenset({'user_settings', 'access_privilege'})
    )

    assert blockers == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_two_foreign_keys_to_the_same_target_are_reported_separately() -> None:
    """`inventory_transfer` points at `warehouse` twice, and `cash_session` at `employee`
    twice. Labelling by table alone would list the same name with two different counts and
    leave the client unable to tell which relation blocks the delete."""
    db = _db_counting([])
    warehouse = Warehouse(warehouse_id=1)

    await find_blocking_references(db, warehouse)

    compiled = str(db.execute.await_args.args[0])
    assert 'inventory_transfer.warehouse' in compiled
    assert 'inventory_transfer.warehouse_to' in compiled


class TestDeliveryTablesAreDiscoverable:
    """The three tables spec 012 adds must be visible to the reference guard.

    `references` derives blockers from mapped metadata rather than a hand-written list, so a new
    foreign key is covered the moment its model exists — but only if the model is imported. These
    assert the delivery models reached the registry (T102).
    """

    def test_delivery_order_is_referenced_by_its_events_and_lines(self) -> None:
        from app.models.logistics import DeliveryOrder
        from app.services.references import referencing_columns

        names = {f'{t.name}.{c.name}' for t, c in referencing_columns(DeliveryOrder)}

        assert 'delivery_order_event.delivery_order' in names
        assert 'delivery_order_detail.delivery_order' in names
        # A partial-delivery child points back at its parent (FR-048)
        assert 'delivery_order.parent_delivery_order' in names

    def test_proof_of_delivery_is_referenced_by_both_holders(self) -> None:
        from app.models.logistics import ProofOfDelivery
        from app.services.references import referencing_columns

        names = {f'{t.name}.{c.name}' for t, c in referencing_columns(ProofOfDelivery)}

        assert 'delivery_order.proof_of_delivery' in names
        assert 'deliveries_itinerary_stop.proof_of_delivery' in names

    def test_a_stop_is_referenced_by_its_lines(self) -> None:
        from app.models.logistics import DeliveriesItineraryStop
        from app.services.references import referencing_columns

        cols = referencing_columns(DeliveriesItineraryStop)
        names = {f'{t.name}.{c.name}' for t, c in cols}

        assert 'deliveries_itinerary_detail.deliveries_itinerary_stop' in names
