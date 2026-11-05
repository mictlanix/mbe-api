"""Store each month-end's open credit orders with their balances (spec 038, research R8).

    uv run python -m app.jobs.snapshot_receivables                          # months not yet written
    uv run python -m app.jobs.snapshot_receivables --from 2025-01           # backfill from a month
    uv run python -m app.jobs.snapshot_receivables --month 2026-03 --rebuild  # replace one month

Run it early each month. Without arguments it writes every ended month after the last one written,
or only the month that just ended when none is. A written month is skipped unless rebuilt. Each
month is one `open_credit_orders(as_of=<month-end 23:59:59>)` call, spec 033's rule (R7), written
in one transaction: its rows, or a marker when it had no open order.

The customer's agent and credit limit are today's. Only the month that just ended is written at its
month-end; every earlier one is backfilled and marks its limit `limit_historical = 0`. Months before
2025-01 are refused with `analytics.before_history`. Writing on mbe_dev needs the user's OK.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.errors import DomainError
from app.db.session import AsyncSessionLocal, engine
from app.models.analytics import ReceivableSnapshot, ReceivableSnapshotMonth
from app.models.customer import Customer
from app.schemas.analytics import MeasureKey
from app.services import sales_order_service
from app.services.analytics.engine import history_guard


def month_end(day: date) -> date:
    """The last day of `day`'s month."""
    first_of_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_of_next - timedelta(days=1)


def _ended(now: datetime) -> date:
    """The last month-end before `now`'s month."""
    return now.date().replace(day=1) - timedelta(days=1)


def _month_ends(first: date, last: date) -> list[date]:
    found = []
    current = month_end(first)
    while current <= last:
        found.append(current)
        current = month_end(current + timedelta(days=1))
    return found


async def _written(db: AsyncSession) -> set[date]:
    """Month-ends with rows, and those marked written with none."""
    rows = await db.execute(select(ReceivableSnapshot.month_end).distinct())
    markers = await db.execute(select(ReceivableSnapshotMonth.month_end))
    return {_day(d) for d in rows.scalars()} | {_day(d) for d in markers.scalars()}


def _day(value: date | str) -> date:
    """SQLite hands a `DISTINCT` date back as text."""
    return value if isinstance(value, date) else date.fromisoformat(str(value))


async def write_month(db: AsyncSession, day: date, *, now: datetime) -> int:
    """Replace `day`'s month-end in one transaction; the number of open orders it holds."""
    at = datetime.combine(day, time(23, 59, 59))
    orders = await sales_order_service.open_credit_orders(db, as_of=at)
    customers = {order.customer for order, _ in orders}
    found = (
        await db.execute(
            select(Customer.customer_id, Customer.salesperson, Customer.credit_limit).where(
                Customer.customer_id.in_(customers)
            )
        )
        if customers
        else None
    )
    people = {c: (agent, limit) for c, agent, limit in found.all()} if found is not None else {}
    historical = day == _ended(now)
    await db.execute(delete(ReceivableSnapshot).where(ReceivableSnapshot.month_end == day))
    marker = ReceivableSnapshotMonth
    await db.execute(delete(marker).where(marker.month_end == day))
    for order, balance in orders:
        agent, limit = people.get(order.customer, (None, None))
        db.add(
            ReceivableSnapshot(
                month_end=day,
                sales_order=order.sales_order_id,
                customer=order.customer,
                facility=order.facility,
                salesperson=order.salesperson,
                sales_agent=agent,
                due_date=order.due_date,
                currency=int(order.currency),
                exchange_rate=order.exchange_rate,
                total=balance.total,
                balance=balance.balance,
                credit_limit=limit,
                limit_historical=historical,
                written_at=now,
            )  # fmt: skip
        )
    if not orders:
        db.add(ReceivableSnapshotMonth(month_end=day, written_at=now, orders=0))
    await db.commit()
    return len(orders)


async def snapshot(
    db: AsyncSession,
    *,
    now: datetime,
    since: date | None = None,
    month: date | None = None,
    rebuild: bool = False,
) -> list[tuple[date, int]]:
    """Write the month-ends asked for that aren't written yet, or `month` again with `rebuild`.
    The month-ends written, with their open orders."""
    last = _ended(now)
    written = await _written(db)
    if month is not None:
        days = [month_end(month)]
    elif since is not None:
        days = _month_ends(since, last)
    elif written:
        days = _month_ends(max(written) + timedelta(days=1), last)
    else:
        days = [last]
    if days:
        history_guard([MeasureKey.RECEIVABLE], days[0], days[-1])
    if any(day > last for day in days):
        raise ValueError(f'{days[-1]:%Y-%m} has not ended')
    done = []
    for day in days:
        if day in written and not rebuild:
            continue
        done.append((day, await write_month(db, day, now=now)))
    return done


def _month(text: str) -> date:
    return datetime.strptime(text, '%Y-%m').date()


def parse(argv: list[str] | None) -> tuple[date | None, date | None, bool]:
    """`(since, month, rebuild)` from the command line."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--from', dest='since', type=_month, help='backfill from YYYY-MM')
    parser.add_argument('--month', type=_month, help='one month, YYYY-MM')
    parser.add_argument('--rebuild', action='store_true', help='replace the --month written')
    args = parser.parse_args(argv)
    if args.rebuild and args.month is None:
        parser.error('--rebuild needs --month')
    if args.since is not None and args.month is not None:
        parser.error('give --from or --month, not both')
    return args.since, args.month, args.rebuild


async def _run(since: date | None, month: date | None, rebuild: bool) -> list[tuple[date, int]]:
    # Dispose inside the loop that opened the pool, as `expire_orders` does.
    try:
        async with AsyncSessionLocal() as db:
            return await snapshot(db, now=local_now(), since=since, month=month, rebuild=rebuild)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    since, month, rebuild = parse(argv)
    try:
        written = asyncio.run(_run(since, month, rebuild))
    except (DomainError, ValueError) as refused:
        print(f'refused: {refused.detail if isinstance(refused, DomainError) else refused}')
        return 2
    for day, orders in written:
        print(f'  {day:%Y-%m-%d}  {orders} open order(s)')
    print(f'wrote {len(written)} month-end(s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
