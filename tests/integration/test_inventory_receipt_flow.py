"""Warehouse receipts from draft to confirmed to cancelled, over their routes (spec 029, US1)."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.deps import CurrentUser
from app.enums import TransactionType
from app.models.core import Warehouse
from app.models.inventory import (
    InventoryReceipt,
    InventoryReceiptDetail,
    LotSerialTracking,
)
from app.models.product import Product
from app.models.purchases import PurchaseOrderDetail
from app.services import inventory_documents as inv
from app.services import stock_ledger
from tests.integration import inventory_seed as seed
from tests.problem import assert_problem

URL = '/api/v1/inventory-receipts'
SOURCE = int(TransactionType.INVENTORY_RECEIPT)


async def _draft(client, warehouse: int = 1) -> int:
    response = await client.post(URL, json={'warehouse': warehouse})
    assert response.status_code == 201, response.text
    return response.json()['inventory_receipt_id']


async def _add(client, receipt: int, product: int, quantity: str = '3'):
    return await client.post(
        f'{URL}/{receipt}/lines', json={'product': product, 'quantity': quantity}
    )


async def _entries(db, receipt: int) -> list[LotSerialTracking]:
    return list(
        (
            await db.execute(
                select(LotSerialTracking)
                .where(LotSerialTracking.source == SOURCE, LotSerialTracking.reference == receipt)
                .order_by(LotSerialTracking.lot_serial_tracking_id)
            )
        )
        .scalars()
        .all()
    )


async def _on_hand(db, product: int, warehouse: int) -> Decimal:
    return await stock_ledger.available(db, product=product, warehouse=warehouse)


def _outsider(*, facility: int) -> CurrentUser:
    return CurrentUser(
        user_id='clerk',
        session_version=1,
        administrator=False,
        facility_id=facility,
        employee_id=1,
    )


# ── Confirm ───────────────────────────────────────────────────────────────────


async def test_confirm_numbers_per_facility_and_posts_one_entry_per_line(client, db, seeded):
    setup = await seed.setup_inventory(db)
    other = await seed.product(db, code='INV2')

    first = await _draft(client)
    await _add(client, first, setup.stockable, '3')
    await _add(client, first, other, '5')
    response = await client.post(f'{URL}/{first}/confirm')
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['serial'], body['status']) == (1, 'completed')
    assert [line['quantity'] for line in body['lines']] == ['3.0000', '5.0000']

    entries = await _entries(db, first)
    assert sorted((e.product, e.warehouse, e.quantity) for e in entries) == sorted(
        [(setup.stockable, 1, Decimal(3)), (other, 1, Decimal(5))]
    )
    assert all(
        (e.lot_number, e.serial_number, e.expiration_date) == (None, None, None) for e in entries
    )

    second = await _draft(client)
    await _add(client, second, setup.stockable)
    assert (await client.post(f'{URL}/{second}/confirm')).json()['serial'] == 2

    # Numbering is per facility: the other facility starts at 1.
    third = await _draft(client, warehouse=setup.b.warehouse)
    await _add(client, third, setup.stockable)
    assert (await client.post(f'{URL}/{third}/confirm')).json()['serial'] == 1


async def test_a_confirmed_receipt_is_read_only(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    line = (await _add(client, receipt, setup.stockable)).json()['lines'][0]['receipt_detail_id']
    await client.post(f'{URL}/{receipt}/confirm')

    assert (await _add(client, receipt, setup.stockable)).status_code == 409
    assert (
        await client.put(f'{URL}/{receipt}/lines/{line}', json={'quantity': '9'})
    ).status_code == 409
    assert (await client.delete(f'{URL}/{receipt}/lines/{line}')).status_code == 409
    assert (await client.put(f'{URL}/{receipt}', json={'comment': 'x'})).status_code == 409


async def test_confirm_without_lines_is_409_and_consumes_no_serial(client, db, seeded):
    setup = await seed.setup_inventory(db)
    empty = await _draft(client)
    assert (await client.post(f'{URL}/{empty}/confirm')).status_code == 409

    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable)
    assert (await client.post(f'{URL}/{receipt}/confirm')).json()['serial'] == 1


async def test_confirming_twice_is_409_with_no_new_entries_or_serial(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable, '3')
    assert (await client.post(f'{URL}/{receipt}/confirm')).status_code == 200

    assert (await client.post(f'{URL}/{receipt}/confirm')).status_code == 409
    assert len(await _entries(db, receipt)) == 1
    next_one = await _draft(client)
    await _add(client, next_one, setup.stockable)
    assert (await client.post(f'{URL}/{next_one}/confirm')).json()['serial'] == 2


async def test_a_legacy_draft_with_bad_lines_cannot_be_confirmed(client, db, seeded):
    setup = await seed.setup_inventory(db)

    async def legacy(product: int, quantity: str) -> int:
        receipt = await _draft(client)
        row = await db.get(Product, product)
        db.add(
            InventoryReceiptDetail(
                receipt=receipt,
                product=product,
                quantity=Decimal(quantity),
                product_code=row.code,
                product_name=row.name,
                quantity_ordered=Decimal(0),
            )
        )
        await db.commit()
        return receipt

    zero = await legacy(setup.stockable, '0')
    response = await client.post(f'{URL}/{zero}/confirm')
    assert response.status_code == 422
    assert 'INV' in response.json()['detail']
    assert response.json()['code'] == 'inventory_document.line_without_quantity'
    assert response.json()['params'] == {'product_code': response.json()['detail'].split()[1]}

    service = await legacy(setup.non_stockable, '2')
    response = await client.post(f'{URL}/{service}/confirm')
    assert response.status_code == 422
    assert 'SRV' in response.json()['detail']
    assert response.json()['code'] == 'product.not_stockable'
    assert set(response.json()['params']) == {'product_code'}
    assert await _entries(db, zero) == await _entries(db, service) == []


async def test_a_product_discontinued_after_adding_still_confirms(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable)
    (await db.get(Product, setup.stockable)).status = 2
    await db.commit()
    assert (await client.post(f'{URL}/{receipt}/confirm')).status_code == 200

    # Adding a discontinued but stockable product is accepted too (R9).
    again = await _draft(client)
    assert (await _add(client, again, setup.stockable)).status_code == 200


# ── Lines and header ──────────────────────────────────────────────────────────


async def test_lines_merge_and_reject_non_stockable(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable, '2')
    body = (await _add(client, receipt, setup.stockable, '3')).json()
    assert [(line['product'], line['quantity']) for line in body['lines']] == [
        (setup.stockable, '5.0000')
    ]
    assert body['lines'][0]['quantity_ordered'] == '0.0000'
    assert body['lines'][0]['quantity_received_elsewhere'] is None
    assert body['warnings'] == []

    refused = await _add(client, receipt, setup.non_stockable)
    assert refused.status_code == 422
    assert refused.json()['code'] == 'product.not_stockable'
    assert isinstance(refused.json()['params']['product_code'], str)
    assert (await _add(client, receipt, 9999)).status_code == 404


async def test_a_draft_warehouse_moves_its_facility_and_refuses_transit(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    response = await client.put(f'{URL}/{receipt}', json={'warehouse': setup.b.warehouse})
    assert response.status_code == 200
    assert (response.json()['warehouse'], response.json()['facility']) == (
        setup.b.warehouse,
        setup.b.facility,
    )

    assert (
        await client.put(f'{URL}/{receipt}', json={'warehouse': setup.a.transit})
    ).status_code == 422
    assert (await client.post(URL, json={'warehouse': setup.a.transit})).status_code == 422


# ── Cancel ────────────────────────────────────────────────────────────────────


async def test_cancel_reverses_the_stock(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable, '4')
    await client.post(f'{URL}/{receipt}/confirm')
    assert await _on_hand(db, setup.stockable, 1) == Decimal(4)

    response = await client.post(f'{URL}/{receipt}/cancel')
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'
    assert await _on_hand(db, setup.stockable, 1) == Decimal(0)
    assert sorted(e.quantity for e in await _entries(db, receipt)) == [Decimal(-4), Decimal(4)]

    again = await client.post(f'{URL}/{receipt}/cancel')
    body = assert_problem(again, 409, 'inventory_document.already_cancelled')
    assert body['params'] == {'resource': 'inventory_receipt'}
    assert len(await _entries(db, receipt)) == 2


async def test_cancel_is_refused_once_the_goods_have_left(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable, '4')
    await client.post(f'{URL}/{receipt}/confirm')
    await seed.stock(db, setup.stockable, 1, '-3')

    response = await client.post(f'{URL}/{receipt}/cancel')
    assert response.status_code == 409
    assert 'INV' in response.json()['detail']
    assert response.json()['detail'].startswith('Not enough stock of INV in ')
    body = assert_problem(response, 409, 'inventory_document.insufficient_stock')
    assert body['params'] == {
        'product_id': setup.stockable,
        'warehouse_id': 1,
        'requested': '4.0000',
        'available': '1.0000',
    }
    assert (await db.get(InventoryReceipt, receipt)).cancelled is False
    assert len(await _entries(db, receipt)) == 1


async def test_cancelling_a_draft_writes_no_entries(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable)
    assert (await client.post(f'{URL}/{receipt}/cancel')).json()['status'] == 'cancelled'
    assert await _entries(db, receipt) == []


async def test_cancel_reverses_legacy_entries_outside_the_header_warehouse(client, db, seeded):
    setup = await seed.setup_inventory(db)
    receipt = await _draft(client)
    await _add(client, receipt, setup.stockable, '4')
    await client.post(f'{URL}/{receipt}/confirm')
    # A post-confirm header edit of the current system left the entry in the old warehouse.
    row = await db.get(InventoryReceipt, receipt)
    row.warehouse = setup.b.warehouse
    row.facility = setup.b.facility
    await db.commit()

    # Its stock check covers the entry's warehouse: take the stock away and cancel is refused.
    await seed.stock(db, setup.stockable, 1, '-4')
    assert (await client.post(f'{URL}/{receipt}/cancel')).status_code == 409

    await seed.stock(db, setup.stockable, 1, '4')
    assert (await client.post(f'{URL}/{receipt}/cancel')).status_code == 200
    assert await _on_hand(db, setup.stockable, 1) == Decimal(0)
    assert await _on_hand(db, setup.stockable, setup.b.warehouse) == Decimal(0)


# ── Facility ──────────────────────────────────────────────────────────────────


async def test_another_facility_cannot_confirm_or_cancel(client, db, seeded):
    setup = await seed.setup_inventory(db)
    admin = CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
    )
    draft = await inv.create_draft(db, inv.RECEIPT, warehouse=1, comment=None, current=admin)
    draft_id = draft.inventory_receipt_id
    await inv.add_line(
        db, inv.RECEIPT, draft_id, product=setup.stockable, quantity=Decimal(2), current=admin
    )
    stranger = _outsider(facility=setup.b.facility)

    with pytest.raises(HTTPException) as exc:
        await inv.confirm_simple(db, inv.RECEIPT, draft_id, current=stranger)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, inv.RECEIPT, draft_id, current=stranger)
    assert exc.value.status_code == 403

    await inv.confirm_simple(db, inv.RECEIPT, draft_id, current=_outsider(facility=1))
    with pytest.raises(HTTPException) as exc:
        await inv.cancel(db, inv.RECEIPT, draft_id, current=stranger)
    assert exc.value.status_code == 403


# ── Lists ─────────────────────────────────────────────────────────────────────


async def test_list_filters_by_status_and_warehouse(client, db, seeded):
    setup = await seed.setup_inventory(db)
    done = await _draft(client)
    await _add(client, done, setup.stockable)
    await client.post(f'{URL}/{done}/confirm')
    draft = await _draft(client, warehouse=setup.b.warehouse)

    everything = (await client.get(URL)).json()
    assert everything['total'] == 2
    assert [item['inventory_receipt_id'] for item in everything['items']] == [draft, done]
    assert [item['status'] for item in everything['items']] == ['draft', 'completed']

    completed = (await client.get(URL, params={'status': 'completed'})).json()
    assert [item['inventory_receipt_id'] for item in completed['items']] == [done]
    at_b = (await client.get(URL, params={'warehouse': setup.b.warehouse})).json()
    assert [item['inventory_receipt_id'] for item in at_b['items']] == [draft]
    assert (await client.get(URL, params={'status': 'paid'})).status_code == 422


# ── Purchase-order view and over-receipt warnings (US4, R7) ───────────────────


async def _linked(
    db,
    order: int,
    lines: list[tuple[int, str]],
    *,
    warehouse: int = 1,
    completed: bool = False,
    cancelled: bool = False,
    stored_ordered: str | None = None,
) -> int:
    """A receipt of `order` with one linked line per `(order line, quantity)`; returns its id.

    Its `quantity_ordered` holds the order line's quantity, unless `stored_ordered` gives the
    current system's complementary value instead.
    """
    receipt = InventoryReceipt(
        facility=(await db.get(Warehouse, warehouse)).facility,
        warehouse=warehouse,
        purchase_order=order,
        serial=None,
        creator=1,
        updater=1,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        completed=completed,
        cancelled=cancelled,
    )
    db.add(receipt)
    await db.flush()
    for order_line, quantity in lines:
        detail = await db.get(PurchaseOrderDetail, order_line)
        db.add(
            InventoryReceiptDetail(
                receipt=receipt.inventory_receipt_id,
                product=detail.product,
                purchase_order_detail=order_line,
                quantity=Decimal(quantity),
                product_code=detail.product_code,
                product_name=detail.product_name,
                quantity_ordered=Decimal(stored_ordered or detail.quantity),
            )
        )
    await db.commit()
    return receipt.inventory_receipt_id


async def test_received_elsewhere_counts_only_other_completed_receipts(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    await _linked(db, order, [(po_line, '3')], completed=True)
    await _linked(db, order, [(po_line, '2')])
    await _linked(db, order, [(po_line, '4')], completed=True, cancelled=True)
    receipt = await _linked(db, order, [(po_line, '1')])

    body = (await client.get(f'{URL}/{receipt}')).json()
    line = body['lines'][0]
    assert (line['purchase_order_detail'], line['quantity']) == (po_line, '1.0000')
    assert line['quantity_ordered'] == '10.0000'
    assert Decimal(line['quantity_received_elsewhere']) == Decimal(3)
    assert body['warnings'] == []


async def test_a_completed_receipt_does_not_count_itself_as_elsewhere(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    receipt = await _linked(db, order, [(po_line, '6')], completed=True)

    line = (await client.get(f'{URL}/{receipt}')).json()['lines'][0]
    assert Decimal(line['quantity_received_elsewhere']) == Decimal(0)


async def test_a_legacy_complementary_draft_gets_no_false_warning(client, db, seeded):
    """The current system stored the remaining 4 as ordered; the check reads the order's 10."""
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    await _linked(db, order, [(po_line, '6')], completed=True)
    legacy = await _linked(db, order, [(po_line, '4')], stored_ordered='4')

    body = (await client.get(f'{URL}/{legacy}')).json()
    assert body['lines'][0]['quantity_ordered'] == '10.0000'
    assert body['warnings'] == []
    # The stored column is only read past, never rewritten.
    stored = await db.get(InventoryReceiptDetail, body['lines'][0]['receipt_detail_id'])
    await db.refresh(stored)
    assert stored.quantity_ordered == Decimal(4)


async def test_an_over_receipt_warns_on_save_read_and_confirm_and_still_confirms(
    client, db, seeded
):
    setup = await seed.setup_inventory(db)
    # Confirming a PO's receipt averages cost into the cost list (spec 031), which must exist.
    await seed.cost(db, setup.stockable, '10')
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    await _linked(db, order, [(po_line, '6')], completed=True)
    receipt = await _linked(db, order, [(po_line, '4')])
    line = (await client.get(f'{URL}/{receipt}')).json()['lines'][0]['receipt_detail_id']

    saved = await client.put(f'{URL}/{receipt}/lines/{line}', json={'quantity': '7'})
    assert saved.status_code == 200, saved.text
    assert len(saved.json()['warnings']) == 1
    assert 'INV' in saved.json()['warnings'][0] and 'by 3' in saved.json()['warnings'][0]

    # Adding the same product merges into the linked line, and warns too.
    merged = await _add(client, receipt, setup.stockable, '1')
    assert 'by 4' in merged.json()['warnings'][0]

    read = (await client.get(f'{URL}/{receipt}')).json()
    assert read['warnings'] == merged.json()['warnings']

    confirmed = await client.post(f'{URL}/{receipt}/confirm')
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['status'] == 'completed'
    assert confirmed.json()['warnings'] == read['warnings']


async def test_a_hand_added_line_on_a_linked_receipt_has_no_link_and_no_warning(client, db, seeded):
    setup = await seed.setup_inventory(db)
    other = await seed.product(db, code='INV2')
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    receipt = await _linked(db, order, [(po_line, '4')])

    response = await _add(client, receipt, other, '50')
    assert response.status_code == 200, response.text
    body = response.json()
    hand = next(line for line in body['lines'] if line['product'] == other)
    assert hand['purchase_order_detail'] is None
    assert hand['quantity_ordered'] == '0.0000'
    assert hand['quantity_received_elsewhere'] is None
    assert body['warnings'] == []


# ── Complementary receipt (US4, R8) ───────────────────────────────────────────


async def test_complementary_creates_a_draft_of_the_under_received_lines(client, db, seeded):
    setup = await seed.setup_inventory(db)
    full = await seed.product(db, code='FULL')
    order, (short, received, elsewhere) = await seed.purchase_order(
        db,
        lines=[
            (setup.stockable, '10', 1),
            (full, '5', 1),
            (setup.stockable, '8', setup.b.warehouse),
        ],
    )
    source = await _linked(db, order, [(short, '6'), (received, '5')], completed=True)

    response = await client.post(f'{URL}/{source}/complementary')
    assert response.status_code == 201, response.text
    body = response.json()
    assert body['inventory_receipt_id'] != source
    assert (body['status'], body['purchase_order']) == ('draft', order)
    assert (body['warehouse'], body['facility']) == (1, 1)
    # Only the under-received line of the receipt's warehouse, at the remaining quantity.
    assert [
        (line['purchase_order_detail'], line['quantity'], line['quantity_ordered'])
        for line in body['lines']
    ] == [(short, '4.0000', '10.0000')]
    assert (body['lines'][0]['product_code'], body['lines'][0]['product_name']) == (
        'INV',
        'Producto INV',
    )
    assert Decimal(body['lines'][0]['quantity_received_elsewhere']) == Decimal(6)
    assert body['warnings'] == []

    stored = await db.get(InventoryReceiptDetail, body['lines'][0]['receipt_detail_id'])
    await db.refresh(stored)
    assert stored.quantity_ordered == Decimal(10)
    assert elsewhere not in {line['purchase_order_detail'] for line in body['lines']}


async def test_complementary_returns_the_open_draft_when_asked_again(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    source = await _linked(db, order, [(po_line, '6')], completed=True)

    created = await client.post(f'{URL}/{source}/complementary')
    assert created.status_code == 201, created.text
    draft = created.json()['inventory_receipt_id']

    again = await client.post(f'{URL}/{source}/complementary')
    assert again.status_code == 200, again.text
    assert again.json()['inventory_receipt_id'] == draft

    # Asked on the open draft itself, it is the one returned.
    itself = await client.post(f'{URL}/{draft}/complementary')
    assert itself.status_code == 200, itself.text
    assert itself.json()['inventory_receipt_id'] == draft

    drafts = (
        (
            await db.execute(
                select(InventoryReceipt).where(
                    InventoryReceipt.purchase_order == order,
                    InventoryReceipt.completed.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(drafts) == 1


async def test_complementary_returns_the_oldest_open_draft(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    source = await _linked(db, order, [(po_line, '6')], completed=True)
    oldest = await _linked(db, order, [(po_line, '1')])
    await _linked(db, order, [(po_line, '2')])

    response = await client.post(f'{URL}/{source}/complementary')
    assert response.status_code == 200, response.text
    assert response.json()['inventory_receipt_id'] == oldest


@pytest.mark.parametrize(
    ('completed', 'cancelled', 'reason'),
    [
        (False, False, 'not completed'),
        (True, True, 'cancelled'),
    ],
)
async def test_complementary_refuses_an_order_not_completed_or_cancelled(
    client, db, seeded, completed, cancelled, reason
):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(
        db, lines=[(setup.stockable, '10', 1)], completed=completed, cancelled=cancelled
    )
    source = await _linked(db, order, [(po_line, '6')], completed=True)

    response = await client.post(f'{URL}/{source}/complementary')
    assert response.status_code == 409
    assert reason in response.json()['detail']


async def test_complementary_refuses_a_fully_received_order(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    await _linked(db, order, [(po_line, '4')], completed=True)
    source = await _linked(db, order, [(po_line, '6')], completed=True)

    response = await client.post(f'{URL}/{source}/complementary')
    assert response.status_code == 409
    assert 'fully received' in response.json()['detail']


async def test_complementary_refuses_a_receipt_without_a_purchase_order(client, db, seeded):
    await seed.setup_inventory(db)
    receipt = await _draft(client)

    response = await client.post(f'{URL}/{receipt}/complementary')
    assert response.status_code == 409
    assert 'no purchase order' in response.json()['detail']
    assert (await client.post(f'{URL}/9999/complementary')).status_code == 404


async def test_complementary_refuses_a_cancelled_source(client, db, seeded):
    """The source is any non-cancelled receipt linked to the order (R8)."""
    setup = await seed.setup_inventory(db)
    order, (po_line,) = await seed.purchase_order(db, lines=[(setup.stockable, '10', 1)])
    source = await _linked(db, order, [(po_line, '6')], completed=True, cancelled=True)

    response = await client.post(f'{URL}/{source}/complementary')
    assert response.status_code == 409
    assert 'cancelled' in response.json()['detail']
