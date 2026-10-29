"""Purchase-order drafts, their lines, and lines taken from approved requests (spec 031, US2).

SQLite cannot show row-lock races, so SC-001 is checked here in sequence (a line taken by one PO is
`skipped` by the next) and concurrently on the test deployment (quickstart step 4).
"""

from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.deps import CurrentUser
from app.enums import CurrencyCode
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail, PurchaseRequestDetail
from app.schemas.purchases import OrderLineAdd
from app.services import purchase_order_service as service
from tests.integration import inventory_seed as seed

URL = '/api/v1/purchase-orders'


async def _order(client, supplier: int, **fields) -> int:
    response = await client.post(URL, json={'supplier': supplier, **fields})
    assert response.status_code == 201, response.text
    return response.json()['purchase_order_id']


async def _vendors(db):  # noqa: ANN202
    """Suppliers A and B, a product of each, and a product of no supplier, with costs."""
    a = await seed.supplier(db, code='A', name='Alfa')
    b = await seed.supplier(db, code='B', name='Beta')
    pa = await seed.product(db, code='PA', supplier=a)
    pb = await seed.product(db, code='PB', supplier=b)
    pn = await seed.product(db, code='PN')
    await seed.cost(db, pa, '10')
    await seed.cost(db, pb, '2.5')
    return a, b, pa, pb, pn


# ── Drafts and hand lines (FR-020 to FR-022) ──────────────────────────────────


async def test_a_draft_po_and_its_hand_lines(client, db, seeded):
    a, b, pa, _, pn = await _vendors(db)
    order = await _order(client, a, comment='Urgente')
    body = (await client.get(f'{URL}/{order}')).json()
    assert (body['status'], body['supplier_name'], body['line_count'], body['total']) == (
        'draft',
        'Alfa',
        0,
        '0.00',
    )

    response = await client.post(f'{URL}/{order}/lines', json={'product': pa, 'quantity': '3'})
    assert response.status_code == 200, response.text
    line = response.json()['lines'][0]
    # The price defaults to cost, the warehouse to the point of sale's, the tax to the product's.
    assert (line['price'], line['warehouse'], line['tax_rate'], line['amount']) == (
        '10.0000000',
        1,
        '0.1600',
        '30.00',
    )
    assert response.json()['warnings'] == []

    response = await client.post(
        f'{URL}/{order}/lines', json={'product': pn, 'quantity': '1', 'price': '4', 'tax_rate': '0'}
    )
    body = response.json()
    assert (body['subtotal'], body['tax'], body['total']) == ('34.00', '4.80', '38.80')

    row = (
        (
            await db.execute(
                select(PurchaseOrderDetail).where(PurchaseOrderDetail.purchase_order == order)
            )
        )
        .scalars()
        .first()
    )
    assert (row.currency, row.exchange_rate, row.discount, row.tax_included) == (
        CurrencyCode.MXN,
        Decimal(1),
        Decimal(0),
        False,
    )
    header = await db.get(PurchaseOrder, order)
    assert (header.approved, header.invoice_number) == (False, None)

    line_id = line['purchase_order_detail_id']
    response = await client.put(
        f'{URL}/{order}/lines/{line_id}', json={'quantity': '5', 'price': '9.5'}
    )
    assert response.json()['lines'][0]['amount'] == '47.50'
    response = await client.put(f'{URL}/{order}', json={'supplier': b, 'comment': None})
    assert (response.json()['supplier'], response.json()['comment']) == (b, None)
    response = await client.delete(f'{URL}/{order}/lines/{line_id}')
    assert response.json()['line_count'] == 1


async def test_a_product_without_cost_defaults_to_zero_with_a_warning(client, db, seeded):
    a, _, _, _, pn = await _vendors(db)
    order = await _order(client, a)
    response = await client.post(f'{URL}/{order}/lines', json={'product': pn, 'quantity': '1'})
    assert Decimal(response.json()['lines'][0]['price']) == 0
    assert response.json()['warnings'] == ['PN has no cost; priced at 0']


async def test_line_and_header_refusals(client, db, seeded):
    setup = await seed.setup_inventory(db)
    a, *_ = await _vendors(db)
    unbuyable = await seed.product(db, code='NOBUY', purchasable=False)
    order = await _order(client, a)

    post = f'{URL}/{order}/lines'
    assert (
        await client.post(post, json={'product': unbuyable, 'quantity': '1'})
    ).status_code == 422
    assert (await client.post(post, json={'product': 999, 'quantity': '1'})).status_code == 404
    response = await client.post(
        post, json={'product': 1, 'quantity': '1', 'warehouse': setup.a.transit}
    )
    assert response.status_code == 422
    assert (await client.post(URL, json={'supplier': 999})).status_code == 404
    assert (await client.put(f'{URL}/{order}', json={'supplier': 999})).status_code == 404
    response = await client.put(f'{URL}/{order}/lines/999', json={'quantity': '1'})
    assert response.status_code == 404
    assert response.json()['code'] == 'resource.not_found'


async def test_a_line_needs_a_warehouse_when_the_caller_has_no_point_of_sale(
    client, db, seeded, current_user
):
    a, _, pa, _, _ = await _vendors(db)
    order = await _order(client, a)
    nowhere = CurrentUser(**{**current_user.__dict__, 'point_sale_id': None})
    with pytest.raises(HTTPException) as caught:
        await service.add_line(db, order, OrderLineAdd(product=pa, quantity='1'), current=nowhere)
    assert (caught.value.status_code, caught.value.detail) == (422, 'A warehouse is required')


# ── Lines from one request (FR-023) ───────────────────────────────────────────


async def test_adding_a_requests_pending_lines(client, db, seeded):
    setup = await seed.setup_inventory(db)
    a, b, pa, pb, _ = await _vendors(db)
    request, lines = await seed.purchase_request(
        db,
        warehouse=1,
        lines=[(pa, '4', 1), (pb, '2', setup.b.warehouse), (pa, '1', 1)],
        state='approved',
        serial=1,
    )
    # The third line is unmarked.
    (await db.get(PurchaseRequestDetail, lines[2])).to_purchase = False
    await db.commit()

    order = await _order(client, a)
    response = await client.post(f'{URL}/{order}/request-lines', json={'purchase_request': request})
    assert response.status_code == 200, response.text
    body = response.json()
    taken = [
        (
            line['purchase_request_detail'],
            line['purchase_request'],
            line['quantity'],
            line['warehouse'],
            line['price'],
        )
        for line in body['lines']
    ]
    assert taken == [
        (lines[0], request, '4.0000', 1, '10.0000000'),
        (lines[1], request, '2.0000', setup.b.warehouse, '2.5000000'),
    ]
    assert body['warnings'] == ['PB is supplied by another supplier']
    assert body['skipped'] == [
        {'purchase_request_detail': lines[2], 'reason': 'Not marked to purchase'}
    ]

    # Again: everything is taken or unmarked.
    response = await client.post(f'{URL}/{order}/request-lines', json={'purchase_request': request})
    assert response.status_code == 409


async def test_only_an_approved_requests_lines_are_added(client, db, seeded):
    a, _, pa, _, _ = await _vendors(db)
    request, _ = await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1)], state='submitted', serial=1
    )
    order = await _order(client, a)
    response = await client.post(f'{URL}/{order}/request-lines', json={'purchase_request': request})
    assert response.status_code == 409
    response = await client.post(f'{URL}/{order}/request-lines', json={'purchase_request': 999})
    assert response.status_code == 404
    assert response.json()['code'] == 'resource.not_found'


async def test_a_line_on_one_po_is_skipped_by_the_next(client, db, seeded):
    """SC-001 in sequence."""
    a, _, pa, _, _ = await _vendors(db)
    request, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1), (pa, '2', 1)], state='approved', serial=1
    )
    first = await _order(client, a)
    await client.post(f'{URL}/{first}/request-lines', json={'purchase_request': request})
    # Remove one line from the first PO: only that one is pending again.
    first_lines = (await client.get(f'{URL}/{first}')).json()['lines']
    await client.delete(f'{URL}/{first}/lines/{first_lines[1]["purchase_order_detail_id"]}')

    second = await _order(client, a)
    response = await client.post(
        f'{URL}/{second}/request-lines', json={'purchase_request': request}
    )
    body = response.json()
    assert [line['purchase_request_detail'] for line in body['lines']] == [lines[1]]
    assert body['skipped'] == [
        {'purchase_request_detail': lines[0], 'reason': f'Already on purchase order {first}'}
    ]


# ── To purchase by supplier (FR-024, FR-025) ──────────────────────────────────


async def test_to_purchase_groups_by_supplier_and_facility(client, db, seeded):
    setup = await seed.setup_inventory(db)
    a, b, pa, pb, pn = await _vendors(db)
    first, first_lines = await seed.purchase_request(
        db,
        warehouse=1,
        lines=[(pa, '2', 1), (pb, '3', 1), (pn, '1', 1)],
        state='approved',
        serial=1,
    )
    second, second_lines = await seed.purchase_request(
        db,
        warehouse=setup.b.warehouse,
        lines=[(pa, '5', setup.b.warehouse)],
        state='approved',
        serial=1,
    )
    # Excluded: an unmarked line, a draft's, a submitted one's, a cancelled one's, one on a live PO.
    await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1)], state='approved', serial=2, to_purchase=False
    )
    await seed.purchase_request(db, warehouse=1, lines=[(pa, '1', 1)])
    await seed.purchase_request(db, warehouse=1, lines=[(pa, '1', 1)], state='submitted', serial=3)
    await seed.purchase_request(db, warehouse=1, lines=[(pa, '1', 1)], state='cancelled', serial=4)
    _, ordered = await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1)], state='approved', serial=5
    )
    await seed.purchase_order(db, lines=[(pa, '1', 1)], completed=False, request_lines=ordered)

    body = (await client.get(f'{URL}/to-purchase')).json()
    summary = [
        (
            g['facility'],
            g['supplier'],
            g['supplier_name'],
            g['line_count'],
            Decimal(g['estimated_total']),
        )
        for g in body['groups']
    ]
    assert summary == [
        (1, a, 'Alfa', 1, Decimal('20')),
        (1, b, 'Beta', 1, Decimal('7.5')),
        (1, None, None, 1, Decimal('0')),
        (setup.b.facility, a, 'Alfa', 1, Decimal('50')),
    ]
    line = body['groups'][0]['lines'][0]
    assert (
        line['purchase_request_detail_id'],
        line['purchase_request'],
        line['serial'],
        line['product_code'],
    ) == (
        first_lines[0],
        first,
        1,
        'PA',
    )
    filtered = (
        await client.get(f'{URL}/to-purchase', params={'facility': setup.b.facility})
    ).json()
    assert [g['facility'] for g in filtered['groups']] == [setup.b.facility]

    response = await client.post(
        f'{URL}/to-purchase', json={'supplier': a, 'facility': setup.b.facility}
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert (created['supplier'], created['status'], created['skipped']) == (a, 'draft', [])
    assert [
        (ln['purchase_request_detail'], ln['quantity'], ln['price']) for ln in created['lines']
    ] == [(second_lines[0], '5.0000', '10.0000000')]

    # Nothing left for that group, and the group is gone from the list.
    response = await client.post(
        f'{URL}/to-purchase', json={'supplier': a, 'facility': setup.b.facility}
    )
    assert response.status_code == 409
    body = (await client.get(f'{URL}/to-purchase')).json()
    assert len(body['groups']) == 3


async def test_a_to_purchase_that_lost_the_race_creates_no_po(client, db, seeded, monkeypatch):
    """The group's lines are taken between the pre-read and the lock: the re-check refuses before
    the PO exists, so no PO number is used up."""
    a, _, pa, _, _ = await _vendors(db)
    _, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1)], state='approved', serial=1
    )
    lock = service.requests.lock_request_lines
    new_orders: list = []

    async def taken_then_locked(session, ids):  # noqa: ANN001, ANN202
        await seed.purchase_order(
            session, lines=[(pa, '1', 1)], completed=False, supplier=a, request_lines=lines
        )
        await lock(session, ids)

    def new_order(*args, **kwargs):  # noqa: ANN202
        new_orders.append(args)
        return original_new_order(*args, **kwargs)

    original_new_order = service._new_order
    monkeypatch.setattr(service.requests, 'lock_request_lines', taken_then_locked)
    monkeypatch.setattr(service, '_new_order', new_order)
    response = await client.post(f'{URL}/to-purchase', json={'supplier': a, 'facility': 1})
    assert (response.status_code, response.json()['detail']) == (
        409,
        'Nothing is pending for this supplier and facility',
    )
    assert new_orders == []


async def test_to_purchase_needs_a_supplier(client, db, seeded):
    response = await client.post(f'{URL}/to-purchase', json={'supplier': None, 'facility': 1})
    assert response.status_code == 422


# ── Release (FR-028) ──────────────────────────────────────────────────────────


async def test_cancelling_a_draft_po_keeps_its_lines_and_frees_the_request_lines(
    client, db, seeded
):
    a, _, pa, _, _ = await _vendors(db)
    request, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(pa, '1', 1)], state='approved', serial=1
    )
    order = await _order(client, a)
    await client.post(f'{URL}/{order}/request-lines', json={'purchase_request': request})

    response = await client.post(f'{URL}/{order}/cancel')
    assert (response.status_code, response.json()['status']) == (200, 'cancelled')
    assert response.json()['line_count'] == 1
    assert (await client.post(f'{URL}/{order}/cancel')).status_code == 409
    assert (
        await client.post(f'{URL}/{order}/lines', json={'product': pa, 'quantity': '1'})
    ).status_code == 409

    again = await _order(client, a)
    response = await client.post(f'{URL}/{again}/request-lines', json={'purchase_request': request})
    assert [line['purchase_request_detail'] for line in response.json()['lines']] == lines


# ── Lists (FR-030) ────────────────────────────────────────────────────────────


async def test_lists_default_to_the_callers_live_orders(client, db, seeded):
    a, b, *_ = await _vendors(db)
    mine = await _order(client, a)
    gone = await _order(client, b)
    await client.post(f'{URL}/{gone}/cancel')
    seeded_order, _ = await seed.purchase_order(db, lines=[], completed=True, supplier=b)

    ids = [item['purchase_order_id'] for item in (await client.get(URL)).json()['items']]
    # The seeded order's creator is employee 1 as well, so it is the caller's too.
    assert ids == [seeded_order, mine]
    assert [
        i['purchase_order_id']
        for i in (await client.get(URL, params={'status': 'cancelled'})).json()['items']
    ] == [gone]
    assert (await client.get(URL, params={'supplier': a})).json()['total'] == 1
    assert (await client.get(URL, params={'status': 'completed'})).json()['total'] == 1
    assert (await client.get(URL, params={'id': mine})).json()['total'] == 1
    assert (await client.get(URL, params={'creator': 1, 'all': 'true'})).json()['total'] == 2
    assert (await client.get(URL, params={'status': 'approved'})).status_code == 422
