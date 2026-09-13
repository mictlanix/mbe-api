"""What an order still owes once goods have come back (#223).

Balance was total less every non-cancelled application, full stop. For anything this API creates
that is right by construction — a refund requires a paid order (FR-060), so the balance is zero
either way. It is wrong for the rows the monolith left behind, which allowed refunding an unpaid
order and recorded the reduction nowhere but in the refund's own lines: no application is ever
written, and `SalesOrder.Balance` there subtracts refunds for exactly that reason
(`Model/SalesOrder.cs:246`).

In mbe_dev that is 275 of 860 unpaid credit orders, 10.3M of balance the API claimed was owed for
goods sitting back on the shelf — and since #220 reads those balances, 55 customers were over
their credit limit on them.

SC-004 is the test these have to satisfy: *the sum of a customer's outstanding order balances
always equals what they owe*. Its formula clause is the means, and on these rows the means had
stopped serving the end.
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, PaymentMethod, PaymentTerms, Priority
from app.models.sales import CustomerRefund, CustomerRefundDetail, SalesOrder, SalesOrderDetail

NOW = datetime(2026, 9, 1, 10, 0)


async def _order_with_one_line(db: AsyncSession, *, quantity: str, price: str) -> tuple[int, int]:
    order = SalesOrder(
        creator=1, updater=1, creation_time=NOW, modification_time=NOW,
        facility=1, point_sale=1, salesperson=1, customer=1,
        date=NOW, promise_date=NOW, due_date=NOW,
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.IMMEDIATE, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=False, delivered=False, serial=701,
    )
    db.add(order)
    await db.flush()
    line = SalesOrderDetail(
        sales_order=order.sales_order_id, product=1, quantity=Decimal(quantity),
        cost=Decimal('1'), price=Decimal(price), discount_rate=Decimal('0'),
        tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
        warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
        tax_included=False,
    )
    db.add(line)
    await db.commit()
    return order.sales_order_id, line.sales_order_detail_id


async def _refund(
    db: AsyncSession,
    *,
    order_id: int,
    line_id: int,
    quantity: str,
    price: str,
    completed: bool = True,
    cancelled: bool = False,
) -> None:
    refund = CustomerRefund(
        sales_order=order_id, customer=1, creator=1, updater=1, sales_person=1,
        creation_time=NOW, modification_time=NOW, completed=completed, cancelled=cancelled,
        facility=1, serial=1, date=NOW, currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
    )
    db.add(refund)
    await db.flush()
    db.add(
        CustomerRefundDetail(
            customer_refund=refund.customer_refund_id, sales_order_detail=line_id, product=1,
            quantity=Decimal(quantity), price=Decimal(price), product_code='P1',
            product_name='Producto Uno', tax_rate=Decimal('0'), discount=Decimal('0'),
            exchange_rate=Decimal('1'), currency=CurrencyCode.MXN, tax_included=False,
        )
    )
    await db.commit()


async def test_returned_goods_come_off_what_is_still_owed(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Bought 10 at 100, returned 4, paid nothing: 600 owed, not 1,000."""
    order_id, line_id = await _order_with_one_line(db, quantity='10', price='100')
    await _refund(db, order_id=order_id, line_id=line_id, quantity='4', price='100')

    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert read.status_code == 200, read.text
    assert read.json()['total'] == '1000.00', 'the document still says what was sold'
    assert read.json()['balance'] == '600.00'


async def test_the_list_row_agrees_with_the_order(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Two code paths compute this — `attach_derived` and `attach_summary_totals` — and a list
    that disagreed with the document it links to is its own bug."""
    order_id, line_id = await _order_with_one_line(db, quantity='10', price='100')
    await _refund(db, order_id=order_id, line_id=line_id, quantity='4', price='100')

    listed = await client.get('/api/v1/sales-orders')

    row = next(r for r in listed.json()['items'] if r['sales_order_id'] == order_id)
    assert row['balance'] == '600.00'


async def test_a_cancelled_or_draft_refund_changes_nothing(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """Nothing has come back yet. Only a completed, uncancelled refund moves goods."""
    order_id, line_id = await _order_with_one_line(db, quantity='10', price='100')
    await _refund(
        db, order_id=order_id, line_id=line_id, quantity='4', price='100', cancelled=True
    )
    await _refund(
        db, order_id=order_id, line_id=line_id, quantity='3', price='100', completed=False
    )

    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert read.json()['balance'] == '1000.00'


async def test_a_refund_on_a_paid_order_does_not_drive_the_balance_negative(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """The only kind this API creates (FR-060), and the reason the monolith's formula could not be
    copied across: it adds credit notes back, and a **cash** payout here writes no credit note.
    Subtracting the refund from an order that owes nothing would report the business owing the
    customer money it has already handed over. `remaining` floors at zero (FR-042a) and that floor
    is doing real work here, not guarding an impossible case.
    """
    order_id, line_id = await _order_with_one_line(db, quantity='10', price='100')
    payment = await client.post(
        '/api/v1/customer-payments',
        json={'customer': 1, 'amount': '1000.00', 'method': int(PaymentMethod.CASH)},
    )
    assert payment.status_code == 201, payment.text
    applied = await client.post(
        f'/api/v1/customer-payments/{payment.json()["customer_payment_id"]}/applications',
        json={'sales_order': order_id, 'amount': '1000.00'},
    )
    assert applied.status_code == 201, applied.text
    await _refund(db, order_id=order_id, line_id=line_id, quantity='4', price='100')

    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert read.json()['balance'] == '0.00'


async def test_returning_everything_leaves_nothing_owed(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    order_id, line_id = await _order_with_one_line(db, quantity='10', price='100')
    await _refund(db, order_id=order_id, line_id=line_id, quantity='10', price='100')

    read = await client.get(f'/api/v1/sales-orders/{order_id}')

    assert read.json()['balance'] == '0.00'
