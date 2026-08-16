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
            shipping=False,
            shipping_required_document=False,
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
