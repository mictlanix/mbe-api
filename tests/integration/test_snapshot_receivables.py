"""Spec 038, T025: the month-end receivable snapshots and their job (research R8), on SQLite.

The job is driven through `snapshot`, the function its command line calls, on the test database;
it never reaches a real one.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import DomainError
from app.enums import CurrencyCode, PaymentTerms, Priority
from app.jobs import snapshot_receivables as job
from app.models.analytics import ReceivableSnapshot, ReceivableSnapshotMonth
from app.models.customer import Customer
from app.models.sales import SalesOrder, SalesOrderDetail

pytestmark = pytest.mark.usefixtures('seeded')

NOW = datetime(2026, 4, 2, 6, 0)
JANUARY, FEBRUARY, MARCH = date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31)


async def _order(db: AsyncSession, confirmed: datetime, price: str = '1000') -> int:
    order = SalesOrder(
        creator=1, updater=1, creation_time=confirmed, modification_time=confirmed,
        facility=1, point_sale=1, salesperson=1, customer=1,
        date=confirmed, promise_date=confirmed, due_date=confirmed + timedelta(days=30),
        currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
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
            warehouse=1, exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
            tax_included=False,
        )  # fmt: skip
    )
    await db.commit()
    return order.sales_order_id


@pytest.fixture
async def orders(db: AsyncSession) -> tuple[int, int]:
    """No open credit order of these at January's end; one from February, one more from March.

    The baseline's own orders have immediate terms, so these are the only ones."""
    customer = await db.get(Customer, 1)
    customer.credit_limit = Decimal('5000')
    customer.salesperson = 1
    await db.commit()
    return await _order(db, datetime(2026, 2, 10, 9)), await _order(db, datetime(2026, 3, 5, 9))


async def _rows(db: AsyncSession) -> list[tuple]:
    found = await db.execute(
        select(ReceivableSnapshot).order_by(
            ReceivableSnapshot.month_end, ReceivableSnapshot.sales_order
        )
    )
    return [
        (
            r.month_end, r.sales_order, r.customer, r.facility, r.salesperson, r.sales_agent,
            r.due_date, r.currency, r.exchange_rate, r.total, r.balance, r.credit_limit,
            r.limit_historical,
        )
        for r in found.scalars()
    ]  # fmt: skip


async def _markers(db: AsyncSession) -> list[tuple[date, int]]:
    found = await db.execute(
        select(ReceivableSnapshotMonth.month_end, ReceivableSnapshotMonth.orders).order_by(
            ReceivableSnapshotMonth.month_end
        )
    )
    return [tuple(r) for r in found.all()]


async def test_one_row_per_open_order_per_month_end_and_a_marker_for_none(
    db: AsyncSession, orders: tuple[int, int]
) -> None:
    february, march = orders

    written = await job.snapshot(db, now=NOW, since=date(2026, 1, 1))

    assert written == [(JANUARY, 0), (FEBRUARY, 1), (MARCH, 2)]
    rows = await _rows(db)
    assert [(r[0], r[1]) for r in rows] == [
        (FEBRUARY, february),
        (MARCH, february),
        (MARCH, march),
    ]
    first = rows[0]
    assert first[2:6] == (1, 1, 1, 1), 'customer, facility, salesperson and the agent'
    assert (first[9], first[10], first[11]) == (Decimal('1000.00'), Decimal('1000.00'), 5000)
    assert await _markers(db) == [(JANUARY, 0)]


async def test_a_second_run_writes_nothing(db: AsyncSession, orders: tuple[int, int]) -> None:
    await job.snapshot(db, now=NOW, since=date(2026, 1, 1))
    before = await _rows(db)

    again = await job.snapshot(db, now=NOW, since=date(2026, 1, 1))
    latest = await job.snapshot(db, now=NOW)

    assert again == [] and latest == []
    assert await _rows(db) == before


async def test_without_arguments_only_the_months_after_the_last_written(
    db: AsyncSession, orders: tuple[int, int]
) -> None:
    assert await job.snapshot(db, now=NOW) == [(MARCH, 2)], 'nothing written: the last month'
    assert await job.snapshot(db, now=NOW + timedelta(days=31)) == [(date(2026, 4, 30), 2)]


async def test_a_rebuild_of_an_unchanged_month_gives_identical_rows(
    db: AsyncSession, orders: tuple[int, int]
) -> None:
    await job.snapshot(db, now=NOW, since=date(2026, 1, 1))
    before = await _rows(db)

    rebuilt = await job.snapshot(db, now=NOW, month=MARCH, rebuild=True)

    assert rebuilt == [(MARCH, 2)]
    assert await _rows(db) == before


async def test_a_rebuild_happens_in_one_transaction(
    db: AsyncSession, orders: tuple[int, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure while computing the month leaves the stored month as it was."""
    await job.snapshot(db, now=NOW, since=date(2026, 1, 1))
    before = await _rows(db)

    async def broken(*args: object, **kwargs: object) -> list:
        raise RuntimeError('lost the connection')

    monkeypatch.setattr(job.sales_order_service, 'open_credit_orders', broken)
    with pytest.raises(RuntimeError):
        await job.snapshot(db, now=NOW, month=MARCH, rebuild=True)
    await db.rollback()

    assert await _rows(db) == before


async def test_a_month_before_the_history_is_refused(db: AsyncSession) -> None:
    with pytest.raises(DomainError) as refused:
        await job.snapshot(db, now=NOW, since=date(2024, 12, 1))

    assert refused.value.code == 'analytics.before_history'
    assert refused.value.params == {
        'history': 'receivables',
        'requested': '2024-12-31',
        'earliest': '2025-01-31',
    }
    assert await _rows(db) == []


async def test_backfilled_months_keep_todays_limit_as_not_historical(
    db: AsyncSession, orders: tuple[int, int]
) -> None:
    """Only the month that just ended is written at its month-end; earlier ones are backfilled."""
    await job.snapshot(db, now=NOW, since=date(2026, 1, 1))

    flags = {(r[0], r[12]) for r in await _rows(db)}
    assert flags == {(FEBRUARY, False), (MARCH, True)}


def test_the_command_line_reads_months() -> None:
    assert job.parse(['--from', '2025-01']) == (date(2025, 1, 1), None, False)
    assert job.parse(['--month', '2026-03', '--rebuild']) == (None, date(2026, 3, 1), True)
    assert job.parse([]) == (None, None, False)
    with pytest.raises(SystemExit):
        job.parse(['--rebuild'])
