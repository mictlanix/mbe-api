"""Spec 038, T024: spec 033's balance rule at a past moment (research R7), on fixtures (SQLite).

`as_of=None` is spec 033's rule exactly. At a moment T, applications, refunds and credit notes
count only when they had happened by T, and the candidates are the credit orders confirmed by T
that hadn't been settled by then.
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
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
from tests.integration.conftest import db, engine, seeded, sessions  # noqa: F401
from tests.integration.seed import seed_credit_note, seed_invoice

pytestmark = pytest.mark.usefixtures('seeded')

CONFIRMED = datetime(2026, 6, 1, 10, 0)
T = datetime(2026, 6, 30, 23, 59, 59)
BEFORE = datetime(2026, 6, 15, 12, 0)
AFTER = datetime(2026, 7, 10, 12, 0)
YEAR_ONE = datetime(1, 1, 1)


async def _order(
    db: AsyncSession,  # noqa: F811
    *,
    price: str = '1000',
    confirmed: datetime = CONFIRMED,
    paid: bool = False,
    zeroed: datetime | None = None,
) -> tuple[int, int]:
    """A completed credit order of one untaxed line, so its total is `price`."""
    order = SalesOrder(
        creator=1, updater=1, creation_time=confirmed, modification_time=confirmed,
        facility=1, point_sale=1, salesperson=1, customer=1,
        date=confirmed, promise_date=confirmed, due_date=confirmed + timedelta(days=30),
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.NET_D, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=paid, delivered=False, serial=801,
        confirmation_time=confirmed, balance_zeroed_time=zeroed,
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
    db: AsyncSession,  # noqa: F811
    order_id: int,
    amount: str,
    *,
    dated: datetime | None,
    paid_on: datetime = BEFORE,
    cancelled: bool = False,
) -> None:
    """An application dated `dated`, of a payment received on `paid_on`."""
    payment = CustomerPayment(
        facility=1, customer=1, amount=Decimal(amount), method=1, date=paid_on,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=paid_on,
        modification_time=paid_on, payment_type=2, serial=0,
    )  # fmt: skip
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order_id,
            customer_payment=payment.customer_payment_id,
            amount=Decimal(amount),
            amount_change=Decimal('0'),
            applier=1,
            date=dated,
            confirmed=True,
            cancelled=cancelled,
        )  # fmt: skip
    )
    await db.commit()


async def _refund(
    db: AsyncSession,  # noqa: F811
    order_id: int,
    line_id: int,
    price: str,
    *,
    dated: datetime | None,
    modified: datetime = BEFORE,
) -> None:
    refund = CustomerRefund(
        sales_order=order_id, customer=1, creator=1, updater=1, sales_person=1,
        creation_time=modified, modification_time=modified, completed=True, cancelled=False,
        facility=1, serial=1, date=dated, currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
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


async def _balance(db: AsyncSession, order_id: int, as_of: datetime | None) -> Decimal:  # noqa: F811
    order = await db.get(SalesOrder, order_id)
    return (await order_balances(db, [order], as_of=as_of))[order_id].balance


async def _open(db: AsyncSession, as_of: datetime | None) -> dict[int, Decimal]:  # noqa: F811
    return {
        order.sales_order_id: balance.balance
        for order, balance in await open_credit_orders(db, as_of=as_of)
    }


async def test_none_and_now_are_spec_033s_figures(db: AsyncSession) -> None:  # noqa: F811
    order_id, line_id = await _order(db)
    await _apply(db, order_id, '300', dated=BEFORE)
    await _refund(db, order_id, line_id, '100', dated=BEFORE)
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(db, invoice, '200.00', issued=BEFORE)
    settled, _ = await _order(db)
    await _apply(db, settled, '1000', dated=BEFORE)

    spec_033 = await _open(db, None)
    now = await _open(db, local_now())

    assert spec_033 == now
    assert spec_033[order_id] == Decimal('400.00')
    assert settled not in spec_033
    assert await _balance(db, order_id, None) == await _balance(db, order_id, local_now())


async def test_an_application_counts_from_its_own_date(db: AsyncSession) -> None:  # noqa: F811
    order_id, _ = await _order(db)
    await _apply(db, order_id, '100', dated=BEFORE)
    await _apply(db, order_id, '200', dated=AFTER, paid_on=BEFORE)
    await _apply(db, order_id, '50', dated=BEFORE, cancelled=True)

    assert await _balance(db, order_id, T) == Decimal('900.00'), 'the later one is not yet applied'
    assert await _balance(db, order_id, None) == Decimal('700.00')


async def test_a_year_one_application_counts_at_its_payments_date(db: AsyncSession) -> None:  # noqa: F811
    order_id, _ = await _order(db)
    await _apply(db, order_id, '100', dated=YEAR_ONE, paid_on=BEFORE)
    await _apply(db, order_id, '200', dated=YEAR_ONE, paid_on=AFTER)

    assert await _balance(db, order_id, T) == Decimal('900.00')
    assert await _balance(db, order_id, AFTER) == Decimal('700.00')


async def test_a_refund_counts_from_its_date_or_its_last_change(db: AsyncSession) -> None:  # noqa: F811
    order_id, line_id = await _order(db)
    await _refund(db, order_id, line_id, '100', dated=BEFORE)
    await _refund(db, order_id, line_id, '200', dated=AFTER)
    await _refund(db, order_id, line_id, '40', dated=None, modified=BEFORE)
    await _refund(db, order_id, line_id, '80', dated=None, modified=AFTER)

    assert await _balance(db, order_id, T) == Decimal('860.00')
    assert await _balance(db, order_id, None) == Decimal('580.00')


async def test_a_note_cancelled_after_the_moment_still_counts_then(db: AsyncSession) -> None:  # noqa: F811
    order_id, line_id = await _order(db)
    invoice = await seed_invoice(db, [('1000', line_id)])
    await seed_credit_note(
        db, invoice, '250.00', issued=BEFORE, cancelled=True, cancellation_date=AFTER
    )
    await seed_credit_note(db, invoice, '100.00', issued=AFTER, serial=7002)

    assert await _balance(db, order_id, T) == Decimal('750.00')
    assert await _balance(db, order_id, None) == Decimal('900.00'), 'cancelled now; the later one'


async def test_an_order_confirmed_after_the_moment_is_not_a_candidate(db: AsyncSession) -> None:  # noqa: F811
    earlier, _ = await _order(db)
    later, _ = await _order(db, confirmed=AFTER)

    at_t = await _open(db, T)

    assert earlier in at_t
    assert later not in at_t
    assert later in await _open(db, None)


async def test_an_order_zeroed_after_the_moment_is_open_then(db: AsyncSession) -> None:  # noqa: F811
    order_id, _ = await _order(db, paid=True, zeroed=AFTER)
    await _apply(db, order_id, '1000', dated=AFTER)

    assert (await _open(db, T))[order_id] == Decimal('1000.00')
    assert order_id not in await _open(db, None)
    assert order_id not in await _open(db, AFTER + timedelta(days=1))


async def test_an_order_flagged_paid_without_a_zero_time_is_always_closed(
    db: AsyncSession,  # noqa: F811
) -> None:
    order_id, _ = await _order(db, paid=True)

    for moment in (T, AFTER, None):
        assert order_id not in await _open(db, moment)
