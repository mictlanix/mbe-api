"""One balance rule for sales orders (spec 033, FR-002, FR-003, FR-007).

Total, less completed refunds, less the order's share of live credit notes against its invoices,
less live applications, floored at zero. `open_credit_orders` is the customer side's set of open
documents built on it.
"""

from datetime import datetime
from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, PaymentTerms, Priority
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.services.sales_order_service import open_credit_orders, order_balances
from tests.integration.seed import seed_credit_note, seed_facility, seed_invoice

NOW = datetime(2026, 9, 1, 10, 0)


async def _order(
    db: AsyncSession,
    *,
    price: str = '1000',
    terms: PaymentTerms = PaymentTerms.NET_D,
    paid: bool = False,
    facility: int = 1,
    customer: int = 1,
) -> tuple[int, int]:
    """A completed order of one line, untaxed, so its total is `price`."""
    order = SalesOrder(
        creator=1, updater=1, creation_time=NOW, modification_time=NOW,
        facility=facility, point_sale=1, salesperson=1, customer=customer,
        date=NOW, promise_date=NOW, due_date=NOW,
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=terms, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=paid, delivered=False, serial=801,
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


async def _apply(
    db: AsyncSession, order_id: int, amount: str, *, change: str = '0', cancelled: bool = False
) -> None:
    payment = CustomerPayment(
        facility=1, customer=1, amount=Decimal(amount) + Decimal(change), method=1,
        date=NOW, currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=NOW,
        modification_time=NOW, payment_type=2, serial=0,
    )  # fmt: skip
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order_id,
            customer_payment=payment.customer_payment_id,
            amount=Decimal(amount),
            amount_change=Decimal(change),
            applier=1,
            date=NOW,
            confirmed=True,
            cancelled=cancelled,
        )  # fmt: skip
    )
    await db.commit()


async def _refund(
    db: AsyncSession, order_id: int, line_id: int, price: str, *, completed: bool = True,
    cancelled: bool = False,
) -> None:  # fmt: skip
    refund = CustomerRefund(
        sales_order=order_id, customer=1, creator=1, updater=1, sales_person=1,
        creation_time=NOW, modification_time=NOW, completed=completed, cancelled=cancelled,
        facility=1, serial=1, date=NOW, currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
    )  # fmt: skip
    db.add(refund)
    await db.flush()
    db.add(
        CustomerRefundDetail(
            customer_refund=refund.customer_refund_id,
            sales_order_detail=line_id,
            product=1,
            quantity=Decimal('1'),
            price=Decimal(price),
            product_code='P1',
            product_name='Producto Uno',
            tax_rate=Decimal('0'),
            discount=Decimal('0'),
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )  # fmt: skip
    )
    await db.commit()


async def _balance(db: AsyncSession, order_id: int):  # noqa: ANN202
    order = await db.get(SalesOrder, order_id)
    return (await order_balances(db, [order]))[order_id]


async def test_live_applications_come_off_and_change_does_not(
    db: AsyncSession, seeded: None
) -> None:
    order_id, _ = await _order(db)
    await _apply(db, order_id, '300', change='50')
    await _apply(db, order_id, '200', cancelled=True)

    balance = await _balance(db, order_id)

    assert balance.total == Decimal('1000.00')
    assert balance.applied == Decimal('300')
    assert balance.balance == Decimal('700.00'), 'change is not money the order received'


async def test_only_completed_live_refunds_come_off(db: AsyncSession, seeded: None) -> None:
    order_id, line_id = await _order(db)
    await _refund(db, order_id, line_id, '100')
    await _refund(db, order_id, line_id, '200', completed=False)
    await _refund(db, order_id, line_id, '400', cancelled=True)

    balance = await _balance(db, order_id)

    assert balance.refunded == Decimal('100.00')
    assert balance.balance == Decimal('900.00')


async def test_a_live_credit_note_comes_off(db: AsyncSession, seeded: None) -> None:
    order_id, line_id = await _order(db)
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '250.00')

    balance = await _balance(db, order_id)

    assert balance.credited == Decimal('250.00')
    assert balance.balance == Decimal('750.00')


async def test_a_traced_line_counts_in_full_for_its_order(db: AsyncSession, seeded: None) -> None:
    """The note is 300: 100 traced to the second order, 200 shared by the invoice's parts."""
    first, first_line = await _order(db, price='600')
    second, second_line = await _order(db, price='400')
    invoice = await seed_invoice(db, [('600', first_line), ('400', second_line)])
    await seed_credit_note(db, invoice, '300.00', lines=[('100', second_line), ('200', None)])

    balances = await order_balances(
        db, [await db.get(SalesOrder, first), await db.get(SalesOrder, second)]
    )

    assert balances[first].credited == Decimal('120.00')
    assert balances[second].credited == Decimal('180.00'), '100 traced plus 80 of the 200'


async def test_an_invoice_of_two_orders_shares_the_note_by_their_parts(
    db: AsyncSession, seeded: None
) -> None:
    first, first_line = await _order(db, price='750')
    second, second_line = await _order(db, price='250')
    invoice = await seed_invoice(db, [('750', first_line), ('250', second_line)])
    await seed_credit_note(db, invoice, '100.00')

    one_by_one = [(await _balance(db, first)).credited, (await _balance(db, second)).credited]
    together = await order_balances(
        db, [await db.get(SalesOrder, first), await db.get(SalesOrder, second)]
    )

    assert one_by_one == [Decimal('75.00'), Decimal('25.00')]
    assert [together[first].credited, together[second].credited] == one_by_one


async def test_cancelled_and_draft_notes_do_not_count(db: AsyncSession, seeded: None) -> None:
    order_id, line_id = await _order(db)
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '100.00', cancelled=True, serial=7002)
    await seed_credit_note(db, invoice, '200.00', completed=False, serial=7003)

    assert (await _balance(db, order_id)).credited == Decimal(0)


async def test_deductions_above_the_total_leave_nothing_owed(
    db: AsyncSession, seeded: None
) -> None:
    order_id, line_id = await _order(db)
    await _apply(db, order_id, '900')
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '500.00')

    assert (await _balance(db, order_id)).balance == Decimal('0.00')


async def test_open_credit_orders_keeps_unpaid_credit_orders_that_owe(
    db: AsyncSession, seeded: None
) -> None:
    owing, _ = await _order(db)
    settled, _ = await _order(db)
    await _apply(db, settled, '1000')
    legacy_paid, _ = await _order(db, paid=True)
    cash, _ = await _order(db, terms=PaymentTerms.IMMEDIATE)
    elsewhere, _ = await _order(db, facility=await seed_facility(db))

    every = {order.sales_order_id for order, _ in await open_credit_orders(db)}
    here = {order.sales_order_id for order, _ in await open_credit_orders(db, facility=1)}
    customer = {order.sales_order_id for order, _ in await open_credit_orders(db, customer=1)}

    assert owing in every and elsewhere in every
    assert settled not in every, 'an unpaid order with nothing left is not open'
    assert legacy_paid not in every, 'the flag closes an order whatever it computes'
    assert cash not in every
    assert here == every - {elsewhere}
    assert customer == every


async def test_the_order_and_its_list_row_read_the_same_balance(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    """`attach_derived` and `attach_summary_totals` report what `order_balances` computes."""
    order_id, line_id = await _order(db)
    await _refund(db, order_id, line_id, '100')
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '200.00')

    read = await client.get(f'/api/v1/sales-orders/{order_id}')
    listed = await client.get('/api/v1/sales-orders', params={'limit': 100})

    expected = (await _balance(db, order_id)).balance
    assert expected == Decimal('700.00')
    assert Decimal(read.json()['balance']) == expected
    row = next(r for r in listed.json()['items'] if r['sales_order_id'] == order_id)
    assert Decimal(row['balance']) == expected
