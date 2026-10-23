"""Commission fixtures on top of the integration baseline (spec 025).

Agents 1 (Ana Ruiz, from the baseline) and 2; one label, CONCRETOS at 1%, on product 1; every
participation at the current system's shares (lifetime 100%, the two split shares 50%); customer 20,
whose salesperson is agent 1. Prices are tax-included, so a line's amount is its price.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus, PaymentTerms, Priority
from app.models.commission import (
    Commission,
    CommissionAgent,
    CommissionParticipation,
    CommissionProduct,
    CommissionSalesperson,
)
from app.models.core import Employee
from app.models.customer import Customer
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)

CUSTOMER = 20
AGENT_A, AGENT_B = 1, 2


async def setup_commissions(db: AsyncSession, *, scheme: int = 1) -> None:
    db.add(
        Employee(
            employee_id=AGENT_B,
            first_name='Beto',
            last_name='Luna',
            nickname='beto',
            gender=1,
            birthday=date(1990, 1, 1),
            sales_person=True,
            status=EntityStatus.ACTIVE,
            start_job_date=date(2020, 1, 1),
        )
    )
    db.add(
        Customer(
            customer_id=CUSTOMER,
            code='C20',
            name='Constructora',
            credit_limit=Decimal('0'),
            credit_days=0,
            price_list=1,
            salesperson=AGENT_A,
            status=EntityStatus.ACTIVE,
        )
    )
    await db.flush()
    db.add_all(
        [
            CommissionAgent(employee=AGENT_A, scheme=scheme),
            CommissionAgent(employee=AGENT_B, scheme=scheme),
            Commission(
                commission_id=1, name='CONCRETOS', commission_rate=Decimal('0.01'), comment='0'
            ),
            CommissionParticipation(commission_participation_id=1, name='CLIENTE VITALICIO'),
            CommissionParticipation(commission_participation_id=2, name='CANALIZACIÓN'),
            CommissionParticipation(commission_participation_id=3, name='ATENCIÓN EN CAMPO'),
        ]
    )
    await db.flush()
    db.add(CommissionProduct(product=1, commission=1))
    for agent in (AGENT_A, AGENT_B):
        for participation, share in ((1, '1'), (2, '0.5'), (3, '0.5')):
            db.add(
                CommissionSalesperson(
                    salesperson=agent,
                    commission=1,
                    commission_participation=participation,
                    participation_rate=Decimal(share),
                )
            )
    await db.commit()


async def order(
    db: AsyncSession,
    *,
    salesperson: int = AGENT_A,
    price: str = '10000',
    paid: bool = False,
    zeroed: datetime | None = None,
    when: datetime = datetime(2026, 3, 2, 10, 0),
) -> tuple[int, int]:
    """A completed order of one tax-included line for customer 20; returns (order, line)."""
    so = SalesOrder(
        creator=1,
        updater=1,
        creation_time=when,
        modification_time=when,
        facility=1,
        point_sale=1,
        salesperson=salesperson,
        customer=CUSTOMER,
        date=when,
        promise_date=when,
        due_date=when,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.IMMEDIATE,
        priority=Priority.NORMAL,
        completed=True,
        cancelled=False,
        paid=paid,
        delivered=False,
        serial=None,
        balance_zeroed_time=zeroed,
    )
    db.add(so)
    await db.flush()
    line = SalesOrderDetail(
        sales_order=so.sales_order_id,
        product=1,
        quantity=Decimal('1'),
        cost=Decimal('0'),
        price=Decimal(price),
        discount_rate=Decimal('0'),
        tax_rate=Decimal('0.16'),
        product_code='P1',
        product_name='Producto Uno',
        warehouse=1,
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        tax_included=True,
    )
    db.add(line)
    await db.commit()
    return so.sales_order_id, line.sales_order_detail_id


async def pay(
    db: AsyncSession, order_id: int, amount: str, when: datetime, *, cancelled: bool = False
) -> int:
    """One customer payment applied to the order; returns the application id."""
    payment = CustomerPayment(
        amount=Decimal(amount),
        method=1,
        date=when,
        customer=CUSTOMER,
        facility=1,
        serial=1,
        creator=1,
        updater=1,
        creation_time=when,
        modification_time=when,
        currency=CurrencyCode.MXN,
        payment_type=0,
    )
    db.add(payment)
    await db.flush()
    application = SalesOrderPayment(
        sales_order=order_id,
        customer_payment=payment.customer_payment_id,
        amount=Decimal(amount),
        amount_change=Decimal('0'),
        applier=1,
        date=when,
        cancelled=cancelled,
    )
    db.add(application)
    await db.commit()
    return application.sales_order_payment_id


async def refund(
    db: AsyncSession, order_id: int, line_id: int, *, quantity: str, price: str, when: datetime
) -> int:
    """A completed, tax-included refund of part of a line; returns the refund line id."""
    cr = CustomerRefund(
        sales_order=order_id,
        customer=CUSTOMER,
        creator=1,
        updater=1,
        sales_person=1,
        creation_time=when,
        modification_time=when,
        completed=True,
        cancelled=False,
        facility=1,
        serial=1,
        date=when,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
    )
    db.add(cr)
    await db.flush()
    detail = CustomerRefundDetail(
        customer_refund=cr.customer_refund_id,
        sales_order_detail=line_id,
        product=1,
        quantity=Decimal(quantity),
        price=Decimal(price),
        product_code='P1',
        product_name='Producto Uno',
        tax_rate=Decimal('0.16'),
        discount=Decimal('0'),
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        tax_included=True,
    )
    db.add(detail)
    await db.commit()
    return detail.customer_refund_detail_id
