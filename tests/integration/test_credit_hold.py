"""Where credit is checked, and what each gate lets through (#207, #219).

Four routes reach a committed sale on credit terms, and before #219 only two of them looked:
`create_order` and an explicit terms change on `update_order`. A customer moved onto an order, a
quote converted, and a customer who simply fell behind between capture and confirmation all
arrived at a folio and a stock reservation unchallenged.

The confirmation gate is the one that cannot be walked around, because every route passes through
it — which is why the monolith puts its check there (`SalesOrdersController.cs:1251`) and why its
own unchecked `CreateFromSalesQuote` does no harm. These pin the gate's two deliberate edges: it
does **not** care what terms this order carries, and it does **not** apply to the walk-in customer.
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus, PaymentTerms, Priority
from app.models.customer import Customer
from app.models.sales import SalesOrder

OVERDUE = datetime(2020, 1, 1, 10, 0)


async def _customer(db: AsyncSession, customer_id: int, *, in_arrears: bool) -> None:
    """A customer with a credit line, optionally carrying one overdue credit order."""
    db.add(
        Customer(
            customer_id=customer_id,
            code=f'C{customer_id}',
            name=f'Cliente {customer_id}',
            credit_limit=Decimal('5000'),
            credit_days=30,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.flush()
    if in_arrears:
        await _overdue_credit_order(db, customer_id)
    await db.commit()


async def _overdue_credit_order(db: AsyncSession, customer_id: int) -> None:
    db.add(
        SalesOrder(
            creator=1,
            updater=1,
            creation_time=OVERDUE,
            modification_time=OVERDUE,
            facility=1,
            point_sale=1,
            salesperson=1,
            customer=customer_id,
            date=OVERDUE,
            promise_date=OVERDUE,
            due_date=OVERDUE,
            currency=CurrencyCode.MXN,
            exchange_rate=Decimal('1'),
            payment_terms=PaymentTerms.NET_D,
            priority=Priority.NORMAL,
            completed=True,
            cancelled=False,
            paid=False,
            delivered=False,
            serial=900 + customer_id,
        )
    )


async def _draft_with_a_line(client: AsyncClient, customer_id: int) -> int:
    """A draft on **immediate** terms, so a refusal cannot be about this order's own terms."""
    created = await client.post(
        '/api/v1/sales-orders', json={'customer': customer_id, 'payment_terms': 0}
    )
    assert created.status_code == 201, created.text
    order_id = created.json()['sales_order_id']
    lined = await client.post(
        f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'quantity': '1'}
    )
    assert lined.status_code == 200, lined.text
    return order_id


def _is_credit_hold(response) -> bool:  # noqa: ANN001 — httpx.Response
    return response.status_code == 422 and 'credit hold' in str(response.json().get('detail'))


class TestTheConfirmationGate:
    async def test_a_customer_in_arrears_cannot_confirm_even_a_cash_sale(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """The monolith's rule, and the decision taken on #219: the hold is a fact about the
        customer, not about this document. Confirming a cash sale still hands over goods."""
        await _customer(db, 2, in_arrears=True)
        order_id = await _draft_with_a_line(client, 2)

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert _is_credit_hold(confirmed), confirmed.text
        assert 'confirmed' in confirmed.json()['detail']

    async def test_a_customer_in_good_standing_passes_the_gate(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Whatever else stops this confirmation, it is not the credit hold."""
        await _customer(db, 3, in_arrears=False)
        order_id = await _draft_with_a_line(client, 3)

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert not _is_credit_hold(confirmed), confirmed.text

    async def test_the_walk_in_customer_is_exempt(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """It is a stand-in for "no customer", so a hold on it is a deployment outage rather than
        a credit control: mbe_dev carries 139 NET_D orders against it and 210 open drafts, and one
        of those falling overdue would stop every counter sale confirming. The monolith does not
        exempt it and does not need to — it refuses to put that customer on credit terms at all."""
        await _overdue_credit_order(db, 1)
        await db.commit()
        order_id = await _draft_with_a_line(client, 1)

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert not _is_credit_hold(confirmed), confirmed.text


class TestThePathsThatUsedToLeak:
    async def test_moving_an_order_onto_a_customer_in_arrears_is_refused(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """The customer branch revisited the salesperson and the prices, never the credit — so the
        NET_D terms survived the move onto a customer who could not have been given them."""
        await _customer(db, 2, in_arrears=True)
        await _customer(db, 3, in_arrears=False)
        created = await client.post(
            '/api/v1/sales-orders', json={'customer': 3, 'payment_terms': 1}
        )
        order_id = created.json()['sales_order_id']

        moved = await client.put(f'/api/v1/sales-orders/{order_id}', json={'customer': 2})

        assert _is_credit_hold(moved), moved.text

    async def test_clearing_the_terms_in_the_same_request_is_allowed(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Judged once, on the state the order ends in. Refusing this would refuse the caller for
        terms the same request is taking away."""
        await _customer(db, 2, in_arrears=True)
        await _customer(db, 3, in_arrears=False)
        created = await client.post(
            '/api/v1/sales-orders', json={'customer': 3, 'payment_terms': 1}
        )
        order_id = created.json()['sales_order_id']

        moved = await client.put(
            f'/api/v1/sales-orders/{order_id}', json={'customer': 2, 'payment_terms': 0}
        )

        assert moved.status_code == 200, moved.text
        assert moved.json()['payment_terms'] == 0

    async def test_converting_a_credit_quote_for_a_customer_in_arrears_is_refused(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """`convert_to_order` copied the quote's terms straight across. The monolith has the same
        hole in `CreateFromSalesQuote` and survives it because confirmation catches the result."""
        await _customer(db, 2, in_arrears=True)
        quote = await client.post('/api/v1/sales-quotes', json={'customer': 2, 'payment_terms': 1})
        assert quote.status_code == 201, quote.text
        quote_id = quote.json()['sales_quote_id']
        await client.post(
            f'/api/v1/sales-quotes/{quote_id}/lines', json={'product': 1, 'quantity': '1'}
        )
        confirmed = await client.post(f'/api/v1/sales-quotes/{quote_id}/confirm')
        assert confirmed.status_code == 200, confirmed.text

        converted = await client.post(f'/api/v1/sales-quotes/{quote_id}/convert')

        assert _is_credit_hold(converted), converted.text
