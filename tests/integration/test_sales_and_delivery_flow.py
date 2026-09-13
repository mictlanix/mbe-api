"""The sales and delivery write paths, driven through the API against a real database.

`POST /api/v1/delivery-orders` raised `AttributeError` on **every** call for as long as #138 was
shipped, and the endpoint had six tests. All six patched `create_from_sales_order`, so all six
passed — they asserted that the router forwards its arguments, which it did. Nothing ran the
service.

`test_raising_a_delivery_order_from_a_sale` is the test that would have failed. It is worth knowing
which assertion does the work: not a careful one about quantities, just `status_code == 201`. The
bug was not subtle once the code ran at all.
"""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DeliveryOrderStatus
from app.models.logistics import DeliveryOrder, DeliveryOrderDetail
from tests.integration.seed import seed_sales_order


async def test_a_sales_order_is_opened_lined_and_read(client: AsyncClient, seeded: None) -> None:
    """FR-010 — a draft opens on configured defaults, so the service has to supply them."""
    created = await client.post('/api/v1/sales-orders', json={'customer': 1})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']
    assert created.json()['status'] == 'draft'

    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'quantity': '2'}
    )
    assert lined.status_code == 200, lined.text
    line = lined.json()['lines'][0]
    assert line['product_code'] == 'P1'
    # #145 — the unit is read through the product and attached, not stored on the line.
    assert line['unit_of_measurement']['id'] == 'H87'
    # #157 — so is the photo, resolved to the URL the product endpoints serve.
    assert line['photo'] == '/images/p1.png'

    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert read.status_code == 200, read.text
    # 2 × 100 = 200, plus 16% = 232. Computed by the real code from real rows.
    assert read.json()['total'] == '232.00'


async def test_mixed_and_delivery_are_distinguishable_after_a_reload(
    client: AsyncClient, seeded: None
) -> None:
    """#170 — the third state the ship-to address cannot carry.

    The point of sale asks how the goods reach the customer, and the answer has three values. It was
    encoded into `ship_to` — the facility's address for a counter pickup, the customer's otherwise —
    which makes *delivery and mixed byte-identical*. A sale reopened in a new session came back as
    plain `delivery`, and the units meant for the counter read as an unassigned remainder.

    So the assertion that matters is not that the field round-trips; it is that two sales with the
    **same `ship_to`** come back saying different things.
    """
    mixed = await client.post(
        '/api/v1/sales-orders', json={'customer': 1, 'ship_to': 1, 'fulfillment_intent': 2}
    )
    delivery = await client.post(
        '/api/v1/sales-orders', json={'customer': 1, 'ship_to': 1, 'fulfillment_intent': 1}
    )
    assert mixed.status_code == 201, mixed.text
    assert delivery.status_code == 201, delivery.text
    assert mixed.json()['ship_to'] == delivery.json()['ship_to']

    # Reopened, as a restarted client would.
    reread = [
        (await client.get(f'/api/v1/sales-orders/{r.json()["sales_order_id"]}')).json()
        for r in (mixed, delivery)
    ]

    assert [o['fulfillment_intent'] for o in reread] == [2, 1]


async def test_a_sale_that_never_stated_an_intent_reports_null(
    client: AsyncClient, seeded: None
) -> None:
    """`null`, never `delivery`. Migration 017 ships the column empty because not one of the
    335,763 existing sales orders has a `ship_to` pointing at a facility address, so inferring
    would stamp every one of them `delivery` — a confident wrong answer in place of "unknown"."""
    created = await client.post('/api/v1/sales-orders', json={'customer': 1})

    assert created.status_code == 201, created.text
    assert created.json()['fulfillment_intent'] is None


async def test_the_intent_is_editable_while_the_sale_is_a_draft(
    client: AsyncClient, seeded: None
) -> None:
    """The cashier can change their mind before confirming, and can take the answer back."""
    order_id = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'fulfillment_intent': 1})
    ).json()['sales_order_id']

    changed = await client.put(
        f'/api/v1/sales-orders/{order_id}', json={'fulfillment_intent': 0}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()['fulfillment_intent'] == 0

    cleared = await client.put(
        f'/api/v1/sales-orders/{order_id}', json={'fulfillment_intent': None}
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()['fulfillment_intent'] is None


async def test_a_list_row_carries_the_customers_own_name(
    client: AsyncClient, seeded: None
) -> None:
    """#172 — `customer_name` is the per-document override and is null on an ordinary sale, so a
    list that renders it shows nothing. `customer_display_name` is joined from the customer."""
    await client.post('/api/v1/sales-orders', json={'customer': 1})

    listed = await client.get('/api/v1/sales-orders')

    assert listed.status_code == 200, listed.text
    row = listed.json()['items'][0]
    assert row['customer_display_name'] == 'Cliente Uno'
    # The override keeps its documented meaning: untouched, so still null.
    assert row['customer_name'] is None


async def test_the_override_and_the_customers_name_stay_distinguishable(
    client: AsyncClient, seeded: None
) -> None:
    """Setting an override must not overwrite the joined name — that is why this is a new field
    rather than a fallback on `customer_name`."""
    created = await client.post(
        '/api/v1/sales-orders', json={'customer': 1, 'customer_name': 'Otro Nombre'}
    )
    assert created.status_code == 201, created.text

    row = (await client.get('/api/v1/sales-orders')).json()['items'][0]

    assert row['customer_name'] == 'Otro Nombre'
    assert row['customer_display_name'] == 'Cliente Uno'


async def test_a_sale_is_searchable_by_its_customers_name(
    client: AsyncClient, seeded: None
) -> None:
    """The other half of #172: `search` matched only the override, so it matched a null column and
    returned an empty page rather than erroring — a documented filter that quietly found nothing."""
    await client.post('/api/v1/sales-orders', json={'customer': 1})

    found = await client.get('/api/v1/sales-orders', params={'search': 'Cliente'})

    assert found.status_code == 200, found.text
    assert found.json()['total'] == 1
    assert found.json()['items'][0]['customer_display_name'] == 'Cliente Uno'


async def test_search_still_matches_the_override_and_still_misses_non_matches(
    client: AsyncClient, seeded: None
) -> None:
    """The customer-name clause is ORed in, not swapped for the override — and a search that
    matches neither must still come back empty rather than matching everything."""
    await client.post(
        '/api/v1/sales-orders', json={'customer': 1, 'customer_name': 'Mostrador'}
    )

    by_override = await client.get('/api/v1/sales-orders', params={'search': 'Mostrador'})
    assert by_override.json()['total'] == 1

    no_match = await client.get('/api/v1/sales-orders', params={'search': 'Zzzz'})
    assert no_match.json()['total'] == 0


async def test_the_product_lookup_answers_with_price_and_unit(
    client: AsyncClient, seeded: None
) -> None:
    response = await client.get(
        '/api/v1/sales-orders/product-lookup', params={'pattern': 'Producto', 'customer': 1}
    )

    assert response.status_code == 200, response.text
    row = response.json()[0]
    assert row['price'] == '100.0000'
    assert row['unit_of_measurement']['id'] == 'H87'
    assert row['photo'] == '/images/p1.png'


async def test_raising_a_delivery_order_from_a_sale(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#149 — this is the call that raised `AttributeError` for every caller, subset or not."""
    sales_order = await seed_sales_order(db, completed=True)

    response = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body['status'] == DeliveryOrderStatus.DRAFT
    # Everything the sale still owes, which is its whole line.
    assert [line['quantity'] for line in body['lines']] == ['10.0000']
    # #147 — derived from the lines, so this exercises the join as well as the create.
    assert body['sales_orders'] == [sales_order]


async def test_raising_one_for_a_named_subset(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The #138 path the shadowed parameter was supposed to serve, never once exercised."""
    sales_order = await seed_sales_order(db, completed=True)
    line_id = (
        await db.execute(
            select(DeliveryOrderDetail.sales_order_detail).where(
                DeliveryOrderDetail.sales_order_detail.is_not(None)
            )
        )
    ).scalar_one_or_none()
    assert line_id is None, 'no delivery order should exist yet'

    from app.models.sales import SalesOrderDetail

    sales_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sales_order
            )
        )
    ).scalar_one()

    response = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': sales_line, 'quantity': '4'}],
        },
    )

    assert response.status_code == 201, response.text
    assert [line['quantity'] for line in response.json()['lines']] == ['4.0000']


async def test_over_claiming_a_line_is_refused_with_422(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """`narrow_to_requested`'s refusal, reached through the endpoint for the first time."""
    sales_order = await seed_sales_order(db, completed=True)
    from app.models.sales import SalesOrderDetail

    sales_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sales_order
            )
        )
    ).scalar_one()

    response = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': sales_line, 'quantity': '99'}],
        },
    )

    assert response.status_code == 422, response.text
    assert 'undelivered' in response.json()['detail']


async def test_a_deleted_line_can_be_put_back(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#163 — the round trip that was impossible: create a subset, drop it, add it again.

    Worth driving against a database rather than mocks: the re-add crosses `sales_orders_of` (which
    finds no origin, the draft now being empty) and `_covered_quantities` (which must no longer
    count the deleted row), and both are SQL.
    """
    sales_order = await seed_sales_order(db, completed=True)
    from app.models.sales import SalesOrderDetail

    sales_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sales_order
            )
        )
    ).scalar_one()

    raised = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': sales_line, 'quantity': '4'}],
        },
    )
    assert raised.status_code == 201, raised.text
    delivery = raised.json()['delivery_order_id']
    line_id = raised.json()['lines'][0]['delivery_order_detail_id']

    duplicate = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': sales_line, 'quantity': '1'},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert f'as line {line_id}' in duplicate.json()['detail']

    dropped = await client.delete(f'/api/v1/delivery-orders/{delivery}/lines/{line_id}')
    assert dropped.status_code == 200, dropped.text
    assert dropped.json()['lines'] == []

    restored = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': sales_line, 'quantity': '10'},
    )

    assert restored.status_code == 201, restored.text
    body = restored.json()
    assert [line['sales_order_detail'] for line in body['lines']] == [sales_line]
    # The whole ten, not the four the deleted row had claimed: coverage no longer counts it.
    assert body['lines'][0]['quantity'] == '10.0000'
    assert body['lines'][0]['open_quantity'] == '10.0000'


async def test_adding_more_than_the_sale_still_owes_is_refused_with_422(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The bound is `_covered_quantities`, so the line already on the order counts against it."""
    sales_order = await seed_sales_order(db, completed=True)
    from app.models.sales import SalesOrderDetail

    sales_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sales_order
            )
        )
    ).scalar_one()

    # Four of the ten go to the first destination and stay there.
    first = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': sales_line, 'quantity': '4'}],
        },
    )
    assert first.status_code == 201, first.text

    second = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': sales_line, 'quantity': '6'}],
        },
    )
    assert second.status_code == 201, second.text
    delivery = second.json()['delivery_order_id']
    line_id = second.json()['lines'][0]['delivery_order_detail_id']
    dropped = await client.delete(f'/api/v1/delivery-orders/{delivery}/lines/{line_id}')
    assert dropped.status_code == 200, dropped.text

    response = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': sales_line, 'quantity': '7'},
    )

    assert response.status_code == 422, response.text
    assert 'left to deliver' in response.json()['detail']
    assert response.json()['detail'].startswith('The sales order line has 6')


async def test_one_shipment_can_consolidate_two_sales_of_the_same_customer(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """A delivery order and a sales order are many-to-many, in both directions.

    #163 shipped a guard comparing an added line's sale against the one already on the order, which
    refused this outright. 261 of the 27,921 sale-linked delivery orders in the production database
    carry two or three sales, so the check forbade an operation the business does — and the read
    path had always allowed it: the filter matches a delivery order under *either* sale, which is
    what this asserts at the end.
    """
    from app.models.sales import SalesOrderDetail

    async def line_of(sales_order: int) -> int:
        return (
            await db.execute(
                select(SalesOrderDetail.sales_order_detail_id).where(
                    SalesOrderDetail.sales_order == sales_order
                )
            )
        ).scalar_one()

    first_sale = await seed_sales_order(db, completed=True)
    second_sale = await seed_sales_order(db, completed=True)

    raised = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': first_sale,
            'lines': [{'sales_order_detail': await line_of(first_sale), 'quantity': '4'}],
        },
    )
    assert raised.status_code == 201, raised.text
    delivery = raised.json()['delivery_order_id']

    added = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': await line_of(second_sale), 'quantity': '3'},
    )

    assert added.status_code == 201, added.text
    assert sorted(line['quantity'] for line in added.json()['lines']) == ['3.0000', '4.0000']
    # The response names both. As a scalar filled by `min()` this reported the lower id alone, and
    # a client could not tell that from a shipment carrying one sale.
    assert added.json()['sales_orders'] == sorted([first_sale, second_sale])

    # The point of deriving the link from the lines: the shipment answers to both sales.
    for sale in (first_sale, second_sale):
        found = await client.get('/api/v1/delivery-orders', params={'sales_order': sale})
        assert delivery in [o['delivery_order_id'] for o in found.json()['items']], sale


async def test_another_customers_line_cannot_be_consolidated_in(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The invariant that survived: no consolidated order in the database spans customers."""
    from app.enums import EntityStatus
    from app.models.customer import Customer
    from app.models.sales import SalesOrder, SalesOrderDetail

    sale = await seed_sales_order(db, completed=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sale})
    assert raised.status_code == 201, raised.text
    delivery = raised.json()['delivery_order_id']

    # A second customer, with a completed sale of their own.
    db.add(
        Customer(
            customer_id=2,
            code='C2',
            name='Cliente Dos',
            credit_limit=Decimal('1000'),
            credit_days=30,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )
    other_sale = await seed_sales_order(db, completed=True)
    (await db.get(SalesOrder, other_sale)).customer = 2
    await db.commit()

    foreign_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == other_sale
            )
        )
    ).scalar_one()

    response = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': foreign_line, 'quantity': '1'},
    )

    assert response.status_code == 422, response.text
    assert 'not a deliverable line of this customer' in response.json()['detail']


async def test_a_destination_is_created_from_its_header_and_filled_afterwards(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#165 with #163 — the point-of-sale delivery step, end to end.

    The destination is created from where it goes, who receives it and any instructions, with no
    quantity decided yet; each sale line's quantity is assigned into it afterwards. Neither half was
    possible before: `min_length=1` refused the empty create, and nothing could add a line to an
    existing order.
    """
    sales_order = await seed_sales_order(db, completed=True)
    from app.models.sales import SalesOrderDetail

    sales_line = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sales_order
            )
        )
    ).scalar_one()

    created = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [],
            'ship_to': 1,
            'contact': 1,
            'comment': 'Deja con el portero',
        },
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body['lines'] == []
    assert body['status'] == DeliveryOrderStatus.DRAFT.value
    assert (body['ship_to'], body['comment']) == (1, 'Deja con el portero')
    # Derived from the lines, of which there are none yet — `[]`, not a failure and not `null`.
    assert body['sales_orders'] == []
    delivery = body['delivery_order_id']

    # An empty destination carries nothing, so there is nothing to confirm yet.
    premature = await client.post(f'/api/v1/delivery-orders/{delivery}/confirm')
    assert premature.status_code == 409, premature.text

    filled = await client.post(
        f'/api/v1/delivery-orders/{delivery}/lines',
        json={'sales_order_detail': sales_line, 'quantity': '10'},
    )

    assert filled.status_code == 201, filled.text
    assert [line['quantity'] for line in filled.json()['lines']] == ['10.0000']
    # The header the side sheet supplied survives the fill, and the sale appears with the line.
    assert filled.json()['comment'] == 'Deja con el portero'
    assert filled.json()['sales_orders'] == [sales_order]

    confirmed = await client.post(f'/api/v1/delivery-orders/{delivery}/confirm')
    assert confirmed.status_code == 200, confirmed.text


async def test_an_empty_create_still_claims_nothing_from_the_sale(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The distinction, where confusing the two would be silent: omitting `lines` takes all ten.

    If `lines: []` were ever read as omitted, this destination would come out carrying the whole
    sale and the next create would report the sale fully delivered — with nothing to show that the
    caller asked for the opposite.
    """
    sales_order = await seed_sales_order(db, completed=True)

    empty = await client.post(
        '/api/v1/delivery-orders', json={'sales_order': sales_order, 'lines': []}
    )
    assert empty.status_code == 201, empty.text
    assert empty.json()['lines'] == []

    everything = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})

    assert everything.status_code == 201, everything.text
    assert [line['quantity'] for line in everything.json()['lines']] == ['10.0000']


async def test_the_destination_header_is_applied_at_creation(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#146 — one call, and the address it names is the one stored."""
    sales_order = await seed_sales_order(db, completed=True)

    response = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'ship_to': 1,
            'contact': 1,
            'comment': 'Deja con el portero',
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body['ship_to'], body['contact'], body['comment']) == (1, 1, 'Deja con el portero')


async def test_an_incomplete_sale_cannot_be_delivered(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The guard, reached for real: a draft sale owes nothing yet (FR-009)."""
    sales_order = await seed_sales_order(db, completed=False)

    response = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})

    assert response.status_code == 409, response.text


async def test_listing_delivery_orders_filters_on_the_sale(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """#147's filter, against a database — it matches through the lines, which mocks cannot show."""
    sales_order = await seed_sales_order(db, completed=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})
    assert raised.status_code == 201, raised.text

    matching = await client.get('/api/v1/delivery-orders', params={'sales_order': sales_order})
    assert matching.status_code == 200, matching.text
    assert matching.json()['total'] == 1

    other = await client.get('/api/v1/delivery-orders', params={'sales_order': sales_order + 999})
    assert other.json()['total'] == 0


@pytest.mark.parametrize('quantity', ['0', '-1'])
async def test_a_non_positive_requested_quantity_is_refused_by_the_schema(
    quantity: str, client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sales_order = await seed_sales_order(db, completed=True)

    response = await client.post(
        '/api/v1/delivery-orders',
        json={
            'sales_order': sales_order,
            'lines': [{'sales_order_detail': 1, 'quantity': quantity}],
        },
    )

    assert response.status_code == 422, response.text


async def test_confirming_a_delivery_order_numbers_it(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """A lifecycle transition end to end, folio assignment included."""
    sales_order = await seed_sales_order(db, completed=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})
    delivery_id = raised.json()['delivery_order_id']

    confirmed = await client.post(f'/api/v1/delivery-orders/{delivery_id}/confirm')

    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()['serial'] is not None
    stored = await db.get(DeliveryOrder, delivery_id)
    assert stored is not None
    assert DeliveryOrderStatus(stored.status) is not DeliveryOrderStatus.DRAFT


async def test_cancelling_releases_what_the_order_held(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sales_order = await seed_sales_order(db, completed=True)
    raised = await client.post('/api/v1/delivery-orders', json={'sales_order': sales_order})
    delivery_id = raised.json()['delivery_order_id']

    cancelled = await client.post(
        f'/api/v1/delivery-orders/{delivery_id}/cancel', json={'reason': 'Cliente canceló'}
    )

    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == DeliveryOrderStatus.CANCELLED
    lines = (
        await db.execute(
            select(DeliveryOrderDetail).where(DeliveryOrderDetail.delivery_order == delivery_id)
        )
    ).scalars().all()
    assert all(line.committed_quantity == Decimal(0) for line in lines)


async def test_two_orders_on_one_register_record_different_workflows(
    client: AsyncClient, seeded: None
) -> None:
    """#209 — the pair of rows that were indistinguishable.

    Both carry the same `point_sale`, because it is derived from the caller and the caller is one
    user with one register. That is the whole point: before this field, a back-office order and a
    walk-in sale raised by the same user were identical in every readable field, so the back-office
    list showed register sales and the register's list showed back-office orders.
    """
    back_office = await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})
    register = await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 0})
    assert back_office.status_code == 201, back_office.text
    assert register.status_code == 201, register.text
    assert back_office.json()['point_sale'] == register.json()['point_sale']

    # Reopened, as a restarted client would.
    reread = [
        (await client.get(f'/api/v1/sales-orders/{r.json()["sales_order_id"]}')).json()
        for r in (back_office, register)
    ]

    assert [o['origin'] for o in reread] == [1, 0]


async def test_an_order_that_never_declared_a_workflow_reports_null(
    client: AsyncClient, seeded: None
) -> None:
    """`null`, and never a guess. The request carries a register (the caller's) and a customer,
    and neither says which workflow raised the order — which is exactly why migration 020 ships the
    column empty rather than backfilling the 335,816 rows that predate it."""
    created = await client.post('/api/v1/sales-orders', json={'customer': 1})

    assert created.status_code == 201, created.text
    assert created.json()['origin'] is None


async def test_a_workflow_outside_the_vocabulary_is_refused(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 7})

    assert created.status_code == 422, created.text


async def test_converting_a_quote_records_the_back_office_without_being_asked(
    client: AsyncClient, seeded: None
) -> None:
    """#209 — the path no client can declare anything on.

    `POST /sales-quotes/{id}/convert` builds the order server-side and takes no request body, so if
    it did not record the workflow itself, every converted order would read as "not recorded"
    forever — and converted orders are exactly what a back-office list most needs to show, a quote
    the customer accepted. 6,261 of the 335,816 orders in the deployment came from a quote.

    The two facts stay separate: `origin` says which workflow the order belongs to, `sales_quote`
    says what preceded it.
    """
    quote = await client.post('/api/v1/sales-quotes', json={'customer': 1})
    assert quote.status_code == 201, quote.text
    quote_id = quote.json()['sales_quote_id']
    await client.post(
        f'/api/v1/sales-quotes/{quote_id}/lines', json={'product': 1, 'quantity': '1'}
    )
    confirmed = await client.post(f'/api/v1/sales-quotes/{quote_id}/confirm')
    assert confirmed.status_code == 200, confirmed.text

    converted = await client.post(f'/api/v1/sales-quotes/{quote_id}/convert')

    assert converted.status_code == 201, converted.text
    assert converted.json()['origin'] == 1
    assert converted.json()['sales_quote'] == quote_id


async def test_a_list_row_carries_the_workflow_and_the_quote_it_came_from(
    client: AsyncClient, seeded: None
) -> None:
    """#209 — the list row is where this fact does most of its work.

    Before it, a list screen could not separate the two inboxes from a page it already had:
    `SalesOrderSummary` returned neither `point_sale` nor `fulfillment_intent`, so the only way to
    learn anything about an order's origin was one `GET /sales-orders/{id}` per row.
    """
    created = await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']

    listed = await client.get('/api/v1/sales-orders')

    assert listed.status_code == 200, listed.text
    row = next(r for r in listed.json()['items'] if r['sales_order_id'] == order_id)
    assert row['origin'] == 1
    assert row['sales_quote'] is None


async def test_selecting_a_workflow_returns_only_orders_that_recorded_it(
    client: AsyncClient, seeded: None
) -> None:
    """FR-011 — an order that recorded nothing is not quietly folded into either workflow. That is
    what makes the back-office list truthful from the day the field starts recording, at the cost
    of not showing anything that predates it."""
    back_office = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})
    ).json()['sales_order_id']
    register = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 0})
    ).json()['sales_order_id']
    unrecorded = (await client.post('/api/v1/sales-orders', json={'customer': 1})).json()[
        'sales_order_id'
    ]

    listed = await client.get('/api/v1/sales-orders?origin=1')

    assert listed.status_code == 200, listed.text
    ids = {r['sales_order_id'] for r in listed.json()['items']}
    assert back_office in ids
    assert register not in ids
    assert unrecorded not in ids


async def test_excluding_a_workflow_keeps_the_orders_that_recorded_nothing(
    client: AsyncClient, seeded: None
) -> None:
    """FR-012, and the one assertion in this feature that fails on a plausible implementation.

    Written as a bare `origin != 1`, SQL's three-valued logic evaluates `NULL != 1` to `NULL`, the
    `WHERE` keeps only rows evaluating to true, and every order that recorded nothing disappears —
    all 335,816 of them in the deployment. The register's own list would lose its entire history
    the day it started filtering, which is worse than the problem this feature set out to fix.
    """
    back_office = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})
    ).json()['sales_order_id']
    register = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 0})
    ).json()['sales_order_id']
    unrecorded = (await client.post('/api/v1/sales-orders', json={'customer': 1})).json()[
        'sales_order_id'
    ]

    listed = await client.get('/api/v1/sales-orders?exclude_origin=1')

    assert listed.status_code == 200, listed.text
    ids = {r['sales_order_id'] for r in listed.json()['items']}
    assert unrecorded in ids
    assert register in ids
    assert back_office not in ids


async def test_asking_for_one_workflow_and_against_it_returns_nothing(
    client: AsyncClient, seeded: None
) -> None:
    """No special case in the code: the two clauses are independent and conjoined, so the honest
    answer to a contradictory question is an empty page rather than an error."""
    await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})

    listed = await client.get('/api/v1/sales-orders?origin=1&exclude_origin=1')

    assert listed.status_code == 200, listed.text
    assert listed.json()['items'] == []


async def test_an_order_that_recorded_nothing_still_reads_lists_and_updates(
    client: AsyncClient, seeded: None
) -> None:
    """FR-014 — the guarantee for the 335,816 orders that predate the column.

    Nothing is rewritten and nothing is re-derived, so an order carrying no origin has to behave
    exactly as it did before the field existed — including staying on an unfiltered list, which is
    what mbe-ui shows until the field has been recording long enough to filter on.
    """
    created = await client.post('/api/v1/sales-orders', json={'customer': 1})
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']

    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    assert read.status_code == 200, read.text
    assert read.json()['origin'] is None

    updated = await client.put(f'/api/v1/sales-orders/{order_id}', json={'comment': 'unchanged'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['origin'] is None
    assert updated.json()['comment'] == 'unchanged'

    listed = await client.get('/api/v1/sales-orders')
    row = next(r for r in listed.json()['items'] if r['sales_order_id'] == order_id)
    assert row['origin'] is None


async def test_the_workflow_cannot_be_changed_after_the_order_is_raised(
    client: AsyncClient, seeded: None
) -> None:
    """FR-005 — origin is a fact about how the order was raised, not an editable attribute.

    The guarantee is invisible in the schema: `SalesOrderUpdate` simply has no such field, so
    Pydantic's default `extra='ignore'` drops it before `update_order` reads `exclude_unset`. There
    is no explicit refusal to find in the service, which is why it is asserted here — otherwise the
    next reader adds one, or removes the thing that makes it true without noticing.
    """
    order_id = (
        await client.post('/api/v1/sales-orders', json={'customer': 1, 'origin': 1})
    ).json()['sales_order_id']

    changed = await client.put(f'/api/v1/sales-orders/{order_id}', json={'origin': 0})

    assert changed.status_code == 200, changed.text
    assert changed.json()['origin'] == 1

    # And an order that recorded nothing cannot be given an origin after the fact either.
    unrecorded = (await client.post('/api/v1/sales-orders', json={'customer': 1})).json()[
        'sales_order_id'
    ]
    given = await client.put(f'/api/v1/sales-orders/{unrecorded}', json={'origin': 1})

    assert given.status_code == 200, given.text
    assert given.json()['origin'] is None
