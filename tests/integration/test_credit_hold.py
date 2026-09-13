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
from app.models.sales import SalesOrder, SalesOrderDetail

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


async def _credit_customer(db: AsyncSession, customer_id: int, *, limit: str) -> None:
    db.add(
        Customer(
            customer_id=customer_id,
            code=f'C{customer_id}',
            name=f'Cliente {customer_id}',
            credit_limit=Decimal(limit),
            credit_days=30,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.commit()


async def _outstanding_credit_order(db: AsyncSession, customer_id: int, *, amount: str) -> None:
    """A confirmed, unpaid credit order carrying `amount` of balance — one unit priced at it.

    Deliberately built the way the API reports it rather than written into a column: the debt is
    summed from the same `balance` every endpoint computes, so a fixture that set a total directly
    would be testing a number nothing else in the system agrees with.
    """
    now = datetime(2026, 9, 1, 10, 0)
    order = SalesOrder(
        creator=1, updater=1, creation_time=now, modification_time=now,
        facility=1, point_sale=1, salesperson=1, customer=customer_id,
        date=now, promise_date=now, due_date=datetime(2099, 1, 1),
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.NET_D, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=False, delivered=False, serial=800 + customer_id,
    )
    db.add(order)
    await db.flush()
    db.add(
        SalesOrderDetail(
            sales_order=order.sales_order_id, product=1, quantity=Decimal('1'),
            cost=Decimal('1'), price=Decimal(amount), discount_rate=Decimal('0'),
            tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
            warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    await db.commit()


async def _credit_draft_in_the_database(db: AsyncSession, customer_id: int) -> int:
    """An unconfirmed order on NET_D terms, written directly — the shape legacy rows have."""
    now = datetime(2026, 9, 10, 10, 0)
    order = SalesOrder(
        creator=1, updater=1, creation_time=now, modification_time=now,
        facility=1, point_sale=1, salesperson=1, customer=customer_id,
        date=now, promise_date=now, due_date=datetime(2099, 1, 1),
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.NET_D, priority=Priority.NORMAL,
        completed=False, cancelled=False, paid=False, delivered=False, serial=None,
    )
    db.add(order)
    await db.flush()
    db.add(
        SalesOrderDetail(
            sales_order=order.sales_order_id, product=1, quantity=Decimal('1'),
            cost=Decimal('1'), price=Decimal('10'), discount_rate=Decimal('0'),
            tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
            warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    await db.commit()
    return order.sales_order_id


class TestTheCreditLimit:
    """#220 — FR-016's fourth refusal, which was never implemented.

    `credit_limit` was read only as a yes/no flag, so a customer with a 5,000 limit and 400,000
    outstanding took credit terms without complaint. These pin the two decisions that give the
    check its shape: the debt is the balance the API already reports, and the order in hand counts
    toward the limit — without which the check can only fire *after* a breach.
    """

    async def test_credit_terms_are_refused_once_the_debt_exceeds_the_limit(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _credit_customer(db, 4, limit='1000')
        await _outstanding_credit_order(db, 4, amount='1500')

        created = await client.post(
            '/api/v1/sales-orders', json={'customer': 4, 'payment_terms': 1}
        )

        assert created.status_code == 422, created.text
        assert 'over their credit limit' in created.json()['detail']

    async def test_a_customer_inside_their_limit_is_untouched(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _credit_customer(db, 5, limit='10000')
        await _outstanding_credit_order(db, 5, amount='1500')

        created = await client.post(
            '/api/v1/sales-orders', json={'customer': 5, 'payment_terms': 1}
        )

        assert created.status_code == 201, created.text

    async def test_confirmation_weighs_the_order_in_hand_against_the_limit(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """The decision that makes this bite before a breach rather than after one. The customer
        is inside their limit until this order, which is exactly the order a limit is for."""
        await _credit_customer(db, 6, limit='1000')
        await _outstanding_credit_order(db, 6, amount='900')
        created = await client.post(
            '/api/v1/sales-orders', json={'customer': 6, 'payment_terms': 1}
        )
        order_id = created.json()['sales_order_id']
        assert created.status_code == 201, created.text
        lined = await client.post(
            f'/api/v1/sales-orders/{order_id}/lines', json={'product': 1, 'quantity': '1'}
        )
        assert lined.status_code == 200, lined.text

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert confirmed.status_code == 422, confirmed.text
        assert 'over their credit limit' in confirmed.json()['detail']

    async def test_a_cash_sale_is_weighed_against_no_limit(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Only a credit order adds to what the customer owes. The hold from #219 still applies to
        a cash sale; the limit does not, and the two must not be conflated."""
        await _credit_customer(db, 7, limit='1000')
        await _outstanding_credit_order(db, 7, amount='2000')
        order_id = await _draft_with_a_line(client, 7)

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert 'over their credit limit' not in str(confirmed.json().get('detail')), confirmed.text

    async def test_the_walk_in_customer_is_exempt_from_the_limit_too(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """Its limit is 0.00, so any credit order against it is over by construction.

        The draft is built in the database rather than through the API, because the API refuses to
        put this customer on credit terms at all — which is the only reason the monolith survives
        without an exemption. mbe_dev nonetheless carries **139 NET_D orders** against it, so the
        state this guards is one the deployment is already in. A draft on immediate terms would
        prove nothing here: the limit is only weighed for credit orders, so such a test passes
        whether or not the exemption exists.
        """
        await _outstanding_credit_order(db, 1, amount='2000')
        order_id = await _credit_draft_in_the_database(db, 1)

        confirmed = await client.post(f'/api/v1/sales-orders/{order_id}/confirm')

        assert 'over their credit limit' not in str(confirmed.json().get('detail')), confirmed.text
