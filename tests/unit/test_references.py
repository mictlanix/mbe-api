"""The shared referential guard behind every delete endpoint (#107).

The referencing tables are derived from the mapped metadata, so these tests use real models
and assert against the real foreign keys rather than a hand-written list.
"""

import re
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.errors import RESOURCE_NAMES
from app.db.base import Base
from app.enums import EntityStatus, FacilityType
from app.models.core import Facility, Warehouse
from app.models.product import Product
from app.models.user import User
from app.services.references import (
    assert_not_referenced,
    assert_unique,
    find_blocking_references,
)

RENAMED = {table for table, name in RESOURCE_NAMES.items() if table != name}


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
async def test_names_the_blockers_by_public_resource_name() -> None:
    db = _db_counting([('warehouse.facility', 9), ('point_sale.facility', 2)])

    with pytest.raises(HTTPException) as exc:
        await assert_not_referenced(db, _facility())

    assert exc.value.status_code == 409
    assert exc.value.code == 'resource.still_referenced'
    assert exc.value.params == {
        'references': [
            {'resource': 'warehouse', 'count': 9},
            {'resource': 'point_of_sale', 'count': 2},
        ],
        'more': 0,
    }
    assert exc.value.detail == (
        'Still referenced by warehouse (9), point_of_sale (2) — remove those records first'
    )


@pytest.mark.asyncio
async def test_two_columns_of_one_table_count_as_one_resource() -> None:
    """A transfer's source and destination warehouse are one `inventory_transfer` blocker."""
    db = _db_counting(
        [('inventory_transfer.warehouse', 3), ('inventory_transfer.warehouse_to', 2)]
    )

    with pytest.raises(HTTPException) as exc:
        await assert_not_referenced(db, Warehouse(warehouse_id=1))

    assert exc.value.params['references'] == [{'resource': 'inventory_transfer', 'count': 5}]


@pytest.mark.asyncio
async def test_long_blocker_lists_are_truncated() -> None:
    tables = [
        'sales_order', 'cash_drawer', 'warehouse', 'point_sale', 'expenses', 'customer_payment',
        'sales_quote', 'purchase_order',
    ]
    db = _db_counting([(f'{table}.facility', 10 - i) for i, table in enumerate(tables)])

    with pytest.raises(HTTPException) as exc:
        await assert_not_referenced(db, _facility())

    assert len(exc.value.params['references']) == 5
    assert exc.value.params['more'] == 3
    assert 'and 3 more' in exc.value.detail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'model',
    sorted(
        {fk.column.table for t in Base.metadata.tables.values() for fk in t.foreign_keys},
        key=lambda t: t.name,
    ),
    ids=lambda t: t.name,
)
async def test_no_table_name_reaches_a_still_referenced_error(model) -> None:
    """SC-006: whatever references whatever, the body carries public names only."""
    columns = [
        (table.name, column.name)
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table is model
        for column in [fk.parent]
    ]
    db = _db_counting([(f'{t}.{c}', 1) for t, c in columns])

    with pytest.raises(HTTPException) as exc:
        await _assert_blocked(db, model, columns)

    text = f'{exc.value.detail} {exc.value.params}'
    leaked = {t for t, _ in columns if t in RENAMED and re.search(rf'\b{t}\b', text)}
    assert not leaked


async def _assert_blocked(db, model, columns) -> None:
    from app.services import references

    async def _blockers(*_args, **_kwargs):
        return sorted(((f'{t}.{c}', 1) for t, c in columns), key=lambda b: b[0])

    original = references.find_blocking_references
    references.find_blocking_references = _blockers
    try:
        await assert_not_referenced(db, object())
    finally:
        references.find_blocking_references = original


@pytest.mark.asyncio
async def test_assert_unique_names_the_resource_and_field() -> None:
    result = MagicMock()
    result.first.return_value = object()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(HTTPException) as exc:
        await assert_unique(db, Warehouse, Warehouse.code, 'WH1', label='Warehouse code')

    assert exc.value.status_code == 409
    assert exc.value.code == 'resource.already_exists'
    assert exc.value.params == {'resource': 'warehouse', 'field': 'code'}
    assert exc.value.detail == 'Warehouse code already exists'


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
