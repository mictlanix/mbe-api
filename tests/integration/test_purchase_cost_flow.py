"""Average cost on the confirm of a purchase order's receipt, and its restore on cancel (spec 031,
US4, SC-004).

Two receipts of one product confirmed at the same moment (US4 scenario 4) are quickstart step 6,
on the test deployment.
"""

from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select

from app.core.constants import COST_PRICE_LIST_ID
from app.models.inventory import InventoryReceipt
from app.models.product import ProductPrice
from app.models.purchases import ProductCostChange, PurchaseOrderDetail
from tests.integration import inventory_seed as seed

ORDERS = '/api/v1/purchase-orders'
RECEIPTS = '/api/v1/inventory-receipts'


async def _cost(db, product: int) -> Decimal | None:
    db.expire_all()
    return (
        await db.execute(
            select(ProductPrice.price).where(
                ProductPrice.product == product, ProductPrice.price_list == COST_PRICE_LIST_ID
            )
        )
    ).scalar_one_or_none()


async def _changes(db, product: int) -> list[ProductCostChange]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(ProductCostChange)
                .where(ProductCostChange.product == product)
                .order_by(ProductCostChange.product_cost_change_id)
            )
        )
        .scalars()
        .all()
    )


async def _receive(client, db, lines, *, price: str = '120', supplier: int) -> tuple[int, int]:
    """Confirm a PO of `lines`, then its single generated receipt; returns (order, receipt)."""
    order, _ = await seed.purchase_order(
        db, lines=lines, completed=False, supplier=supplier, price=price
    )
    response = await client.post(f'{ORDERS}/{order}/confirm')
    assert response.status_code == 200, response.text
    (generated,) = response.json()['receipts']
    receipt = generated['inventory_receipt_id']
    response = await client.post(f'{RECEIPTS}/{receipt}/confirm')
    assert response.status_code == 200, response.text
    return order, receipt


async def _setup(db):  # noqa: ANN202
    setup = await seed.setup_inventory(db)
    vendor = await seed.supplier(db)
    return setup, vendor


def _reproduce(change: ProductCostChange) -> Decimal:
    q, r = change.quantity_on_hand, change.quantity_received
    value = (q * change.previous_cost + r * change.unit_cost) / (q + r)
    return value.quantize(Decimal('0.0001'), ROUND_HALF_UP)


async def test_cost_averages_against_company_wide_stock(client, db, seeded):
    setup, vendor = await _setup(db)
    item = setup.stockable
    await seed.cost(db, item, '100')
    # 10 on hand across both facilities, transit included.
    await seed.stock(db, item, setup.a.warehouse, '4')
    await seed.stock(db, item, setup.b.warehouse, '3')
    await seed.stock(db, item, setup.a.transit, '3')

    _, receipt = await _receive(client, db, [(item, '10', setup.a.warehouse)], supplier=vendor)
    assert await _cost(db, item) == Decimal('110')
    (change,) = await _changes(db, item)
    assert (
        change.receipt,
        change.previous_cost,
        change.new_cost,
        change.quantity_on_hand,
        change.quantity_received,
        change.unit_cost,
        change.creator,
        change.reverted_change,
    ) == (receipt, Decimal(100), Decimal(110), Decimal(10), Decimal(10), Decimal(120), 1, None)
    assert change.purchase_order_detail is not None
    assert _reproduce(change) == change.new_cost


async def test_receipts_without_an_order_line_leave_cost_alone(client, db, seeded):
    setup, vendor = await _setup(db)
    other = await seed.product(db, code='HAND')
    await seed.cost(db, setup.stockable, '100')
    await seed.cost(db, other, '50')

    # A hand-made receipt has no PO.
    hand = (await client.post(RECEIPTS, json={'warehouse': 1})).json()['inventory_receipt_id']
    await client.post(
        f'{RECEIPTS}/{hand}/lines', json={'product': setup.stockable, 'quantity': '5'}
    )
    assert (await client.post(f'{RECEIPTS}/{hand}/confirm')).status_code == 200
    assert await _cost(db, setup.stockable) == Decimal('100')

    # A line added by hand to a PO's receipt has no PO line. It is another product: the same one
    # would merge into the linked line.
    order, _ = await seed.purchase_order(
        db, lines=[(setup.stockable, '1', 1)], completed=False, supplier=vendor, price='100'
    )
    (generated,) = (await client.post(f'{ORDERS}/{order}/confirm')).json()['receipts']
    receipt = generated['inventory_receipt_id']
    await client.post(f'{RECEIPTS}/{receipt}/lines', json={'product': other, 'quantity': '5'})
    assert (await client.post(f'{RECEIPTS}/{receipt}/confirm')).status_code == 200
    assert await _cost(db, other) == Decimal('50')
    assert await _changes(db, other) == []


async def test_a_product_without_a_cost_row_gets_one(client, db, seeded):
    setup, vendor = await _setup(db)
    # The cost list exists, through another product's row.
    await seed.cost(db, 1, '1')
    assert await _cost(db, setup.stockable) is None
    await _receive(client, db, [(setup.stockable, '4', 1)], price='7.5', supplier=vendor)
    assert await _cost(db, setup.stockable) == Decimal('7.5')
    (change,) = await _changes(db, setup.stockable)
    assert (change.previous_cost, change.quantity_on_hand) == (Decimal(0), Decimal(0))


async def test_two_lines_of_one_product_average_in_turn(client, db, seeded):
    setup, vendor = await _setup(db)
    await seed.cost(db, setup.stockable, '100')
    await seed.stock(db, setup.stockable, 1, '10')
    order, lines = await seed.purchase_order(
        db,
        lines=[(setup.stockable, '10', 1), (setup.stockable, '10', 1)],
        completed=False,
        supplier=vendor,
    )
    # Price the second line higher.
    (await db.get(PurchaseOrderDetail, lines[1])).price = Decimal('130')
    await db.commit()
    (generated,) = (await client.post(f'{ORDERS}/{order}/confirm')).json()['receipts']
    await client.post(f'{RECEIPTS}/{generated["inventory_receipt_id"]}/confirm')

    # PO price is 10 on the first line (the seed's default): (10×100 + 10×10)/20 = 55,
    # then (20×55 + 10×130)/30 = 80.
    changes = await _changes(db, setup.stockable)
    assert [(c.quantity_on_hand, c.new_cost) for c in changes] == [
        (Decimal(10), Decimal(55)),
        (Decimal(20), Decimal(80)),
    ]
    assert all(_reproduce(c) == c.new_cost for c in changes)
    assert await _cost(db, setup.stockable) == Decimal(80)


async def test_a_zero_price_is_reported_and_changes_nothing(client, db, seeded):
    setup, vendor = await _setup(db)
    await seed.cost(db, setup.stockable, '100')
    order, _ = await seed.purchase_order(
        db, lines=[(setup.stockable, '1', 1)], completed=False, supplier=vendor, price='0'
    )
    (generated,) = (await client.post(f'{ORDERS}/{order}/confirm')).json()['receipts']
    response = await client.post(f'{RECEIPTS}/{generated["inventory_receipt_id"]}/confirm')
    assert response.json()['warnings'] == ['INV is priced at 0; its cost was not changed']
    assert await _cost(db, setup.stockable) == Decimal('100')


async def test_cancel_restores_the_latest_and_warns_on_an_earlier(client, db, seeded):
    setup, vendor = await _setup(db)
    item = setup.stockable
    await seed.cost(db, item, '100')
    await seed.stock(db, item, 1, '10')

    _, first = await _receive(client, db, [(item, '10', 1)], price='120', supplier=vendor)
    _, second = await _receive(client, db, [(item, '10', 1)], price='140', supplier=vendor)
    # (10×100 + 10×120)/20 = 110, then (20×110 + 10×140)/30 = 120.
    assert await _cost(db, item) == Decimal(120)

    response = await client.post(f'{RECEIPTS}/{first}/cancel')
    assert response.status_code == 200, response.text
    serial = (await db.get(InventoryReceipt, second)).serial
    assert response.json()['warnings'] == [
        f'Cost of INV not restored: changed since by receipt {serial}'
    ]
    assert await _cost(db, item) == Decimal(120)

    response = await client.post(f'{RECEIPTS}/{second}/cancel')
    assert (response.status_code, response.json()['warnings']) == (200, [])
    assert await _cost(db, item) == Decimal(110)
    restore = (await _changes(db, item))[-1]
    assert (restore.receipt, restore.previous_cost, restore.new_cost) == (
        second,
        Decimal(120),
        Decimal(110),
    )
    assert (restore.quantity_on_hand, restore.quantity_received, restore.unit_cost) == (
        None,
        None,
        None,
    )
    assert restore.reverted_change == (await _changes(db, item))[1].product_cost_change_id


async def test_the_cost_history_lists_newest_first(client, db, seeded):
    setup, vendor = await _setup(db)
    await seed.cost(db, setup.stockable, '100')
    await _receive(client, db, [(setup.stockable, '1', 1)], price='110', supplier=vendor)
    await _receive(client, db, [(setup.stockable, '1', 1)], price='120', supplier=vendor)

    response = await client.get(f'{ORDERS}/cost-changes', params={'product': setup.stockable})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == 2
    ids = [item['product_cost_change_id'] for item in body['items']]
    assert ids == sorted(ids, reverse=True)
    assert Decimal(body['items'][0]['unit_cost']) == Decimal(120)

    assert (await client.get(f'{ORDERS}/cost-changes')).status_code == 422
    assert (await client.get(f'{ORDERS}/cost-changes', params={'product': 999})).status_code == 404
