"""Closing, reopening and paying months, and the stability they buy (spec 025).

`local_now` is patched so that months can end on demand. Every scenario is driven through the
service, the way the routes call it.
"""

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.models.commission import Commission, CommissionPeriodLine, CommissionProduct
from app.models.customer import Customer
from app.models.sales import SalesOrder, SalesOrderPayment
from app.services import commission_service as svc
from tests.integration.commission_seed import (
    AGENT_B,
    CUSTOMER,
    order,
    pay,
    setup_commissions,
)

D = Decimal
USER = CurrentUser(
    user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=1
)


@contextmanager
def _today(day: datetime):
    with patch.object(svc, 'local_now', return_value=day):
        yield


def _snapshot(view) -> list[tuple]:  # noqa: ANN001
    return sorted(
        (x.sales_order_detail, x.salesperson, x.participation, x.movement, x.rate, x.commission)
        for x in view.lines
    )


async def _status(db: AsyncSession, year: int, month: int) -> str:
    return (await svc.read_period(db, year, month)).status


async def test_a_month_cannot_close_before_it_ends(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db)
    with _today(datetime(2026, 4, 30, 23, 0)), pytest.raises(HTTPException) as refused:
        await svc.close(db, 2026, 4, current=USER)

    assert refused.value.status_code == 409
    assert refused.value.detail == 'Period has not ended'


async def test_months_close_in_sequence(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db)
    with _today(datetime(2026, 6, 2)):
        await svc.close(db, 2026, 3, current=USER)  # the first close starts the chain
        with pytest.raises(HTTPException) as refused:
            await svc.close(db, 2026, 5, current=USER)
        await svc.close(db, 2026, 4, current=USER)
        await svc.close(db, 2026, 5, current=USER)

    assert refused.value.status_code == 409
    assert refused.value.detail == 'The previous period is not closed'
    assert await _status(db, 2026, 5) == 'closed'


async def test_a_closed_month_never_changes(db: AsyncSession, seeded: None) -> None:
    """SC-001: reassign, change a rate, relabel, edit the order: the stored lines stand."""
    await setup_commissions(db)
    order_id, _ = await order(db, paid=True, zeroed=datetime(2026, 4, 9))
    with _today(datetime(2026, 5, 2)):
        await svc.close(db, 2026, 4, current=USER)
    before = await svc.read_period(db, 2026, 4)

    await db.execute(
        update(Customer).where(Customer.customer_id == CUSTOMER).values(salesperson=AGENT_B)
    )
    await db.execute(update(Commission).values(commission_rate=D('0.05')))
    db.add(Commission(commission_id=2, name='OTRA', commission_rate=D('0.09'), comment='0'))
    await db.flush()
    await db.execute(update(CommissionProduct).values(commission=2))
    await db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == order_id).values(comment='edited')
    )
    await db.commit()
    after = await svc.read_period(db, 2026, 4)

    assert before.final and after.final
    assert _snapshot(after) == _snapshot(before)
    assert [t.commission for t in after.totals] == [D('100.00')]


async def test_only_the_latest_closed_month_reopens(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db)
    await order(db, paid=True, zeroed=datetime(2026, 4, 9))
    with _today(datetime(2026, 5, 2)):
        await svc.close(db, 2026, 3, current=USER)
        await svc.close(db, 2026, 4, current=USER)
        with pytest.raises(HTTPException) as refused:
            await svc.reopen(db, 2026, 3, current=USER)
        reopened = await svc.reopen(db, 2026, 4, current=USER)

    assert refused.value.detail == 'A later period is closed'
    assert reopened.reopened_by == 1 and reopened.reopened_at is not None
    lines = await db.scalar(select(func.count()).select_from(CommissionPeriodLine))
    assert lines == 0
    view = await svc.read_period(db, 2026, 4)
    assert view.status == 'open' and not view.final
    assert [x.commission for x in view.lines] == [D('100.00')]


async def test_a_paid_month_is_final(db: AsyncSession, seeded: None) -> None:
    await setup_commissions(db)
    with _today(datetime(2026, 5, 2)):
        with pytest.raises(HTTPException) as unclosed:
            await svc.pay(db, 2026, 4, current=USER)
        await svc.close(db, 2026, 4, current=USER)
        paid = await svc.pay(db, 2026, 4, current=USER)
        with pytest.raises(HTTPException) as reopen:
            await svc.reopen(db, 2026, 4, current=USER)
        with pytest.raises(HTTPException) as again:
            await svc.close(db, 2026, 4, current=USER)

    assert unclosed.value.detail == 'Close the period first'
    assert paid.paid_by == 1
    assert reopen.value.detail == 'A paid period cannot be reopened'
    assert again.value.status_code == 409
    assert await _status(db, 2026, 4) == 'paid'


async def test_scheme_one_late_pickup_end_to_end(db: AsyncSession, seeded: None) -> None:
    """Analysis C2: paid in March, reversed before March closed, paid again in May."""
    await setup_commissions(db)
    order_id, _ = await order(db, paid=False, zeroed=datetime(2026, 3, 5))
    with _today(datetime(2026, 6, 2)):
        await svc.close(db, 2026, 3, current=USER)
        await svc.close(db, 2026, 4, current=USER)
        await db.execute(
            update(SalesOrder).where(SalesOrder.sales_order_id == order_id).values(paid=True)
        )
        await db.commit()
        await svc.close(db, 2026, 5, current=USER)

    assert (await svc.read_period(db, 2026, 3)).lines == []
    assert [x.commission for x in (await svc.read_period(db, 2026, 5)).lines] == [D('100.00')]


async def test_scheme_two_reversal_after_full_payment_is_clawed_back(
    db: AsyncSession, seeded: None
) -> None:
    """Analysis C1: a fully stored line still sees a reversal."""
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db)
    first = await pay(db, order_id, '4000', datetime(2026, 3, 10))
    await pay(db, order_id, '6000', datetime(2026, 3, 20))
    with _today(datetime(2026, 5, 2)):
        await svc.close(db, 2026, 3, current=USER)
        await db.execute(
            update(SalesOrderPayment)
            .where(SalesOrderPayment.sales_order_payment_id == first)
            .values(cancelled=True)
        )
        await db.commit()
        await svc.close(db, 2026, 4, current=USER)

    march = (await svc.read_period(db, 2026, 3)).lines
    april = (await svc.read_period(db, 2026, 4)).lines
    assert [x.commission for x in march] == [D('100.00')]
    assert [x.commission for x in april] == [D('-40.00')]


async def test_no_key_is_paid_past_its_full_commission(db: AsyncSession, seeded: None) -> None:
    """SC-003/SC-006 over closed months: instalments, then a switch, then a payoff."""
    await setup_commissions(db, scheme=2)
    order_id, _ = await order(db)
    await pay(db, order_id, '4000', datetime(2026, 3, 10))
    with _today(datetime(2026, 6, 2)):
        await svc.close(db, 2026, 3, current=USER)
        await pay(db, order_id, '6000', datetime(2026, 4, 10))
        await db.execute(
            update(SalesOrder)
            .where(SalesOrder.sales_order_id == order_id)
            .values(paid=True, balance_zeroed_time=datetime(2026, 4, 10))
        )
        await db.commit()
        await svc.close(db, 2026, 4, current=USER)
        await svc.close(db, 2026, 5, current=USER)

    rows = (await db.execute(select(CommissionPeriodLine))).scalars().all()
    assert sum(r.commission for r in rows) == D('100.00')
    assert all(sum(r.commission for r in rows if r.salesperson == k) <= D('100.00') for k in {1})
