"""A customer's product discounts, written and read back through the API against a real database.

The unique key on (customer, product) and the join to `product` are what a mocked session cannot
check (#15).
"""

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import CustomerDiscount


async def _count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(CustomerDiscount))).scalar_one()


async def test_an_override_is_added_and_listed_with_its_product(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'}
    )
    assert created.status_code == 201, created.text

    listed = await client.get('/api/v1/customers/1/discounts')

    assert listed.status_code == 200, listed.text
    row = listed.json()[0]
    assert row['customer'] == 1
    assert row['product'] == {'product_id': 1, 'code': 'P1', 'name': 'Producto Uno'}


async def test_changing_the_rate_leaves_the_product_alone(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'}
    )
    discount_id = created.json()['customer_discount_id']

    changed = await client.put(
        f'/api/v1/customers/1/discounts/{discount_id}', json={'discount': '0.3'}
    )

    assert changed.status_code == 200, changed.text
    assert float(changed.json()['discount']) == 0.3
    assert changed.json()['product']['product_id'] == 1


async def test_a_second_override_for_the_same_product_is_refused_and_leaves_one_row(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await client.post('/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'})

    again = await client.post(
        '/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.2'}
    )

    assert again.status_code == 409, again.text
    assert await _count(db) == 1


async def test_the_same_product_is_allowed_for_a_different_customer(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    other = await client.post(
        '/api/v1/customers', json={'code': 'C-DISC', 'name': 'Otro Cliente', 'price_list': 1}
    )
    assert other.status_code == 201, other.text
    other_id = other.json()['customer_id']
    await client.post('/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'})

    second = await client.post(
        f'/api/v1/customers/{other_id}/discounts', json={'product': 1, 'discount': '0.2'}
    )

    assert second.status_code == 201, second.text
    assert await _count(db) == 2


async def test_an_override_of_another_customer_is_not_reachable(
    client: AsyncClient, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'}
    )
    discount_id = created.json()['customer_discount_id']

    r = await client.delete(f'/api/v1/customers/999/discounts/{discount_id}')

    assert r.status_code == 404


async def test_removing_the_override_leaves_none(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    created = await client.post(
        '/api/v1/customers/1/discounts', json={'product': 1, 'discount': '0.15'}
    )
    discount_id = created.json()['customer_discount_id']

    removed = await client.delete(f'/api/v1/customers/1/discounts/{discount_id}')

    assert removed.status_code == 204, removed.text
    assert await _count(db) == 0
