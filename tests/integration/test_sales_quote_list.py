"""What a quote list row can show, and what a search on it actually narrows (#213).

Quotes were the third list of this shape and the only one that could not render a customer:
`SalesOrderSummary` and `CustomerPaymentSummary` both carry `customer_display_name` already. The
gap is sharper here, because a quote has no `customer_name` override column to fall back on and a
quote list is browsed *by customer* — "what did we quote Acme?" is the question the screen exists
to answer.

The search half was the worse of the two. A non-numeric term added no clause at all, so the page
came back unfiltered: typing a customer's name returned every quote in the facility, which reads as
"these are all their quotes". Compare #172, where a name search matched a null column and returned
nothing — that reads as "no results". A filter that silently widens is the more dangerous shape,
which is why it is pinned against real rows rather than against the SQL.
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.customer import Customer


async def _customer(db: AsyncSession, customer_id: int, name: str) -> None:
    db.add(
        Customer(
            customer_id=customer_id,
            code=f'C{customer_id}',
            name=name,
            credit_limit=Decimal('0'),
            credit_days=0,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.commit()


async def test_a_quote_row_carries_the_customers_own_name(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await _customer(db, 2, 'Acme Refacciones')
    created = await client.post('/api/v1/sales-quotes', json={'customer': 2})
    assert created.status_code == 201, created.text

    listed = await client.get('/api/v1/sales-quotes')

    assert listed.status_code == 200, listed.text
    assert listed.json()['items'][0]['customer_display_name'] == 'Acme Refacciones'


async def test_searching_a_name_narrows_the_list_instead_of_widening_it(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The assertion that matters is not that Acme is found — it is that Bravo is *gone*."""
    await _customer(db, 2, 'Acme Refacciones')
    await _customer(db, 3, 'Bravo Herramientas')
    for customer in (2, 3):
        assert (
            await client.post('/api/v1/sales-quotes', json={'customer': customer})
        ).status_code == 201

    everything = await client.get('/api/v1/sales-quotes')
    matched = await client.get('/api/v1/sales-quotes', params={'search': 'Acme'})

    assert everything.json()['total'] == 2
    assert matched.json()['total'] == 1
    assert matched.json()['items'][0]['customer_display_name'] == 'Acme Refacciones'


async def test_a_name_nobody_carries_finds_nothing(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The other half of the same mistake: an unmatched term used to return the whole page."""
    await _customer(db, 2, 'Acme Refacciones')
    assert (await client.post('/api/v1/sales-quotes', json={'customer': 2})).status_code == 201

    found = await client.get('/api/v1/sales-quotes', params={'search': 'Zeta'})

    assert found.json()['total'] == 0


async def test_searching_a_number_still_matches_the_folio(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Unchanged: a digit term is an id or a serial, and is not looked for in a name."""
    await _customer(db, 2, 'Acme Refacciones')
    created = await client.post('/api/v1/sales-quotes', json={'customer': 2})
    quote_id = created.json()['sales_quote_id']

    found = await client.get('/api/v1/sales-quotes', params={'search': str(quote_id)})
    missed = await client.get('/api/v1/sales-quotes', params={'search': str(quote_id + 50)})

    assert [q['sales_quote_id'] for q in found.json()['items']] == [quote_id]
    assert missed.json()['total'] == 0
