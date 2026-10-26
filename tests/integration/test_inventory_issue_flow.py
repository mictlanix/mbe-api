"""Warehouse issues from draft to confirmed to cancelled, over their routes (spec 029, US2)."""

from decimal import Decimal

from sqlalchemy import select

from app.enums import TransactionType
from app.models.inventory import InventoryIssue, InventoryIssueDetail, LotSerialTracking
from app.models.product import Product
from app.models.supplier import Supplier, SupplierReturn
from app.services import inventory_documents as inv
from app.services import stock_ledger
from tests.integration import inventory_seed as seed

URL = '/api/v1/inventory-issues'
SOURCE = int(TransactionType.INVENTORY_ISSUE)


async def _draft(client, warehouse: int = 1) -> int:
    response = await client.post(URL, json={'warehouse': warehouse})
    assert response.status_code == 201, response.text
    return response.json()['inventory_issue_id']


async def _add(client, issue: int, product: int, quantity: str = '3'):
    return await client.post(
        f'{URL}/{issue}/lines', json={'product': product, 'quantity': quantity}
    )


async def _entries(db, issue: int) -> list[LotSerialTracking]:
    return list(
        (
            await db.execute(
                select(LotSerialTracking)
                .where(LotSerialTracking.source == SOURCE, LotSerialTracking.reference == issue)
                .order_by(LotSerialTracking.lot_serial_tracking_id)
            )
        )
        .scalars()
        .all()
    )


async def _available(db, product: int, warehouse: int = 1) -> Decimal:
    return await stock_ledger.available(db, product=product, warehouse=warehouse)


async def test_issue_over_the_available_stock_is_409_naming_product_and_warehouse(
    client, db, seeded
):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    await seed.reserve(db, setup.stockable, 1, '4')

    issue = await _draft(client)
    await _add(client, issue, setup.stockable, '7')
    response = await client.post(f'{URL}/{issue}/confirm')
    assert response.status_code == 409
    assert 'INV' in response.json()['detail']
    assert '6' in response.json()['detail']
    assert await _entries(db, issue) == []

    line = (await client.get(f'{URL}/{issue}')).json()['lines'][0]['issue_detail_id']
    await client.put(f'{URL}/{issue}/lines/{line}', json={'quantity': '6'})
    response = await client.post(f'{URL}/{issue}/confirm')
    assert response.status_code == 200, response.text
    # No serial was consumed by the refusal.
    assert response.json()['serial'] == 1
    assert await _available(db, setup.stockable) == Decimal(0)


async def test_confirm_posts_one_negative_entry_per_line_with_source_3(client, db, seeded):
    setup = await seed.setup_inventory(db)
    other = await seed.product(db, code='INV2')
    await seed.stock(db, setup.stockable, 1, '10')
    await seed.stock(db, other, 1, '10')

    issue = await _draft(client)
    await _add(client, issue, setup.stockable, '2')
    await _add(client, issue, other, '3')
    body = (await client.post(f'{URL}/{issue}/confirm')).json()
    assert (body['status'], body['supplier_return']) == ('completed', None)

    entries = await _entries(db, issue)
    assert sorted((e.product, e.quantity, e.source) for e in entries) == sorted(
        [(setup.stockable, Decimal(-2), 3), (other, Decimal(-3), 3)]
    )
    # Confirmed is read-only.
    assert (await _add(client, issue, setup.stockable)).status_code == 409


async def test_cancel_posts_the_quantities_back(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '10')
    issue = await _draft(client)
    await _add(client, issue, setup.stockable, '4')
    await client.post(f'{URL}/{issue}/confirm')
    assert await _available(db, setup.stockable) == Decimal(6)

    response = await client.post(f'{URL}/{issue}/cancel')
    assert response.status_code == 200
    assert response.json()['status'] == 'cancelled'
    assert await _available(db, setup.stockable) == Decimal(10)
    assert sorted(e.quantity for e in await _entries(db, issue)) == [Decimal(-4), Decimal(4)]
    assert (await client.post(f'{URL}/{issue}/cancel')).status_code == 409


async def test_an_issue_on_an_in_transit_warehouse_is_422(client, db, seeded):
    setup = await seed.setup_inventory(db)
    assert (await client.post(URL, json={'warehouse': setup.a.transit})).status_code == 422
    issue = await _draft(client)
    response = await client.put(f'{URL}/{issue}', json={'warehouse': setup.b.transit})
    assert response.status_code == 422


async def test_repeated_products_are_summed_against_the_stock(client, db, seeded):
    setup = await seed.setup_inventory(db)
    await seed.stock(db, setup.stockable, 1, '5')
    issue = await _draft(client)
    product = await db.get(Product, setup.stockable)
    for _ in range(2):
        # Legacy drafts can repeat a product; the service merges, so write both rows directly.
        db.add(
            InventoryIssueDetail(
                issue=issue,
                product=setup.stockable,
                quantity=Decimal(3),
                product_code=product.code,
                product_name=product.name,
            )
        )
    await db.commit()

    response = await client.post(f'{URL}/{issue}/confirm')
    assert response.status_code == 409
    assert 'INV' in response.json()['detail']
    assert await _entries(db, issue) == []


async def test_unknown_issue_is_404_at_the_route_and_none_at_the_service(client, db, seeded):
    assert (await client.post(f'{URL}/9999/confirm')).status_code == 404
    assert (await client.post(f'{URL}/9999/cancel')).status_code == 404
    assert await inv.get_document(db, inv.ISSUE, 9999) is None


async def test_a_legacy_supplier_return_is_shown_and_cannot_be_changed(client, db, seeded):
    setup = await seed.setup_inventory(db)
    order, _ = await seed.purchase_order(db, lines=[(setup.stockable, '5', 1)])
    supplier = Supplier(code='S1', name='Proveedor', credit_limit=Decimal(0), credit_days=0)
    db.add(supplier)
    await db.flush()
    supplier_return = SupplierReturn(
        purchase_order=order,
        supplier=supplier.supplier_id,
        creator=1,
        updater=1,
        creation_time=seed.WHEN,
        modification_time=seed.WHEN,
        completed=True,
        cancelled=False,
    )
    db.add(supplier_return)
    await db.flush()
    issue = await _draft(client)
    (await db.get(InventoryIssue, issue)).supplier_return = supplier_return.supplier_return_id
    await db.commit()

    shown = await client.get(f'{URL}/{issue}')
    assert shown.json()['supplier_return'] == supplier_return.supplier_return_id

    updated = await client.put(f'{URL}/{issue}', json={'supplier_return': None, 'comment': 'c'})
    assert updated.status_code == 200
    assert updated.json()['supplier_return'] == supplier_return.supplier_return_id
    assert updated.json()['comment'] == 'c'
