"""The pilot's sales rules, end to end (spec 023).

**Credit terms.** A credit customer starts every order and quote on credit, and may still choose
cash while they are under their limit. Once their credit debt reaches the limit, cash is refused
as well as credit: buying in cash is not a way around a debt they have stopped paying (FR-012).
Quotes commit nothing, so they are never refused on credit grounds; every check waits for the
conversion (FR-019).

**Delivery details.** An order raised on the back-office sales orders screen (origin 1) is
confirmed only with a fulfilment intent, an address when it is delivered, and a promise date that
has not passed (FR-008). The POS and orders that recorded no origin are confirmed as before.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.enums import CurrencyCode, EntityStatus, PaymentTerms, Priority
from app.models.customer import Customer
from app.models.sales import SalesOrder, SalesOrderDetail

GOOD = 2  # credit line, nothing owed
HELD = 3  # credit line, one overdue credit order
AT_LIMIT = 4  # credit line of 1000, owes 1500 that is not yet due
NO_CREDIT = 5

OVERDUE = datetime(2020, 1, 1, 10, 0)


def _order(customer_id: int, *, terms: PaymentTerms, completed: bool, due: datetime) -> SalesOrder:
    return SalesOrder(
        creator=1,
        updater=1,
        creation_time=due,
        modification_time=due,
        facility=1,
        point_sale=1,
        salesperson=1,
        customer=customer_id,
        date=due,
        promise_date=due,
        due_date=due,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=terms,
        priority=Priority.NORMAL,
        completed=completed,
        cancelled=False,
        paid=False,
        delivered=False,
        serial=900 + customer_id if completed else None,
    )


def _line(order_id: int, price: str) -> SalesOrderDetail:
    return SalesOrderDetail(
        sales_order=order_id,
        product=1,
        quantity=Decimal('1'),
        cost=Decimal('0'),
        price=Decimal(price),
        discount_rate=Decimal('0'),
        tax_rate=Decimal('0'),
        product_code='P1',
        product_name='Producto 1',
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        tax_included=True,
    )


async def _customers(db: AsyncSession) -> None:
    limits = {GOOD: '5000', HELD: '5000', AT_LIMIT: '1000', NO_CREDIT: '0'}
    for customer_id, limit in limits.items():
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
    await db.flush()
    # It owes something: since spec 033 the hold reads the balance, not the flag alone.
    held = _order(HELD, terms=PaymentTerms.NET_D, completed=True, due=OVERDUE)
    db.add(held)
    await db.flush()
    db.add(_line(held.sales_order_id, '100'))
    debt = _order(
        AT_LIMIT, terms=PaymentTerms.NET_D, completed=True, due=local_now() + timedelta(days=365)
    )
    db.add(debt)
    await db.flush()
    db.add(_line(debt.sales_order_id, '1500'))
    await db.commit()


async def _create_order(client: AsyncClient, **body: object) -> dict:
    created = await client.post('/api/v1/sales-orders', json=body)
    assert created.status_code == 201, created.text
    return created.json()


async def _add_line(client: AsyncClient, path: str) -> None:
    added = await client.post(path, json={'product': 1, 'quantity': '1'})
    assert added.status_code == 200, added.text


def _settle_first(response) -> bool:  # noqa: ANN001 — httpx.Response
    return response.status_code == 422 and str(response.json().get('detail')).startswith(
        'Customer must settle their credit'
    )


class TestCreditTerms:
    async def test_a_quote_defaults_to_credit_for_a_credit_customer(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)

        credit = await client.post('/api/v1/sales-quotes', json={'customer': GOOD})
        cash = await client.post('/api/v1/sales-quotes', json={'customer': NO_CREDIT})

        assert credit.status_code == 201, credit.text
        assert credit.json()['payment_terms'] == PaymentTerms.NET_D
        assert cash.json()['payment_terms'] == PaymentTerms.IMMEDIATE

    async def test_a_quote_is_never_refused_on_credit_grounds(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """A customer on hold still gets a quote; the hold bites at conversion (FR-019)."""
        await _customers(db)

        created = await client.post('/api/v1/sales-quotes', json={'customer': HELD})
        assert created.status_code == 201, created.text
        assert created.json()['payment_terms'] == PaymentTerms.NET_D
        quote = created.json()['sales_quote_id']

        to_cash = await client.put(f'/api/v1/sales-quotes/{quote}', json={'payment_terms': 0})
        to_other = await client.put(f'/api/v1/sales-quotes/{quote}', json={'customer': NO_CREDIT})

        assert to_cash.status_code == 200, to_cash.text
        assert to_other.status_code == 200, to_other.text

    async def test_moving_a_cash_order_onto_a_credit_customer_makes_it_credit(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)
        order = await _create_order(client, customer=NO_CREDIT)

        moved = await client.put(
            f'/api/v1/sales-orders/{order["sales_order_id"]}', json={'customer': GOOD}
        )

        assert moved.status_code == 200, moved.text
        assert moved.json()['payment_terms'] == PaymentTerms.NET_D

    async def test_moving_a_cash_order_onto_a_held_credit_customer_is_refused(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """The move makes it credit, and credit for a customer on hold is refused there, as
        creating the order would be (#207)."""
        await _customers(db)
        order = await _create_order(client, customer=NO_CREDIT)

        moved = await client.put(
            f'/api/v1/sales-orders/{order["sales_order_id"]}', json={'customer': HELD}
        )

        assert moved.status_code == 422
        assert 'credit hold' in moved.json()['detail']

    async def test_cash_under_the_limit_is_allowed(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)

        order = await _create_order(client, customer=GOOD, payment_terms=0)

        assert order['payment_terms'] == PaymentTerms.IMMEDIATE

    async def test_cash_at_the_limit_is_refused_on_create(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)

        created = await client.post(
            '/api/v1/sales-orders', json={'customer': AT_LIMIT, 'payment_terms': 0}
        )

        assert _settle_first(created), created.text

    async def test_cash_at_the_limit_is_refused_on_update(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)
        order = await _create_order(client, customer=NO_CREDIT)

        moved = await client.put(
            f'/api/v1/sales-orders/{order["sales_order_id"]}',
            json={'customer': AT_LIMIT, 'payment_terms': 0},
        )

        assert _settle_first(moved), moved.text

    async def test_cash_at_the_limit_is_refused_on_confirm(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        """A draft raised before the rule existed: 37 of these sit in mbe_dev."""
        await _customers(db)
        draft = _order(
            AT_LIMIT, terms=PaymentTerms.IMMEDIATE, completed=False, due=local_now()
        )
        db.add(draft)
        await db.flush()
        db.add(_line(draft.sales_order_id, '100'))
        await db.commit()

        confirmed = await client.post(f'/api/v1/sales-orders/{draft.sales_order_id}/confirm')

        assert _settle_first(confirmed), confirmed.text

    async def test_cash_at_the_limit_is_refused_on_convert(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)
        created = await client.post(
            '/api/v1/sales-quotes', json={'customer': AT_LIMIT, 'payment_terms': 0}
        )
        assert created.status_code == 201, created.text
        quote = created.json()['sales_quote_id']
        await _add_line(client, f'/api/v1/sales-quotes/{quote}/lines')
        confirmed = await client.post(f'/api/v1/sales-quotes/{quote}/confirm')
        assert confirmed.status_code == 200, confirmed.text

        converted = await client.post(f'/api/v1/sales-quotes/{quote}/convert')

        assert _settle_first(converted), converted.text

    async def test_cash_for_customers_without_credit_is_untouched(
        self, client: AsyncClient, db: AsyncSession, seeded: None
    ) -> None:
        await _customers(db)

        for customer_id in (1, NO_CREDIT):
            order = await _create_order(client, customer=customer_id, payment_terms=0)
            await _add_line(client, f'/api/v1/sales-orders/{order["sales_order_id"]}/lines')

            confirmed = await client.post(
                f'/api/v1/sales-orders/{order["sales_order_id"]}/confirm'
            )

            assert not _settle_first(confirmed), confirmed.text


ADDRESS = 1  # seeded by tests/integration/seed.py


async def _back_office(client: AsyncClient, **body: object) -> int:
    """A cash draft for the walk-in customer, so no credit rule can be what refuses it."""
    order = await _create_order(client, customer=1, payment_terms=0, **body)
    await _add_line(client, f'/api/v1/sales-orders/{order["sales_order_id"]}/lines')
    return order['sales_order_id']


def _missing(response) -> list[str]:  # noqa: ANN001 — httpx.Response
    assert response.status_code == 409, response.text
    detail = response.json()['detail']
    assert detail['message'] == 'Order is missing delivery details', detail
    return detail['missing']


class TestBackOfficeDelivery:
    async def test_an_order_without_an_intent_is_refused(
        self, client: AsyncClient, seeded: None
    ) -> None:
        order = await _back_office(client, origin=1)

        confirmed = await client.post(f'/api/v1/sales-orders/{order}/confirm')

        assert _missing(confirmed) == ['fulfillment_intent']

    async def test_a_delivery_needs_its_address_and_then_confirms(
        self, client: AsyncClient, seeded: None
    ) -> None:
        order = await _back_office(client, origin=1, fulfillment_intent=1)

        refused = await client.post(f'/api/v1/sales-orders/{order}/confirm')
        addressed = await client.put(f'/api/v1/sales-orders/{order}', json={'ship_to': ADDRESS})
        confirmed = await client.post(f'/api/v1/sales-orders/{order}/confirm')

        assert _missing(refused) == ['ship_to']
        assert addressed.status_code == 200, addressed.text
        assert confirmed.status_code == 200, confirmed.text

    async def test_a_pickup_confirms_without_an_address(
        self, client: AsyncClient, seeded: None
    ) -> None:
        order = await _back_office(client, origin=1, fulfillment_intent=0)

        confirmed = await client.post(f'/api/v1/sales-orders/{order}/confirm')

        assert confirmed.status_code == 200, confirmed.text

    async def test_a_promise_date_already_past_is_refused(
        self, client: AsyncClient, seeded: None
    ) -> None:
        yesterday = (local_now() - timedelta(days=1)).replace(microsecond=0).isoformat()
        order = await _back_office(
            client, origin=1, fulfillment_intent=1, ship_to=ADDRESS, promise_date=yesterday
        )

        confirmed = await client.post(f'/api/v1/sales-orders/{order}/confirm')

        assert _missing(confirmed) == ['promise_date']

    async def test_a_refusal_writes_nothing(self, client: AsyncClient, seeded: None) -> None:
        order = await _back_office(client, origin=1)

        await client.post(f'/api/v1/sales-orders/{order}/confirm')
        after = await client.get(f'/api/v1/sales-orders/{order}')

        assert after.json()['serial'] is None
        assert after.json()['status'] == 'draft'

    async def test_the_pos_and_unrecorded_origins_are_untouched(
        self, client: AsyncClient, seeded: None
    ) -> None:
        for body in ({'origin': 0}, {}):
            order = await _back_office(client, **body)

            confirmed = await client.post(f'/api/v1/sales-orders/{order}/confirm')

            assert confirmed.status_code == 200, confirmed.text

    async def test_a_converted_quote_needs_an_intent(
        self, client: AsyncClient, seeded: None
    ) -> None:
        """Conversion raises a back-office order and records no intent: the quote has none."""
        created = await client.post('/api/v1/sales-quotes', json={'customer': 1})
        quote = created.json()['sales_quote_id']
        await _add_line(client, f'/api/v1/sales-quotes/{quote}/lines')
        await client.post(f'/api/v1/sales-quotes/{quote}/confirm')
        converted = await client.post(f'/api/v1/sales-quotes/{quote}/convert')
        assert converted.status_code in (200, 201), converted.text

        confirmed = await client.post(
            f'/api/v1/sales-orders/{converted.json()["sales_order_id"]}/confirm'
        )

        assert 'fulfillment_intent' in _missing(confirmed)
