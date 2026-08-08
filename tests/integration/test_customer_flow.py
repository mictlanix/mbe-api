"""A customer, created and read back through the API against a real database (#150, #154).

`GET /customers/{id}`, `POST /customers` and `PUT /customers/{id}` all returned 500 for a week while
their mocked tests stayed green. What no mocked test can do is what these do: run the service, let
it build the SQL, and hand that SQL to a database that has tables.

The junction is the point of interest. A mocked session accepts any key in an insert payload, so the
old unit test asserted `{'customer': 1, 'taxpayer_recipient': ...}` and passed while the column was
wrong. Here the insert either matches the schema or it fails.
"""

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.customer import Customer, customer_taxpayer
from tests.integration.seed import RFC


async def test_a_customer_is_created_read_and_updated(client: AsyncClient, seeded: None) -> None:
    created = await client.post(
        '/api/v1/customers',
        json={'code': 'C-NEW', 'name': 'Cliente Nuevo', 'price_list': 1},
    )
    assert created.status_code == 201, created.text
    customer_id = created.json()['customer_id']

    read = await client.get(f'/api/v1/customers/{customer_id}')
    assert read.status_code == 200, read.text
    assert read.json()['name'] == 'Cliente Nuevo'
    # The FK expansion runs for real here, rather than being handed a fake.
    assert read.json()['price_list']['price_list_id'] == 1

    updated = await client.put(f'/api/v1/customers/{customer_id}', json={'zone': 'Norte'})
    assert updated.status_code == 200, updated.text
    assert updated.json()['zone'] == 'Norte'


async def test_a_customer_is_created_with_its_tax_registration(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The #150 path end to end: the RFC goes in on create and comes back expanded on read."""
    created = await client.post(
        '/api/v1/customers',
        json={'code': 'C-RFC', 'name': 'Con RFC', 'price_list': 1, 'taxpayers': [RFC]},
    )
    assert created.status_code == 201, created.text
    customer_id = created.json()['customer_id']
    assert [t['taxpayer_recipient_id'] for t in created.json()['taxpayers']] == [RFC]

    read = await client.get(f'/api/v1/customers/{customer_id}')
    assert read.status_code == 200, read.text
    assert [t['taxpayer_recipient_id'] for t in read.json()['taxpayers']] == [RFC]

    # The junction row itself, read with the column name the schema has.
    linked = (
        await db.execute(
            select(customer_taxpayer.c['taxpayer']).where(
                customer_taxpayer.c['customer'] == customer_id
            )
        )
    ).scalars().all()
    assert list(linked) == [RFC]


async def test_the_links_are_replaced_only_when_sent(
    client: AsyncClient, seeded: None
) -> None:
    """Omitted leaves them, `[]` clears them — the semantics, against a database storing them."""
    created = await client.post(
        '/api/v1/customers',
        json={'code': 'C-LINKS', 'name': 'Enlaces', 'price_list': 1, 'taxpayers': [RFC]},
    )
    customer_id = created.json()['customer_id']

    untouched = await client.put(f'/api/v1/customers/{customer_id}', json={'zone': 'Sur'})
    assert [t['taxpayer_recipient_id'] for t in untouched.json()['taxpayers']] == [RFC]

    cleared = await client.put(f'/api/v1/customers/{customer_id}', json={'taxpayers': []})
    assert cleared.json()['taxpayers'] == []


async def test_an_unknown_rfc_is_refused_without_writing_the_customer(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """A foreign key the database enforces, surfacing as 409 rather than 500 (#107).

    Also the assertion the incident asked for: a refused request must not leave a customer behind.
    """
    response = await client.post(
        '/api/v1/customers',
        json={'code': 'C-BAD', 'name': 'Mal RFC', 'price_list': 1, 'taxpayers': ['ZZZ999999ZZ9']},
    )

    assert response.status_code == 409, response.text
    found = (
        await db.execute(select(Customer).where(Customer.code == 'C-BAD'))
    ).scalars().all()
    assert not found, 'the customer was written even though the request was refused'
