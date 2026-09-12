"""Which orders the expiry sweep selects, decided by real rows rather than by a compiled string.

The unit tests for this sweep assert against the SQL it builds, which pins the predicates but
cannot answer the question #210 actually asked: *does a scheduled order survive it?* That depends
on a join through `delivery_order_detail` and on a cutoff chosen per row, and either could be
written plausibly and still select the wrong orders.

So these build the four orders the sweep has to tell apart — abandoned, promised, scheduled, and
scheduled-but-long-abandoned — and look at what comes back.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    CurrencyCode,
    DeliveryOrderStatus,
    PaymentTerms,
    Priority,
    TransactionType,
)
from app.models.inventory import LotSerialRqmt
from app.models.logistics import DeliveryOrder, DeliveryOrderDetail
from app.models.sales import SalesOrder, SalesOrderDetail
from app.services.order_expiry import find_expired

NOW = datetime(2026, 9, 12, 12, 0)


async def _order(
    db: AsyncSession, *, days_old: int, promise_in_days: int, reserved: bool = True
) -> int:
    """A confirmed, unpaid, undelivered order holding stock — what the sweep is looking for."""
    date = NOW - timedelta(days=days_old)
    order = SalesOrder(
        creator=1,
        updater=1,
        creation_time=date,
        modification_time=date,
        facility=1,
        point_sale=1,
        salesperson=1,
        customer=1,
        date=date,
        promise_date=NOW + timedelta(days=promise_in_days),
        due_date=date,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.IMMEDIATE,
        priority=Priority.NORMAL,
        completed=True,
        cancelled=False,
        paid=False,
        delivered=False,
        serial=1,
    )
    db.add(order)
    await db.flush()

    db.add(
        SalesOrderDetail(
            sales_order=order.sales_order_id,
            product=1,
            quantity=Decimal('10'),
            cost=Decimal('50'),
            price=Decimal('100'),
            discount_rate=Decimal('0'),
            tax_rate=Decimal('0.16'),
            product_code='P1',
            product_name='Producto Uno',
            warehouse=1,
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    if reserved:
        db.add(
            LotSerialRqmt(
                source=int(TransactionType.SALES_ORDER_RESERVATION),
                reference=order.sales_order_id,
                warehouse=1,
                product=1,
                quantity=Decimal('10'),
            )
        )
    await db.commit()
    return order.sales_order_id


async def _schedule(db: AsyncSession, order_id: int, *, status: DeliveryOrderStatus) -> None:
    """Plan a delivery for the order — the fact the sweep could not see."""
    line = (await db.execute(SalesOrderDetail.__table__.select())).all()
    sales_order_detail = next(
        row.sales_order_detail_id for row in line if row.sales_order == order_id
    )

    delivery = DeliveryOrder(
        creator=1,
        updater=1,
        creation_time=NOW,
        modification_time=NOW,
        facility=1,
        customer=1,
        date=NOW,
        priority=Priority.NORMAL,
        status=status,
    )
    db.add(delivery)
    await db.flush()
    db.add(
        DeliveryOrderDetail(
            delivery_order=delivery.delivery_order_id,
            sales_order_detail=sales_order_detail,
            product=1,
            quantity=Decimal('10'),
            product_code='P1',
            product_name='Producto Uno',
            warehouse=1,
        )
    )
    await db.commit()


async def test_an_abandoned_order_is_still_selected(db: AsyncSession, seeded: None) -> None:
    """The case the sweep was written for, unchanged: nobody scheduled anything."""
    abandoned = await _order(db, days_old=5, promise_in_days=-3)

    found = await find_expired(db, days=2, scheduled_days=30, now=NOW)

    assert [o.sales_order_id for o in found] == [abandoned]


async def test_an_order_promised_for_a_future_date_is_left_alone(
    db: AsyncSession, seeded: None
) -> None:
    """Patience, not abandonment. The goods are not due yet."""
    await _order(db, days_old=5, promise_in_days=10)

    found = await find_expired(db, days=2, scheduled_days=30, now=NOW)

    assert found == []


async def test_an_order_with_a_planned_delivery_is_left_alone(
    db: AsyncSession, seeded: None
) -> None:
    """The predicate #210 is really about: somebody has committed to delivering this.

    Its promise date is in the past, so only the delivery order saves it — which is the whole
    point, since `delivered` stays false until every line has actually gone out.
    """
    scheduled = await _order(db, days_old=5, promise_in_days=-1)
    await _schedule(db, scheduled, status=DeliveryOrderStatus.APPROVED)

    found = await find_expired(db, days=2, scheduled_days=30, now=NOW)

    assert found == []


async def test_a_cancelled_delivery_order_does_not_protect_it(
    db: AsyncSession, seeded: None
) -> None:
    """That shipment was called off, so it says nothing about anyone's intention to deliver."""
    order_id = await _order(db, days_old=5, promise_in_days=-1)
    await _schedule(db, order_id, status=DeliveryOrderStatus.CANCELLED)

    found = await find_expired(db, days=2, scheduled_days=30, now=NOW)

    assert [o.sales_order_id for o in found] == [order_id]


async def test_the_longer_window_is_a_window_not_an_exemption(
    db: AsyncSession, seeded: None
) -> None:
    """A scheduled order that nobody ever delivered is still abandoned — just later."""
    stale = await _order(db, days_old=40, promise_in_days=-30)
    await _schedule(db, stale, status=DeliveryOrderStatus.APPROVED)

    swept = await find_expired(db, days=2, scheduled_days=30, now=NOW)
    assert [o.sales_order_id for o in swept] == [stale]
    assert await find_expired(db, days=2, scheduled_days=60, now=NOW) == []


async def test_zero_scheduled_days_exempts_them_outright(db: AsyncSession, seeded: None) -> None:
    """The reading `UNPAID_ORDER_EXPIRY_DAYS=0` already has: 0 means the sweep leaves them alone.

    Read the other way — 0 days of patience — it would cancel every scheduled order on sight, which
    is the opposite of what an operator setting it to zero is asking for.
    """
    abandoned = await _order(db, days_old=40, promise_in_days=-30)
    scheduled = await _order(db, days_old=40, promise_in_days=-30)
    await _schedule(db, scheduled, status=DeliveryOrderStatus.APPROVED)

    found = await find_expired(db, days=2, scheduled_days=0, now=NOW)

    assert [o.sales_order_id for o in found] == [abandoned]


async def test_an_order_holding_no_stock_is_still_ignored(db: AsyncSession, seeded: None) -> None:
    """#118's load-bearing condition, unchanged by any of this: no reservation, nothing to give
    back."""
    await _order(db, days_old=5, promise_in_days=-3, reserved=False)

    assert await find_expired(db, days=2, scheduled_days=30, now=NOW) == []
