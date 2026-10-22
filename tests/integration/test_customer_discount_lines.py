"""A customer's per-product discount lands on the line it applies to (spec 023, #149).

The monolith reads `CustomerDiscount` in `AddItem` and nowhere else (`SalesOrdersController.cs:814`,
`QuotationsController.cs:674`): a line *starts* at the customer's rate, and from then on the rate
is the line's own. Changing the customer, the quantity or the price never looks it up again, and
converting or duplicating a quote copies each line's rate rather than re-reading the override.
"""

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EntityStatus
from app.models.customer import Customer

WITH_DISCOUNT = 2
WITHOUT_DISCOUNT = 3


async def _customers(db: AsyncSession, client: AsyncClient) -> None:
    """Two customers without credit, so payment terms never get in the way; only the first has
    an override, 10% on product 1."""
    for customer_id in (WITH_DISCOUNT, WITHOUT_DISCOUNT):
        db.add(
            Customer(
                customer_id=customer_id,
                code=f'C{customer_id}',
                name=f'Cliente {customer_id}',
                credit_limit=Decimal('0'),
                credit_days=0,
                price_list=1,
                status=EntityStatus.ACTIVE,
            )
        )
    await db.commit()
    created = await client.post(
        f'/api/v1/customers/{WITH_DISCOUNT}/discounts', json={'product': 1, 'discount': '0.1'}
    )
    assert created.status_code == 201, created.text


async def _order(client: AsyncClient, customer_id: int) -> int:
    created = await client.post(
        '/api/v1/sales-orders', json={'customer': customer_id, 'payment_terms': 0}
    )
    assert created.status_code == 201, created.text
    return created.json()['sales_order_id']


async def _quote(client: AsyncClient, customer_id: int) -> int:
    created = await client.post(
        '/api/v1/sales-quotes', json={'customer': customer_id, 'payment_terms': 0}
    )
    assert created.status_code == 201, created.text
    return created.json()['sales_quote_id']


async def _add(client: AsyncClient, path: str, **line: str) -> dict:
    added = await client.post(path, json={'product': 1, 'quantity': '1', **line})
    assert added.status_code == 200, added.text
    return added.json()['lines'][-1]


def _rate(line: dict) -> Decimal:
    return Decimal(str(line['discount_rate']))


class TestTheOverrideStartsTheLine:
    async def test_an_order_line_without_a_discount_takes_the_override(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        order = await _order(client, WITH_DISCOUNT)

        line = await _add(client, f'/api/v1/sales-orders/{order}/lines')

        assert _rate(line) == Decimal('0.1')

    async def test_an_explicit_zero_is_kept(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Zero is a decision, not an absence — the reason `discount_rate` defaults to null."""
        await _customers(db, client)
        order = await _order(client, WITH_DISCOUNT)

        line = await _add(client, f'/api/v1/sales-orders/{order}/lines', discount_rate='0')

        assert _rate(line) == 0

    async def test_an_explicit_rate_is_kept(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        order = await _order(client, WITH_DISCOUNT)

        line = await _add(client, f'/api/v1/sales-orders/{order}/lines', discount_rate='0.05')

        assert _rate(line) == Decimal('0.05')

    async def test_a_customer_without_an_override_gets_no_discount(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        order = await _order(client, WITHOUT_DISCOUNT)

        line = await _add(client, f'/api/v1/sales-orders/{order}/lines')

        assert _rate(line) == 0

    async def test_a_quote_line_without_a_discount_takes_the_override(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        quote = await _quote(client, WITH_DISCOUNT)

        line = await _add(client, f'/api/v1/sales-quotes/{quote}/lines')

        assert _rate(line) == Decimal('0.1')


class TestTheOverrideIsNeverReadAgain:
    async def test_editing_a_line_does_not_reapply_the_override(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        order = await _order(client, WITH_DISCOUNT)
        line = await _add(client, f'/api/v1/sales-orders/{order}/lines')
        path = f'/api/v1/sales-orders/{order}/lines/{line["sales_order_detail_id"]}'

        edited = await client.put(path, json={'discount_rate': '0.2'})
        assert edited.status_code == 200, edited.text
        requantified = await client.put(path, json={'quantity': '3'})
        assert requantified.status_code == 200, requantified.text

        assert _rate(requantified.json()['lines'][0]) == Decimal('0.2')

    async def test_changing_the_customer_leaves_existing_lines_alone(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        order = await _order(client, WITHOUT_DISCOUNT)
        await _add(client, f'/api/v1/sales-orders/{order}/lines')

        moved = await client.put(f'/api/v1/sales-orders/{order}', json={'customer': WITH_DISCOUNT})
        assert moved.status_code == 200, moved.text
        later = await _add(client, f'/api/v1/sales-orders/{order}/lines')

        assert _rate(moved.json()['lines'][0]) == 0
        assert _rate(later) == Decimal('0.1')


class TestAQuoteCarriesItsOwnDiscounts:
    async def _edited_quote(self, client: AsyncClient) -> int:
        quote = await _quote(client, WITH_DISCOUNT)
        line = await _add(client, f'/api/v1/sales-quotes/{quote}/lines')
        edited = await client.put(
            f'/api/v1/sales-quotes/{quote}/lines/{line["sales_quote_detail_id"]}',
            json={'discount_rate': '0.07'},
        )
        assert edited.status_code == 200, edited.text
        return quote

    async def test_converting_copies_the_quote_line_discount(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        quote = await self._edited_quote(client)
        confirmed = await client.post(f'/api/v1/sales-quotes/{quote}/confirm')
        assert confirmed.status_code == 200, confirmed.text

        converted = await client.post(f'/api/v1/sales-quotes/{quote}/convert')

        assert converted.status_code in (200, 201), converted.text
        assert _rate(converted.json()['lines'][0]) == Decimal('0.07')

    async def test_duplicating_copies_the_quote_line_discount(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db, client)
        quote = await self._edited_quote(client)

        duplicated = await client.post(f'/api/v1/sales-quotes/{quote}/duplicate')

        assert duplicated.status_code in (200, 201), duplicated.text
        assert _rate(duplicated.json()['lines'][0]) == Decimal('0.07')
