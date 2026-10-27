"""Confirming a purchase order into draft receipts, once (spec 031, US3).

The concurrent double confirm of SC-003 is quickstart step 5, on the test deployment.
"""

from decimal import Decimal

from sqlalchemy import select

from app.models.inventory import InventoryReceipt, InventoryReceiptDetail
from tests.integration import inventory_seed as seed

URL = '/api/v1/purchase-orders'
RECEIPTS = '/api/v1/inventory-receipts'


async def _setup(db):  # noqa: ANN202
    setup = await seed.setup_inventory(db)
    vendor = await seed.supplier(db)
    other = await seed.product(db, code='INV2', supplier=vendor)
    return setup, vendor, other


async def _receipts(db, order: int) -> list[InventoryReceipt]:
    return list(
        (
            await db.execute(
                select(InventoryReceipt)
                .where(InventoryReceipt.purchase_order == order)
                .order_by(InventoryReceipt.inventory_receipt_id)
            )
        )
        .scalars()
        .all()
    )


async def _receipt_lines(db, receipt: int) -> list[InventoryReceiptDetail]:
    return list(
        (
            await db.execute(
                select(InventoryReceiptDetail)
                .where(InventoryReceiptDetail.receipt == receipt)
                .order_by(InventoryReceiptDetail.receipt_detail_id)
            )
        )
        .scalars()
        .all()
    )


async def test_confirm_creates_one_draft_receipt_per_warehouse_once(client, db, seeded):
    setup, vendor, other = await _setup(db)
    order, lines = await seed.purchase_order(
        db,
        lines=[
            (setup.stockable, '5', setup.a.warehouse),
            (other, '2', setup.a.warehouse),
            (setup.stockable, '3', setup.b.warehouse),
        ],
        completed=False,
        supplier=vendor,
    )

    response = await client.post(f'{URL}/{order}/confirm')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'completed'
    receipts = await _receipts(db, order)
    assert [(r.warehouse, r.facility, r.completed, r.serial) for r in receipts] == [
        (setup.a.warehouse, setup.a.facility, False, None),
        (setup.b.warehouse, setup.b.facility, False, None),
    ]
    assert [
        (g['inventory_receipt_id'], g['warehouse'], g['facility'], g['line_count'])
        for g in body['receipts']
    ] == [
        (receipts[0].inventory_receipt_id, setup.a.warehouse, setup.a.facility, 2),
        (receipts[1].inventory_receipt_id, setup.b.warehouse, setup.b.facility, 1),
    ]
    first = await _receipt_lines(db, receipts[0].inventory_receipt_id)
    assert [
        (ln.purchase_order_detail, ln.product, ln.quantity, ln.quantity_ordered, ln.product_code)
        for ln in first
    ] == [
        (lines[0], setup.stockable, Decimal('5'), Decimal('5'), 'INV'),
        (lines[1], other, Decimal('2'), Decimal('2'), 'INV2'),
    ]

    response = await client.post(f'{URL}/{order}/confirm')
    assert response.status_code == 409
    assert len(await _receipts(db, order)) == 2


async def test_confirm_takes_what_live_receipts_already_hold(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    order, lines = await seed.purchase_order(
        db, lines=[(setup.stockable, '5', setup.a.warehouse)], completed=False, supplier=vendor
    )
    # A hand-made receipt already linked to the line holds 2.
    held = InventoryReceipt(
        facility=1,
        warehouse=1,
        purchase_order=order,
        creator=1,
        updater=1,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        completed=False,
        cancelled=False,
    )
    db.add(held)
    await db.flush()
    db.add(
        InventoryReceiptDetail(
            receipt=held.inventory_receipt_id,
            product=setup.stockable,
            purchase_order_detail=lines[0],
            quantity=Decimal('2'),
            quantity_ordered=Decimal('5'),
        )
    )
    await db.commit()

    await client.post(f'{URL}/{order}/confirm')
    generated = (await _receipts(db, order))[1]
    (line,) = await _receipt_lines(db, generated.inventory_receipt_id)
    assert (line.quantity, line.quantity_ordered) == (Decimal('3'), Decimal('5'))


async def test_confirm_refusals(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    empty, _ = await seed.purchase_order(db, lines=[], completed=False, supplier=vendor)
    assert (await client.post(f'{URL}/{empty}/confirm')).status_code == 409
    orphan, _ = await seed.purchase_order(db, lines=[(setup.stockable, '1', 1)], completed=False)
    assert (await client.post(f'{URL}/{orphan}/confirm')).status_code == 409
    zero, _ = await seed.purchase_order(
        db, lines=[(setup.stockable, '0', 1)], completed=False, supplier=vendor
    )
    assert (await client.post(f'{URL}/{zero}/confirm')).status_code == 409
    assert (await client.post(f'{URL}/999/confirm')).status_code == 404


async def test_a_zero_price_confirms_with_a_warning(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    order, _ = await seed.purchase_order(
        db, lines=[(setup.stockable, '1', 1)], completed=False, supplier=vendor, price='0'
    )
    response = await client.post(f'{URL}/{order}/confirm')
    assert response.status_code == 200
    assert response.json()['warnings'] == ['INV is priced at 0']


async def test_a_confirmed_po_is_read_only(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    order, lines = await seed.purchase_order(
        db, lines=[(setup.stockable, '1', 1)], completed=False, supplier=vendor
    )
    await client.post(f'{URL}/{order}/confirm')

    assert (await client.put(f'{URL}/{order}', json={'comment': 'x'})).status_code == 409
    response = await client.post(
        f'{URL}/{order}/lines', json={'product': setup.stockable, 'quantity': '1'}
    )
    assert response.status_code == 409
    response = await client.put(f'{URL}/{order}/lines/{lines[0]}', json={'quantity': '2'})
    assert response.status_code == 409
    assert (await client.delete(f'{URL}/{order}/lines/{lines[0]}')).status_code == 409
    assert (await client.post(f'{URL}/{order}/cancel')).status_code == 409


async def test_receiving_progress_and_the_complementary_receipt(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    order, lines = await seed.purchase_order(
        db, lines=[(setup.stockable, '5', setup.a.warehouse)], completed=False, supplier=vendor
    )
    await client.post(f'{URL}/{order}/confirm')
    (receipt,) = await _receipts(db, order)
    (line,) = await _receipt_lines(db, receipt.inventory_receipt_id)

    detail = (await client.get(f'{URL}/{order}')).json()['lines'][0]
    assert (detail['quantity_received'], detail['quantity_pending']) == ('0', '5.0000')

    await client.put(
        f'{RECEIPTS}/{receipt.inventory_receipt_id}/lines/{line.receipt_detail_id}',
        json={'quantity': '2'},
    )
    response = await client.post(f'{RECEIPTS}/{receipt.inventory_receipt_id}/confirm')
    assert response.status_code == 200, response.text
    detail = (await client.get(f'{URL}/{order}')).json()['lines'][0]
    assert (Decimal(detail['quantity_received']), Decimal(detail['quantity_pending'])) == (
        Decimal('2'),
        Decimal('3'),
    )

    response = await client.post(f'{RECEIPTS}/{receipt.inventory_receipt_id}/complementary')
    assert response.status_code == 201, response.text
    assert [Decimal(ln['quantity']) for ln in response.json()['lines']] == [Decimal('3')]


async def test_a_receipt_of_a_po_keeps_its_warehouse(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    order, _ = await seed.purchase_order(
        db, lines=[(setup.stockable, '1', setup.a.warehouse)], completed=False, supplier=vendor
    )
    await client.post(f'{URL}/{order}/confirm')
    (receipt,) = await _receipts(db, order)
    second = await seed.warehouse(db, facility=1, code='W1B')
    response = await client.put(
        f'{RECEIPTS}/{receipt.inventory_receipt_id}', json={'warehouse': second}
    )
    assert response.status_code == 409
    assert response.json()['detail'] == "A purchase order's receipt keeps its warehouse"

    # A hand-made receipt can still move.
    hand = (await client.post(RECEIPTS, json={'warehouse': 1})).json()['inventory_receipt_id']
    response = await client.put(f'{RECEIPTS}/{hand}', json={'warehouse': second})
    assert (response.status_code, response.json()['warehouse']) == (200, second)


async def test_non_stockable_lines_get_no_receipt_and_no_progress(client, db, seeded):
    setup, vendor, _ = await _setup(db)
    only, _ = await seed.purchase_order(
        db, lines=[(setup.non_stockable, '1', 1)], completed=False, supplier=vendor
    )
    response = await client.post(f'{URL}/{only}/confirm')
    assert (response.status_code, response.json()['receipts']) == (200, [])
    assert await _receipts(db, only) == []
    detail = (await client.get(f'{URL}/{only}')).json()['lines'][0]
    assert (detail['quantity_received'], detail['quantity_pending']) == (None, None)

    mixed, _ = await seed.purchase_order(
        db,
        lines=[(setup.stockable, '4', 1), (setup.non_stockable, '1', 1)],
        completed=False,
        supplier=vendor,
    )
    await client.post(f'{URL}/{mixed}/confirm')
    (receipt,) = await _receipts(db, mixed)
    (line,) = await _receipt_lines(db, receipt.inventory_receipt_id)
    await client.put(
        f'{RECEIPTS}/{receipt.inventory_receipt_id}/lines/{line.receipt_detail_id}',
        json={'quantity': '1'},
    )
    await client.post(f'{RECEIPTS}/{receipt.inventory_receipt_id}/confirm')

    # The complementary receipt leaves the non-stockable line out, so it can be confirmed.
    response = await client.post(f'{RECEIPTS}/{receipt.inventory_receipt_id}/complementary')
    assert response.status_code == 201, response.text
    complementary = response.json()
    assert [ln['product'] for ln in complementary['lines']] == [setup.stockable]
    confirmed = await client.post(f'{RECEIPTS}/{complementary["inventory_receipt_id"]}/confirm')
    assert confirmed.status_code == 200, confirmed.text
