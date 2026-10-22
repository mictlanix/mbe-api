"""Gathering a month's facts and computing it on the real schema (spec 025, research R1).

The rules have their own exhaustive unit tests; these check that the queries pick the right orders,
payments and refunds for a month, and that money flows through to the lines.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CommissionMovement
from app.models.customer import Customer
from app.models.sales import SalesOrderPayment
from app.services import commission_service
from tests.integration.commission_seed import (
    AGENT_A,
    AGENT_B,
    CUSTOMER,
    order,
    pay,
    refund,
    setup_commissions,
)

D = Decimal


async def test_a_scheme_one_order_earns_in_the_month_it_is_paid(
    db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db)
    await order(db, paid=True, zeroed=datetime(2026, 3, 9))
    await order(db, paid=True, zeroed=datetime(2026, 2, 27))

    lines = await commission_service.compute(db, 2026, 3)

    assert [(x.salesperson, x.participation, x.commission) for x in lines] == [
        (AGENT_A, 1, D('100.00'))
    ]
    assert lines[0].paid_date == datetime(2026, 3, 9)


async def test_two_agents_split_an_order_written_by_someone_else(
    db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db)
    await order(db, salesperson=AGENT_B, paid=True, zeroed=datetime(2026, 3, 9))

    lines = await commission_service.compute(db, 2026, 3)

    assert {(x.salesperson, x.participation, x.commission) for x in lines} == {
        (AGENT_B, 2, D('50.00')),
        (AGENT_A, 3, D('50.00')),
    }


async def test_a_refund_before_payment_nets_in_the_paid_month(
    db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db)
    order_id, line_id = await order(db, paid=True, zeroed=datetime(2026, 4, 5))
    await refund(db, order_id, line_id, quantity='0.3', price='10000', when=datetime(2026, 3, 20))

    march = await commission_service.compute(db, 2026, 3)
    april = await commission_service.compute(db, 2026, 4)

    assert march == []
    assert sorted(x.commission for x in april) == [D('-30.00'), D('100.00')]
    assert {x.movement for x in april} == {CommissionMovement.SALE, CommissionMovement.REFUND}


async def test_scheme_two_follows_the_payments_applied_in_the_month(
    db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db)
    await pay(db, order_id, '4000', datetime(2026, 3, 15))

    lines = await commission_service.compute(db, 2026, 3)

    assert [(x.commission, x.share) for x in lines] == [(D('40.00'), D('0.400000'))]
    assert lines[0].paid_date == datetime(2026, 3, 15)


async def test_an_application_in_a_later_month_does_not_count_yet(
    db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db)
    await pay(db, order_id, '4000', datetime(2026, 3, 15))
    await pay(db, order_id, '6000', datetime(2026, 4, 2))

    march = await commission_service.compute(db, 2026, 3)

    assert [x.commission for x in march] == [D('40.00')]


async def test_a_cancelled_application_is_not_counted(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db)
    application = await pay(db, order_id, '4000', datetime(2026, 3, 15))
    await db.execute(
        update(SalesOrderPayment)
        .where(SalesOrderPayment.sales_order_payment_id == application)
        .values(cancelled=True)
    )
    await db.commit()

    assert await commission_service.compute(db, 2026, 3) == []


async def test_the_salesperson_filter(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db)
    await order(db, salesperson=AGENT_B, paid=True, zeroed=datetime(2026, 3, 9))

    lines = await commission_service.compute(db, 2026, 3, salesperson=AGENT_B)

    assert [(x.salesperson, x.participation) for x in lines] == [(AGENT_B, 2)]


async def test_a_customer_without_a_salesperson_earns_nothing(
    db: AsyncSession, seeded: None
) -> None:
    """Parity with the current system: its branches compare csp with osp, and NULL matches none."""
    await setup_commissions(db)
    await db.execute(
        update(Customer).where(Customer.customer_id == CUSTOMER).values(salesperson=None)
    )
    await db.commit()
    await order(db, paid=True, zeroed=datetime(2026, 3, 9))

    assert await commission_service.compute(db, 2026, 3) == []
