"""The orders a payment can be applied to (spec 033, FR-010).

Only orders that still owe more than 0.01, each with the shared balance. Every payment term stays:
cash sales are paid through this list too, so it isn't the AR view's credit-only set.
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, PaymentTerms, Priority
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderDetail, SalesOrderPayment
from app.services.sales_order_service import order_balances
from tests.integration.seed import seed_credit_note, seed_facility, seed_invoice

NOW = datetime(2026, 9, 1, 10, 0)
URL = '/api/v1/customer-payments/outstanding-orders'


async def _order(
    db: AsyncSession,
    *,
    price: str = '1000',
    terms: PaymentTerms = PaymentTerms.NET_D,
    facility: int = 1,
) -> tuple[int, int]:
    order = SalesOrder(
        creator=1, updater=1, creation_time=NOW, modification_time=NOW,
        facility=facility, point_sale=1, salesperson=1, customer=1,
        date=NOW, promise_date=NOW, due_date=NOW,
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=terms, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=False, delivered=False, serial=811,
    )  # fmt: skip
    db.add(order)
    await db.flush()
    line = SalesOrderDetail(
        sales_order=order.sales_order_id, product=1, quantity=Decimal('1'),
        cost=Decimal('1'), price=Decimal(price), discount_rate=Decimal('0'),
        tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
        warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
        tax_included=False,
    )  # fmt: skip
    db.add(line)
    await db.commit()
    return order.sales_order_id, line.sales_order_detail_id


async def _applied(db: AsyncSession, order_id: int, amount: str) -> None:
    payment = CustomerPayment(
        facility=1, customer=1, amount=Decimal(amount), method=1, date=NOW,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=NOW,
        modification_time=NOW, payment_type=2, serial=0,
    )  # fmt: skip
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order_id, customer_payment=payment.customer_payment_id,
            amount=Decimal(amount), amount_change=Decimal(0), applier=1, date=NOW,
            confirmed=True, cancelled=False,
        )  # fmt: skip
    )
    await db.commit()


async def _listed(client: AsyncClient, **params: object) -> dict[int, str]:
    response = await client.get(URL, params={'limit': 100, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['total'] == len(body['items'])
    return {row['sales_order_id']: row['balance'] for row in body['items']}


async def test_orders_that_owe_nothing_are_not_listed(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    credit_settled, _ = await _order(db)
    await _applied(db, credit_settled, '999.99')
    cash_settled, _ = await _order(db, terms=PaymentTerms.IMMEDIATE)
    await _applied(db, cash_settled, '1000')
    cash_owing, _ = await _order(db, terms=PaymentTerms.IMMEDIATE)

    listed = await _listed(client)

    assert credit_settled not in listed and cash_settled not in listed
    assert listed[cash_owing] == '1000.00', 'a cash sale is paid through this list too'


async def test_the_balance_is_the_shared_one(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    order_id, line_id = await _order(db)
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '150.00')
    await _applied(db, order_id, '100')

    listed = await _listed(client)

    shared = (await order_balances(db, [await db.get(SalesOrder, order_id)]))[order_id]
    assert listed[order_id] == '750.00'
    assert Decimal(listed[order_id]) == shared.balance


async def test_paging_counts_only_what_is_listed(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    owing = [(await _order(db))[0] for _ in range(3)]
    settled, _ = await _order(db)
    await _applied(db, settled, '1000')

    first = await client.get(URL, params={'limit': 2, 'skip': 0})
    second = await client.get(URL, params={'limit': 2, 'skip': 2})

    assert first.json()['total'] == 3 and second.json()['total'] == 3
    ids = [r['sales_order_id'] for r in first.json()['items'] + second.json()['items']]
    assert ids == sorted(owing, reverse=True), 'newest first, nothing repeated or skipped'


async def test_scope_and_filters_are_unchanged(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    here, _ = await _order(db)
    elsewhere, _ = await _order(db, facility=await seed_facility(db))

    listed = await _listed(client)
    by_customer = await _listed(client, customer=1)
    by_id = await _listed(client, search=str(here))
    other_customer = await _listed(client, customer=999)

    assert here in listed and elsewhere not in listed, "the caller's facility only"
    assert set(by_customer) == set(listed)
    assert set(by_id) == {here}
    assert other_customer == {}
