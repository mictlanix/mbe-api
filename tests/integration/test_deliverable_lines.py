"""`GET /delivery-orders/deliverable-lines` and the eligibility `add_line` now shares (#313).

Every sale `seed_sales_order` makes is customer 1's, at facility 1, with one line of ten.
"""

from unittest.mock import patch

from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sales import SalesOrder, SalesOrderDetail
from app.services import delivery_order_service
from tests.integration.seed import seed_facility, seed_sales_order

URL = '/api/v1/delivery-orders/deliverable-lines'


async def _line_of(db: AsyncSession, sale: int) -> int:
    return (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == sale
            )
        )
    ).scalar_one()


async def _raise(client: AsyncClient, sale: int, line: int, quantity: str) -> int:
    raised = await client.post(
        '/api/v1/delivery-orders',
        json={'sales_order': sale, 'lines': [{'sales_order_detail': line, 'quantity': quantity}]},
    )
    assert raised.status_code == 201, raised.text
    return raised.json()['delivery_order_id']


async def test_remaining_is_what_delivery_orders_do_not_cover(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sale = await seed_sales_order(db, completed=True)
    line = await _line_of(db, sale)
    await _raise(client, sale, line, '4')

    response = await client.get(URL, params={'customer': 1})

    assert response.status_code == 200, response.text
    assert response.json() == {
        'items': [
            {
                'sales_order': sale,
                'sales_order_detail': line,
                'facility': 1,
                'product': 1,
                'product_code': 'P1',
                'product_name': 'Producto Uno',
                'warehouse': 1,
                'quantity': '10.0000',
                'covered': '4.0000',
                'remaining': '6.0000',
            }
        ],
        'total': 1,
    }


async def test_a_fully_covered_line_is_absent(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sale = await seed_sales_order(db, completed=True)
    await _raise(client, sale, await _line_of(db, sale), '10')

    response = await client.get(URL, params={'customer': 1})

    assert response.json() == {'items': [], 'total': 0}


async def test_a_cancelled_delivery_order_covers_nothing(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sale = await seed_sales_order(db, completed=True)
    line = await _line_of(db, sale)
    kept = await _raise(client, sale, line, '3')
    dropped = await _raise(client, sale, line, '5')
    cancelled = await client.post(
        f'/api/v1/delivery-orders/{dropped}/cancel', json={'reason': 'Cliente canceló'}
    )
    assert cancelled.status_code == 200, cancelled.text
    assert kept != dropped

    [item] = (await client.get(URL, params={'customer': 1})).json()['items']

    assert (item['covered'], item['remaining']) == ('3.0000', '7.0000')


async def test_ineligible_sales_are_absent(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await seed_sales_order(db, completed=False)
    cancelled = await seed_sales_order(db, completed=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == cancelled).values(cancelled=True)
    )
    unpaid = await seed_sales_order(db, completed=True)
    paid = await seed_sales_order(db, completed=True, paid=True)
    await db.commit()

    everything = (await client.get(URL, params={'customer': 1})).json()
    assert sorted(i['sales_order'] for i in everything['items']) == [unpaid, paid]

    # The setting `create_from_sales_order` honours: immediate terms and unpaid is then refused.
    with patch.object(
        delivery_order_service.settings, 'delivery_order_requires_paid_or_credit_sales_order', True
    ):
        strict = (await client.get(URL, params={'customer': 1})).json()
    assert [i['sales_order'] for i in strict['items']] == [paid]

    # Another customer's sales are not this one's.
    assert (await client.get(URL, params={'customer': 2})).json() == {'items': [], 'total': 0}


async def test_every_facility_is_listed_and_the_sale_narrows_it(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    other = await seed_facility(db)
    here = await seed_sales_order(db, completed=True)
    there = await seed_sales_order(db, completed=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == there).values(facility=other)
    )
    await db.commit()

    listed = (await client.get(URL, params={'customer': 1})).json()
    assert listed['total'] == 2
    assert [(i['sales_order'], i['facility']) for i in listed['items']] == [
        (there, other),
        (here, 1),
    ]

    narrowed = (await client.get(URL, params={'customer': 1, 'sales_order': here})).json()
    assert [i['sales_order'] for i in narrowed['items']] == [here]

    paged = (await client.get(URL, params={'customer': 1, 'skip': 1, 'limit': 1})).json()
    assert (paged['total'], [i['sales_order'] for i in paged['items']]) == (2, [here])


async def test_customer_is_required(client: AsyncClient, seeded: None) -> None:
    response = await client.get(URL)

    assert response.status_code == 422, response.text


async def test_add_line_refuses_a_line_of_an_uncompleted_or_cancelled_sale(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    sale = await seed_sales_order(db, completed=True)
    delivery = await _raise(client, sale, await _line_of(db, sale), '1')

    draft = await seed_sales_order(db, completed=False)
    cancelled = await seed_sales_order(db, completed=True)
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == cancelled).values(cancelled=True)
    )
    await db.commit()

    for refused in (draft, cancelled):
        response = await client.post(
            f'/api/v1/delivery-orders/{delivery}/lines',
            json={'sales_order_detail': await _line_of(db, refused), 'quantity': '1'},
        )
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'delivery_order.sales_order_not_deliverable'
