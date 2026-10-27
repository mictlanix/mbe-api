"""Payment rows carry the customer's name, and the facility list searches on it (#270)."""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import PaymentMethod
from app.models.customer import Customer


async def _payment(client: AsyncClient) -> dict:
    created = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '10.00', 'method': PaymentMethod.CASH},
    )
    assert created.status_code == 201, created.text
    return created.json()


async def test_every_payment_response_names_its_customer(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    name = (await db.get(Customer, 1)).name
    created = await _payment(client)
    payment_id = created['customer_payment_id']

    assert created['customer_display_name'] == name
    read = await client.get(f'/api/v1/customer-payments/{payment_id}')
    assert read.json()['customer_display_name'] == name
    for path in ('', '/search', '/unverified'):
        listed = await client.get(f'/api/v1/customer-payments{path}')
        assert listed.status_code == 200, listed.text
        row = next(p for p in listed.json()['items'] if p['customer_payment_id'] == payment_id)
        assert row['customer_display_name'] == name


async def test_the_list_searches_on_the_customers_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    name = (await db.get(Customer, 1)).name
    payment_id = (await _payment(client))['customer_payment_id']

    found = await client.get('/api/v1/customer-payments', params={'search': name[1:-1].lower()})
    missed = await client.get('/api/v1/customer-payments', params={'search': 'zzz-no-such-name'})

    assert payment_id in [p['customer_payment_id'] for p in found.json()['items']]
    assert missed.json() == {'items': [], 'total': 0}
