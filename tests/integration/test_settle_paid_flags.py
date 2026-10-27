"""The one-time correction of stale `paid` flags (spec 033, US6, FR-012).

Orders that owe nothing by the one balance rule but are still flagged unpaid: 257 on mbe_dev, 3 from
2024, 219 from 2025 and 35 from 2026. A dry run lists them; applying flags them paid, with a zero
time only from 2026 on, and one incidence each. A second run finds nothing.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import SYSTEM_EMPLOYEE_ID
from app.enums import PaymentTerms, SourceType
from app.models.incidence import Incidence
from app.models.sales import SalesOrder, SalesOrderPayment
from app.services import employee_service
from app.services.customer_payment_service import settle_stale_flags
from tests.integration.test_accounts_receivable_flow import _customer, _order, _payment

PAID_ON = datetime(2026, 3, 4, 12, 0)


@pytest.fixture
async def stale(db: AsyncSession, seeded: None) -> dict[str, int]:
    await employee_service.ensure_system_employee(db)
    await db.commit()
    await _customer(db, 21, limit='50000')
    old, _ = await _order(db, 21, '500', date=datetime(2025, 6, 1))
    await _payment(db, 21, '500', applied=((old, '500'),))
    recent, _ = await _order(db, 21, '700', date=datetime(2026, 2, 1))
    await _payment(db, 21, '700', applied=((recent, '700'),))
    owing, _ = await _order(db, 21, '900', date=datetime(2026, 2, 1))
    paid, _ = await _order(db, 21, '300', date=datetime(2026, 2, 1), paid=True)
    cash, _ = await _order(db, 21, '100', date=datetime(2026, 2, 1))
    await _payment(db, 21, '100', applied=((cash, '100'),))
    await db.execute(
        SalesOrder.__table__.update()
        .where(SalesOrder.sales_order_id == cash)
        .values(payment_terms=PaymentTerms.IMMEDIATE)
    )
    await db.execute(
        SalesOrderPayment.__table__.update()
        .where(SalesOrderPayment.sales_order == recent)
        .values(date=PAID_ON)
    )
    await db.commit()
    return {'old': old, 'recent': recent, 'owing': owing, 'paid': paid, 'cash': cash}


async def _row(db: AsyncSession, order_id: int) -> SalesOrder:
    db.expire_all()
    return await db.get(SalesOrder, order_id)


async def test_the_dry_run_lists_and_writes_nothing(db: AsyncSession, stale: dict) -> None:
    report = await settle_stale_flags(db, apply=False)

    assert [row['sales_order_id'] for row in report] == [stale['old'], stale['recent']]
    assert report[0]['balance'] == Decimal('0.00')
    assert (report[0]['zero_time'], report[1]['zero_time']) == (None, PAID_ON)
    assert (await _row(db, stale['old'])).paid is False
    assert (await db.execute(select(Incidence))).first() is None


async def test_applying_flags_them_paid_and_records_each(db: AsyncSession, stale: dict) -> None:
    await settle_stale_flags(db, apply=True)

    old = await _row(db, stale['old'])
    assert (old.paid, old.balance_zeroed_time) == (True, None), 'no commission month before 2026'
    recent = await _row(db, stale['recent'])
    assert (recent.paid, recent.balance_zeroed_time) == (True, PAID_ON)
    assert (await _row(db, stale['owing'])).paid is False
    assert (await _row(db, stale['cash'])).paid is False, 'only credit orders'
    incidences = list(
        (
            await db.execute(
                select(Incidence).where(Incidence.source == int(SourceType.SALES_ORDER))
            )
        ).scalars()
    )
    assert sorted(i.instance_id for i in incidences) == [stale['old'], stale['recent']]
    for incidence in incidences:
        assert incidence.updater == SYSTEM_EMPLOYEE_ID
        assert incidence.comment == 'Paid flag corrected: balance is settled (spec 033)'


async def test_a_second_run_finds_nothing(db: AsyncSession, stale: dict) -> None:
    await settle_stale_flags(db, apply=True)

    assert await settle_stale_flags(db, apply=True) == []
