"""Warehouse receipts from draft to confirmed to cancelled, over their routes (spec 029, US1)."""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.deps import CurrentUser
from app.enums import TransactionType
from app.models.inventory import (
    InventoryReceipt,
    InventoryReceiptDetail,
    LotSerialTracking,
)
from app.models.product import Product
from app.services import inventory_documents as inv
from app.services import stock_ledger
from tests.integration import inventory_seed as seed

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

    service = await legacy(setup.non_stockable, '2')
    response = await client.post(f'{URL}/{service}/confirm')
    assert response.status_code == 422
    assert 'SRV' in response.json()['detail']
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
    assert again.status_code == 409
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
