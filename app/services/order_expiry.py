"""Retiring confirmed orders nobody ever paid for or delivered.

Confirming a sales order reserves its stock (FR-055). The reservation is released when the order
is cancelled, when its goods depart, or on a counter pickup — so an order confirmed and then
abandoned holds stock indefinitely, visible on the shelf but not available to promise. Nothing
noticed, because on-hand stays correct and it is *availability* that quietly falls (#118).

This sweep closes that: an order still neither paid nor delivered `unpaid_order_expiry_days` after
its date, **and still holding a reservation**, is cancelled — and cancelling releases the
reservation through the path that already exists. The stock condition keeps the sweep to its
purpose; without it, it retires historical orders that hold nothing (see `find_expired`).

It deliberately reuses `sales_order_service.cancel_order` rather than deleting
reservations directly, so an expired order is retired by exactly the same code, and subject to
exactly the same guards, as one a person cancels.

Those guards are the reason the sweep reports rather than forces. A partially-paid order holds
live payment applications and cannot be cancelled until they are reversed — that is a decision for
a person, so the sweep skips it and names it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import TransactionType
from app.models.core import Employee
from app.models.inventory import LotSerialRqmt
from app.models.sales import SalesOrder
from app.services import sales_order_service


@dataclass(frozen=True)
class ExpiryReport:
    """What the sweep did, and what it could not do.

    `skipped` is the interesting half: those orders are still holding stock and still need a
    human, so a run that reports nothing but successes is not the same as a clean run.
    """

    cancelled: list[int]
    skipped: list[tuple[int, str]]

    @property
    def total(self) -> int:
        return len(self.cancelled) + len(self.skipped)


async def find_expired(
    db: AsyncSession, *, days: int, now: datetime | None = None
) -> Sequence[SalesOrder]:
    """Confirmed orders past the cutoff that are neither paid nor delivered **and hold stock**.

    Dated from `sales_order.date` rather than a confirmation timestamp: there is no column
    recording when an order was confirmed, and `modification_time` moves on every subsequent edit,
    so it would keep pushing the cutoff away from an order that is being fiddled with rather than
    fulfilled.

    The reservation condition is the load-bearing one, and it is not merely an optimisation.
    Reservations exist only for orders confirmed after this model shipped — none were backfilled.
    Without it the sweep matches every historical order that was never paid or delivered and
    cancels them wholesale: measured on 2026-07-28, **1,363 orders matched the age and payment
    rules and not one held a reservation**. That is a mass retirement of historical documents
    releasing nothing at all. The sweep exists to give back stock, so it goes no further than the
    orders actually holding some.
    """
    cutoff = (now or datetime.now()) - timedelta(days=days)

    holds_stock = (
        select(LotSerialRqmt.lot_serial_rqmt_id)
        .where(
            LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
            LotSerialRqmt.reference == SalesOrder.sales_order_id,
        )
        .exists()
    )

    return (
        (
            await db.execute(
                select(SalesOrder).where(
                    SalesOrder.completed.is_(True),
                    SalesOrder.cancelled.is_(False),
                    SalesOrder.paid.is_(False),
                    SalesOrder.delivered.is_(False),
                    SalesOrder.date < cutoff,
                    holds_stock,
                )
            )
        )
        .scalars()
        .all()
    )


async def _assert_employee_exists(db: AsyncSession, employee: int) -> None:
    """Fail before the first cancellation, not during it.

    `sales_order.updater` is an enforced foreign key, so a missing employee surfaces as error 1452
    partway through the sweep — after some orders have already been cancelled and others have not.
    Checking once up front makes the run all-or-nothing.
    """
    found = (
        await db.execute(select(Employee.employee_id).where(Employee.employee_id == employee))
    ).scalar_one_or_none()

    if found is None:
        raise RuntimeError(
            f'SYSTEM_EMPLOYEE_ID={employee} names no employee. Migration 010 seeds the system '
            'employee at -1; point the setting at it, or at a real employee.'
        )


async def expire_unpaid_orders(
    db: AsyncSession,
    *,
    days: int | None = None,
    employee: int | None = None,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ExpiryReport:
    """Cancel every expired order, releasing the stock each was holding."""
    days = settings.unpaid_order_expiry_days if days is None else days
    if days <= 0:
        return ExpiryReport(cancelled=[], skipped=[])

    employee = settings.system_employee_id if employee is None else employee
    await _assert_employee_exists(db, employee)

    orders = await find_expired(db, days=days, now=now)
    if dry_run:
        return ExpiryReport(cancelled=[o.sales_order_id for o in orders], skipped=[])

    current = CurrentUser(
        user_id='system',
        session_version=0,
        administrator=True,
        facility_id=None,
        employee_id=employee,
    )

    cancelled: list[int] = []
    skipped: list[tuple[int, str]] = []

    for order in orders:
        try:
            await sales_order_service.cancel_order(db, order, current=current)
        except HTTPException as exc:
            # Almost always a partially-paid order holding live applications. Reversing those is
            # somebody's decision, not a sweep's.
            skipped.append((order.sales_order_id, str(exc.detail)))
        else:
            cancelled.append(order.sales_order_id)

    return ExpiryReport(cancelled=cancelled, skipped=skipped)
