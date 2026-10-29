"""Two-step transfers against real tables (spec 029, US3 scenarios 1–11, T017).

Facility A (1) owns warehouse 1 and its transit 2; facility B is built by `setup_inventory`.
SQLite ignores row locks, so what this proves about the locks is that every step re-reads the
state after `begin_locked`: a repeated step is refused with nothing new posted.
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select, update

from app.core.deps import CurrentUser
from app.enums import EntityStatus, TransactionType
from app.models.core import PointSale, Warehouse
from app.models.inventory import InventoryTransfer, InventoryTransferDetail, LotSerialTracking
from app.schemas.inventory import TransferStatus, TransferUpdate
from app.services import inventory_documents as inv
from app.services import inventory_transfer_service as svc
from app.services import stock_ledger
from tests.integration import inventory_seed as seed

D = Decimal
TRANSFER = int(TransactionType.INVENTORY_TRANSFER)


def _clerk(facility: int, *, point_sale: int | None = None) -> CurrentUser:
    return CurrentUser(
        user_id='clerk',
        session_version=1,
        administrator=False,
        facility_id=facility,
        employee_id=1,
        point_sale_id=point_sale,
    )


async def _draft(db, current, origin: int, destination: int, *lines: tuple[int, str]) -> int:
    transfer = await inv.create_draft(
        db, inv.TRANSFER, warehouse=origin, warehouse_to=destination, comment=None, current=current
    )
    transfer_id = transfer.inventory_transfer_id
    for product, quantity in lines:
        await inv.add_line(
            db, inv.TRANSFER, transfer_id, product=product, quantity=D(quantity), current=current
        )
    return transfer_id


async def _entries(db, transfer_id: int) -> list[tuple[int, int, Decimal]]:
    rows = await db.execute(
        select(LotSerialTracking.warehouse, LotSerialTracking.product, LotSerialTracking.quantity)
        .where(LotSerialTracking.source == TRANSFER, LotSerialTracking.reference == transfer_id)
        .order_by(LotSerialTracking.lot_serial_tracking_id)
    )
    return [tuple(row) for row in rows.all()]


async def _on_hand(db, product: int, warehouse: int) -> Decimal:
    return await stock_ledger.on_hand(db, product=product, warehouse=warehouse)


async def _view(db, transfer_id: int):
    transfer = await inv.get_document(db, inv.TRANSFER, transfer_id)
    await db.refresh(transfer)
    return await svc.attach_derived(db, transfer)


def _figures(transfer) -> list[tuple[Decimal, Decimal, Decimal]]:
    return [
        (line.quantity, line.quantity_received, line.quantity_outstanding)
        for line in transfer.lines
    ]


@pytest.fixture
async def setup(db, seeded):
    return await seed.setup_inventory(db)


@pytest.fixture
async def dispatched(db, setup, current_user):
    """10 of the stockable product sent from A's warehouse to B's."""
    await seed.stock(db, setup.stockable, setup.a.warehouse, '10')
    transfer_id = await _draft(
        db, current_user, setup.a.warehouse, setup.b.warehouse, (setup.stockable, '10')
    )
    await svc.dispatch(db, transfer_id, current=current_user)
    return transfer_id


async def _status_code(call) -> int:
    with pytest.raises(HTTPException) as exc:
        await call
    return exc.value.status_code


async def _error(call) -> HTTPException:
    with pytest.raises(HTTPException) as exc:
        await call
    return exc.value


# ── Origin and destination (scenario 1, FR-040, FR-009) ───────────────────────


async def test_the_same_warehouse_at_both_ends_is_422(db, setup, current_user):
    a = setup.a.warehouse
    assert await _status_code(_draft(db, current_user, a, a)) == 422
    refused = await _error(_draft(db, current_user, a, a))
    assert refused.code == 'inventory_transfer.same_warehouse'

    transfer_id = await _draft(db, current_user, a, setup.b.warehouse)
    edit = inv.update_header(
        db, inv.TRANSFER, transfer_id, TransferUpdate(warehouse_to=a), current=current_user
    )
    assert await _status_code(edit) == 422


@pytest.mark.parametrize('end', ['origin', 'destination'])
async def test_an_in_transit_warehouse_at_either_end_is_422(db, setup, current_user, end):
    ends = (setup.a.transit, setup.b.warehouse) if end == 'origin' else (1, setup.b.transit)
    assert await _status_code(_draft(db, current_user, *ends)) == 422


# ── Dispatch (scenarios 2, 3, FR-041) ─────────────────────────────────────────


async def test_dispatch_over_the_available_stock_is_409_naming_the_product(db, setup, current_user):
    await seed.stock(db, setup.stockable, 1, '10')
    await seed.reserve(db, setup.stockable, 1, '4')
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '7'))

    with pytest.raises(HTTPException) as exc:
        await svc.dispatch(db, transfer_id, current=current_user)
    assert exc.value.status_code == 409
    assert 'INV' in exc.value.detail
    assert exc.value.code == 'inventory_document.insufficient_stock'
    assert exc.value.params == {
        'product_id': setup.stockable,
        'warehouse_id': 1,
        'requested': '7.0000',
        'available': '6.0000',
    }
    assert await _entries(db, transfer_id) == []


async def test_dispatch_numbers_and_moves_stock_into_the_origins_transit(db, setup, current_user):
    await seed.stock(db, setup.stockable, 1, '10')
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '6'))

    transfer = await svc.dispatch(db, transfer_id, current=current_user)

    assert transfer.serial == 1
    assert (transfer.completed, transfer.in_transit) == (True, True)
    assert await _entries(db, transfer_id) == [
        (1, setup.stockable, D(-6)),
        (setup.a.transit, setup.stockable, D(6)),
    ]
    assert await _on_hand(db, setup.stockable, 1) == D(4)
    view = await _view(db, transfer_id)
    assert view.status == TransferStatus.IN_TRANSIT
    assert _figures(view) == [(D(6), D(0), D(6))]
    line = (
        await db.execute(
            select(InventoryTransferDetail).where(InventoryTransferDetail.transfer == transfer_id)
        )
    ).scalar_one()
    await db.refresh(line)
    assert line.quantity_received == D(0)

    # The next dispatch of facility A takes the next number.
    second = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '1'))
    assert (await svc.dispatch(db, second, current=current_user)).serial == 2


async def test_dispatch_twice_is_409_with_nothing_new_posted(db, setup, current_user, dispatched):
    assert await _status_code(svc.dispatch(db, dispatched, current=current_user)) == 409
    assert len(await _entries(db, dispatched)) == 2


async def test_dispatch_with_no_lines_is_409(db, setup, current_user):
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse)
    assert await _status_code(svc.dispatch(db, transfer_id, current=current_user)) == 409
    refused = await _error(svc.dispatch(db, transfer_id, current=current_user))
    assert refused.code == 'inventory_document.no_lines'
    assert refused.params == {'resource': 'inventory_transfer'}


async def test_dispatch_without_a_transit_warehouse_is_422_and_consumes_no_serial(
    db, setup, current_user
):
    await seed.stock(db, setup.stockable, 1, '10')
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '2'))
    await db.execute(
        update(Warehouse).where(Warehouse.warehouse_id == setup.a.transit).values(in_transit=False)
    )
    await db.commit()

    with pytest.raises(HTTPException) as exc:
        await svc.dispatch(db, transfer_id, current=current_user)
    assert exc.value.status_code == 422
    assert 'in-transit' in exc.value.detail
    assert exc.value.code == 'facility.no_in_transit_location'
    assert exc.value.params == {'facility_ids': [setup.a.facility]}
    assert await _entries(db, transfer_id) == []

    await db.execute(
        update(Warehouse).where(Warehouse.warehouse_id == setup.a.transit).values(in_transit=True)
    )
    await db.commit()
    assert (await svc.dispatch(db, transfer_id, current=current_user)).serial == 1


# ── Receive (scenarios 4, 5, 7, FR-042) ───────────────────────────────────────


async def test_a_full_receive_moves_transit_to_the_destination(db, setup, current_user, dispatched):
    transfer = await svc.receive(db, dispatched, None, current=current_user)

    assert transfer.in_transit is False
    entries = await _entries(db, dispatched)
    assert entries[2:] == [
        (setup.a.transit, setup.stockable, D(-10)),
        (setup.b.warehouse, setup.stockable, D(10)),
    ]
    # SC-001: dispatch and receive net to zero through transit.
    assert sum(quantity for warehouse, _, quantity in entries if warehouse == setup.a.transit) == 0
    assert await _on_hand(db, setup.stockable, setup.b.warehouse) == D(10)
    view = await _view(db, dispatched)
    assert view.status == TransferStatus.RECEIVED
    assert _figures(view) == [(D(10), D(10), D(0))]


async def test_a_partial_receive_stays_in_transit_until_the_rest_arrives(
    db, setup, current_user, dispatched
):
    line_id = (await _view(db, dispatched)).lines[0].transfer_detail_id

    await svc.receive(db, dispatched, [(line_id, D(8))], current=current_user)
    view = await _view(db, dispatched)
    assert view.status == TransferStatus.IN_TRANSIT
    assert _figures(view) == [(D(10), D(8), D(2))]
    assert await _on_hand(db, setup.stockable, setup.a.transit) == D(2)

    # Scenario 7: more than outstanding.
    over = svc.receive(db, dispatched, [(line_id, D(3))], current=current_user)
    refused = await _error(over)
    assert refused.status_code == 409
    assert refused.code == 'inventory_transfer.over_received'
    assert refused.params == {'product_code': 'INV', 'received': '3', 'outstanding': '2.0000'}

    await svc.receive(db, dispatched, [(line_id, D(2))], current=current_user)
    view = await _view(db, dispatched)
    assert view.status == TransferStatus.RECEIVED
    assert _figures(view) == [(D(10), D(10), D(0))]
    posted = len(await _entries(db, dispatched))

    # The state is re-read after the lock: receiving again finds nothing in transit.
    again = svc.receive(db, dispatched, [(line_id, D(2))], current=current_user)
    refused = await _error(again)
    assert refused.status_code == 409
    assert refused.code == 'inventory_transfer.not_in_transit'
    assert len(await _entries(db, dispatched)) == posted


async def test_receiving_a_draft_is_409(db, setup, current_user):
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '1'))
    assert await _status_code(svc.receive(db, transfer_id, None, current=current_user)) == 409


async def test_receiving_a_line_of_another_transfer_is_404(db, setup, current_user, dispatched):
    other = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '1'))
    other_line = (await _view(db, other)).lines[0].transfer_detail_id
    call = svc.receive(db, dispatched, [(other_line, D(1))], current=current_user)
    assert await _status_code(call) == 404


# ── Transit warehouse (research R4) ───────────────────────────────────────────


async def test_moving_the_origin_warehouse_does_not_change_the_transit_used(
    db, setup, current_user, dispatched
):
    await db.execute(
        update(Warehouse).where(Warehouse.warehouse_id == 1).values(facility=setup.b.facility)
    )
    await db.commit()
    line_id = (await _view(db, dispatched)).lines[0].transfer_detail_id

    await svc.receive(db, dispatched, [(line_id, D(4))], current=current_user)
    await svc.cancel(db, dispatched, current=current_user)

    entries = await _entries(db, dispatched)
    assert {warehouse for warehouse, _, _ in entries} == {1, setup.a.transit, setup.b.warehouse}
    assert await _on_hand(db, setup.stockable, setup.b.transit) == D(0)
    assert await _on_hand(db, setup.stockable, setup.a.transit) == D(0)
    assert await _on_hand(db, setup.stockable, 1) == D(10)


# ── Close the remainder (scenario 6, FR-043) ──────────────────────────────────


async def test_closing_the_remainder_returns_it_to_the_origin(db, setup, current_user, dispatched):
    line_id = (await _view(db, dispatched)).lines[0].transfer_detail_id
    await svc.receive(db, dispatched, [(line_id, D(8))], current=current_user)

    await svc.close_remainder(db, dispatched, current=current_user)

    assert (await _entries(db, dispatched))[4:] == [
        (setup.a.transit, setup.stockable, D(-2)),
        (1, setup.stockable, D(2)),
    ]
    assert await _on_hand(db, setup.stockable, 1) == D(2)
    assert await _on_hand(db, setup.stockable, setup.a.transit) == D(0)
    view = await _view(db, dispatched)
    assert view.status == TransferStatus.RECEIVED
    assert _figures(view) == [(D(10), D(8), D(0))]

    assert await _status_code(svc.close_remainder(db, dispatched, current=current_user)) == 409


# ── Cancel (scenario 11, FR-044) ──────────────────────────────────────────────


async def test_cancelling_in_transit_returns_the_goods_to_the_origin(
    db, setup, current_user, dispatched
):
    transfer = await svc.cancel(db, dispatched, current=current_user)

    assert (transfer.cancelled, transfer.in_transit) == (True, False)
    assert await _on_hand(db, setup.stockable, 1) == D(10)
    assert await _on_hand(db, setup.stockable, setup.a.transit) == D(0)
    assert (await _view(db, dispatched)).status == TransferStatus.CANCELLED


async def test_cancelling_a_received_transfer_takes_back_from_the_destination(
    db, setup, current_user, dispatched
):
    line_id = (await _view(db, dispatched)).lines[0].transfer_detail_id
    await svc.receive(db, dispatched, [(line_id, D(8))], current=current_user)
    await svc.receive(db, dispatched, None, current=current_user)

    await svc.cancel(db, dispatched, current=current_user)

    assert await _on_hand(db, setup.stockable, 1) == D(10)
    assert await _on_hand(db, setup.stockable, setup.b.warehouse) == D(0)
    assert await _on_hand(db, setup.stockable, setup.a.transit) == D(0)
    # The lines still show what had been received before the cancel (R3).
    view = await _view(db, dispatched)
    assert view.status == TransferStatus.CANCELLED
    assert _figures(view) == [(D(10), D(10), D(0))]


async def test_cancelling_after_the_destination_used_the_stock_is_409(
    db, setup, current_user, dispatched
):
    await svc.receive(db, dispatched, None, current=current_user)
    await seed.stock(db, setup.stockable, setup.b.warehouse, '-6')
    posted = len(await _entries(db, dispatched))

    with pytest.raises(HTTPException) as exc:
        await svc.cancel(db, dispatched, current=current_user)
    assert exc.value.status_code == 409
    assert 'INV' in exc.value.detail and 'W2' in exc.value.detail
    assert len(await _entries(db, dispatched)) == posted


# ── Facility rule (R6, FR-011) ────────────────────────────────────────────────


async def test_dispatch_belongs_to_the_origin_facility(db, setup, current_user):
    await seed.stock(db, setup.stockable, 1, '10')
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (setup.stockable, '1'))

    call = svc.dispatch(db, transfer_id, current=_clerk(setup.b.facility))
    assert await _status_code(call) == 403
    assert (await svc.dispatch(db, transfer_id, current=_clerk(1))).serial == 1


async def test_receive_belongs_to_the_destination_facility(db, setup, current_user, dispatched):
    assert await _status_code(svc.receive(db, dispatched, None, current=_clerk(1))) == 403
    transfer = await svc.receive(db, dispatched, None, current=_clerk(setup.b.facility))
    assert transfer.in_transit is False


@pytest.mark.parametrize('side', ['origin', 'destination'])
async def test_either_facility_may_close_the_remainder(db, setup, current_user, dispatched, side):
    facility = 1 if side == 'origin' else setup.b.facility
    transfer = await svc.close_remainder(db, dispatched, current=_clerk(facility))
    assert transfer.in_transit is False


async def test_a_third_facility_may_not_close_the_remainder(db, setup, current_user, dispatched):
    assert await _status_code(svc.close_remainder(db, dispatched, current=_clerk(999))) == 403


# ── Lists and the in-transit view (scenarios 8, 9, FR-045) ────────────────────


async def test_both_ends_list_a_transfer_in_transit(db, setup, current_user, dispatched):
    db.add(
        PointSale(
            facility=setup.b.facility,
            code='POS2',
            name='Caja 2',
            warehouse=setup.b.warehouse,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.commit()
    pos_b = (
        await db.execute(select(PointSale.point_sale_id).where(PointSale.code == 'POS2'))
    ).scalar_one()

    for facility, point_sale in ((1, 1), (setup.b.facility, pos_b)):
        clerk = CurrentUser(
            user_id='clerk',
            session_version=1,
            administrator=False,
            facility_id=facility,
            employee_id=999,
            point_sale_id=point_sale,
        )
        items, total = await inv.list_documents(
            db,
            inv.TRANSFER,
            current=clerk,
            status='in_transit',
            status_clause=svc.transfer_status_clause,
        )
        assert (total, [t.inventory_transfer_id for t in items]) == (1, [dispatched])


@pytest.mark.parametrize('value', ['draft', 'in_transit', 'received', 'cancelled'])
async def test_the_status_filter(db, setup, current_user, dispatched, value):
    draft = await _draft(db, current_user, 1, setup.b.warehouse)
    expected = {'draft': [draft], 'in_transit': [dispatched]}.get(value, [])
    items, total = await inv.list_documents(
        db,
        inv.TRANSFER,
        current=current_user,
        status=value,
        status_clause=svc.transfer_status_clause,
    )
    assert [t.inventory_transfer_id for t in items] == expected
    assert total == len(expected)


async def test_an_unknown_status_is_422(db, setup, current_user):
    call = inv.list_documents(
        db,
        inv.TRANSFER,
        current=current_user,
        status='completed',
        status_clause=svc.transfer_status_clause,
    )
    assert await _status_code(call) == 422


async def test_the_in_transit_view_shows_both_ends_per_product_and_line(
    db, setup, current_user, dispatched
):
    line_id = (await _view(db, dispatched)).lines[0].transfer_detail_id
    await svc.receive(db, dispatched, [(line_id, D(8))], current=current_user)

    for facility in (1, setup.b.facility):
        view = await svc.in_transit_view(db, facility)
        assert view.facility == facility
        assert [(p.product, p.product_code, p.quantity) for p in view.products] == [
            (setup.stockable, 'INV', D(2))
        ]
        assert [
            (
                line.inventory_transfer_id,
                line.serial,
                line.warehouse,
                line.warehouse_to,
                line.transfer_detail_id,
                line.quantity_outstanding,
            )
            for line in view.lines
        ] == [(dispatched, 1, 1, setup.b.warehouse, line_id, D(2))]

    assert (await svc.in_transit_view(db, 999)).lines == []


# ── Sales-order items (scenario 10, FR-046) ───────────────────────────────────


async def test_sales_order_items_add_stockable_lines_once(db, setup, current_user):
    product = setup.stockable
    order = await seed.sales_order(
        db,
        lines=[(product, '2', 1), (setup.non_stockable, '1', 1), (product, '3', 1), (1, '4', 1)],
    )
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse, (1, '1'))

    await svc.add_sales_order_items(db, transfer_id, order, current=current_user)
    lines = [(line.product, line.quantity) for line in (await _view(db, transfer_id)).lines]
    # Product 1 was already on the transfer: its line stays as it was.
    assert lines == [(1, D(1)), (product, D(5))]
    assert (await _view(db, transfer_id)).lines[1].product_code == 'INV'

    await svc.add_sales_order_items(db, transfer_id, order, current=current_user)
    again = [(line.product, line.quantity) for line in (await _view(db, transfer_id)).lines]
    assert again == lines


@pytest.mark.parametrize('state', ['draft', 'cancelled'])
async def test_sales_order_items_need_a_confirmed_order(db, setup, current_user, state):
    order = await seed.sales_order(
        db,
        lines=[(setup.stockable, '1', 1)],
        completed=state != 'draft',
        cancelled=state == 'cancelled',
    )
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse)
    call = svc.add_sales_order_items(db, transfer_id, order, current=current_user)
    refused = await _error(call)
    assert refused.status_code == 409
    assert refused.code == 'sales_order.not_confirmed'
    assert refused.params == {'sales_order_id': order}


async def test_sales_order_items_of_an_unknown_order_is_404(db, setup, current_user):
    transfer_id = await _draft(db, current_user, 1, setup.b.warehouse)
    call = svc.add_sales_order_items(db, transfer_id, 999, current=current_user)
    assert await _status_code(call) == 404


async def test_sales_order_items_on_a_dispatched_transfer_is_409(
    db, setup, current_user, dispatched
):
    order = await seed.sales_order(db, lines=[(setup.stockable, '1', 1)])
    call = svc.add_sales_order_items(db, dispatched, order, current=current_user)
    assert await _status_code(call) == 409


# ── Legacy transfers (FR-044a, R3) ────────────────────────────────────────────


async def test_a_legacy_transfer_reads_received_and_cancels_what_it_posted(db, setup, current_user):
    product = setup.stockable
    elsewhere = await seed.warehouse(db, facility=1, code='W3')
    await seed.stock(db, product, elsewhere, '5')
    legacy = InventoryTransfer(
        facility=1,
        serial=50,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        creator=1,
        updater=1,
        warehouse=1,
        warehouse_to=setup.b.warehouse,
        completed=True,
        cancelled=False,
        in_transit=False,
    )
    db.add(legacy)
    await db.flush()
    db.add(
        InventoryTransferDetail(
            transfer=legacy.inventory_transfer_id,
            product=product,
            quantity=D(5),
            quantity_received=None,
            product_code='INV',
            product_name='Producto INV',
        )
    )
    # One-step: out of a warehouse that is no longer its origin, straight into the destination.
    for warehouse, quantity in ((elsewhere, '-5'), (setup.b.warehouse, '5')):
        stock_ledger.post_movement(
            db,
            source=TransactionType.INVENTORY_TRANSFER,
            reference=legacy.inventory_transfer_id,
            product=product,
            warehouse=warehouse,
            quantity=abs(D(quantity)),
            outbound=D(quantity) < 0,
        )
    await db.commit()
    transfer_id = legacy.inventory_transfer_id

    view = await _view(db, transfer_id)
    assert view.status == TransferStatus.RECEIVED
    assert _figures(view) == [(D(5), D(5), D(0))]

    await svc.cancel(db, transfer_id, current=current_user)

    assert await _on_hand(db, product, elsewhere) == D(5)
    assert await _on_hand(db, product, setup.b.warehouse) == D(0)
    assert await _entries(db, transfer_id) == [
        (elsewhere, product, D(-5)),
        (setup.b.warehouse, product, D(5)),
        (elsewhere, product, D(5)),
        (setup.b.warehouse, product, D(-5)),
    ]
    view = await _view(db, transfer_id)
    assert view.status == TransferStatus.CANCELLED
    assert _figures(view) == [(D(5), D(5), D(0))]


# ── Through the routes ────────────────────────────────────────────────────────


async def test_the_routes_run_a_transfer_end_to_end(client, db, setup):
    await seed.stock(db, setup.stockable, 1, '10')
    base = '/api/v1/inventory-transfers'

    created = await client.post(base, json={'warehouse': 1, 'warehouse_to': setup.b.warehouse})
    assert created.status_code == 201, created.text
    transfer_id = created.json()['inventory_transfer_id']
    added = await client.post(
        f'{base}/{transfer_id}/lines', json={'product': setup.stockable, 'quantity': '10'}
    )
    line_id = added.json()['lines'][0]['transfer_detail_id']
    dispatched = await client.post(f'{base}/{transfer_id}/dispatch')
    assert (dispatched.json()['status'], dispatched.json()['serial']) == ('in_transit', 1)

    partial = await client.post(
        f'{base}/{transfer_id}/receive', json={'lines': [{'line_id': line_id, 'quantity': '8'}]}
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()['lines'][0]['quantity_outstanding'] == '2.0000'

    in_transit = await client.get(f'{base}/in-transit', params={'facility': setup.b.facility})
    assert in_transit.json()['products'][0]['quantity'] == '2.0000'

    rest = await client.post(f'{base}/{transfer_id}/receive')
    assert rest.json()['status'] == 'received'
    listed = await client.get(base, params={'status': 'received'})
    assert [t['inventory_transfer_id'] for t in listed.json()['items']] == [transfer_id]
    assert (await client.get(f'{base}/{transfer_id}')).json()['lines'][0][
        'quantity_received'
    ] == '10.0000'
