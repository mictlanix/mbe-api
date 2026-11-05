"""Credit orders and their applications, for the spec 038 receivable tests (research R7–R10)."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, PaymentTerms, Priority
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderDetail, SalesOrderPayment


async def credit_order(
    db: AsyncSession,
    *,
    confirmed: datetime,
    due: datetime,
    price: str = '1000',
    customer: int = 1,
    facility: int = 1,
    salesperson: int = 1,
    currency: CurrencyCode = CurrencyCode.MXN,
    rate: str = '1',
) -> int:
    """A completed credit order of one untaxed line, so its total is `price`."""
    order = SalesOrder(
        creator=1, updater=1, creation_time=confirmed, modification_time=confirmed,
        facility=facility, point_sale=1, salesperson=salesperson, customer=customer,
        date=confirmed, promise_date=confirmed, due_date=due,
        currency=currency, exchange_rate=Decimal(rate),
        payment_terms=PaymentTerms.NET_D, priority=Priority.NORMAL,
        completed=True, cancelled=False, paid=False, delivered=False, serial=801,
        confirmation_time=confirmed,
    )  # fmt: skip
    db.add(order)
    await db.flush()
    db.add(
        SalesOrderDetail(
            sales_order=order.sales_order_id, product=1, quantity=Decimal('1'),
            cost=Decimal('1'), price=Decimal(price), discount_rate=Decimal('0'),
            tax_rate=Decimal('0'), product_code='P1', product_name='Producto Uno',
            warehouse=1, exchange_rate=Decimal(rate), currency=currency, tax_included=False,
        )  # fmt: skip
    )
    await db.commit()
    return order.sales_order_id


async def apply(db: AsyncSession, order: int, amount: str, dated: datetime) -> None:
    """A payment received and applied to `order` on `dated`."""
    payment = CustomerPayment(
        facility=1, customer=1, amount=Decimal(amount), method=1, date=dated,
        currency=CurrencyCode.MXN, creator=1, updater=1, creation_time=dated,
        modification_time=dated, payment_type=2, serial=0,
    )  # fmt: skip
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order, customer_payment=payment.customer_payment_id,
            amount=Decimal(amount), amount_change=Decimal('0'), applier=1, date=dated,
            confirmed=True, cancelled=False,
        )  # fmt: skip
    )
    await db.commit()
