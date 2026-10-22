"""Commissions: facts, computation, periods and settings (spec 025).

The money rules live in `commission_rules`, which is pure. This module gathers the facts a month
needs, hands them to `commission_rules.month_lines`, and stores what a close returns. The facts are
SQLAlchemy Core queries over the mapped columns, not raw SQL, so they type the same on MariaDB and
on the SQLite the integration tests run on.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, case, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CommissionMovement, CommissionPeriodStatus, CommissionScheme
from app.models.commission import (
    Commission,
    CommissionAgent,
    CommissionParticipation,
    CommissionPeriod,
    CommissionPeriodLine,
    CommissionProduct,
    CommissionSalesperson,
)
from app.models.customer import Customer
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.services import commission_rules as rules

CENTS = Decimal('0.01')
# A legacy row's "unset" `balance_zeroed_time`.
_SENTINEL = datetime(1900, 1, 1)


def month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    """`[first day 00:00, first day of next month 00:00)` in business local time."""
    start = datetime(year, month, 1)
    return start, datetime(year + (month == 12), month % 12 + 1, 1)


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(CENTS) if value is not None else Decimal(0)


def _decimal(value: object) -> Decimal:
    return Decimal(str(value)) if value is not None else Decimal(0)


def _unit_price(price, discount, tax_rate, tax_included):  # noqa: ANN001, ANN202
    """The current system's line price: after discount, tax included, rounded to cents."""
    return func.round(
        price * (1 - discount) * (1 + case((tax_included.is_(True), 0), else_=tax_rate)), 2
    )


_SALE_PRICE = _unit_price(
    SalesOrderDetail.price,
    SalesOrderDetail.discount_rate,
    SalesOrderDetail.tax_rate,
    SalesOrderDetail.tax_included,
)
_REFUND_PRICE = _unit_price(
    CustomerRefundDetail.price,
    CustomerRefundDetail.discount,
    CustomerRefundDetail.tax_rate,
    CustomerRefundDetail.tax_included,
)
_APPLICATION_DATE = func.coalesce(SalesOrderPayment.date, CustomerPayment.date)
_CLOSED = (int(CommissionPeriodStatus.CLOSED), int(CommissionPeriodStatus.PAID))


def _stored_rows():  # noqa: ANN202
    """Lines of closed and paid months, the ledger every computation subtracts."""
    return (
        select(CommissionPeriodLine)
        .join(
            CommissionPeriod,
            CommissionPeriod.commission_period_id == CommissionPeriodLine.commission_period,
        )
        .where(CommissionPeriod.status.in_(_CLOSED))
    )


async def chain_start(db: AsyncSession) -> datetime | None:
    """The first month this system closed: where the sequence of closed months begins."""
    row = (
        await db.execute(
            select(CommissionPeriod.year, CommissionPeriod.month)
            .where(CommissionPeriod.status.in_(_CLOSED))
            .order_by(CommissionPeriod.year, CommissionPeriod.month)
            .limit(1)
        )
    ).first()
    return datetime(row.year, row.month, 1) if row else None


async def _settings(db: AsyncSession) -> rules.Settings:
    agents = {
        employee: scheme or int(CommissionScheme.FULLY_PAID)
        for employee, scheme in (
            await db.execute(select(CommissionAgent.employee, CommissionAgent.scheme))
        ).all()
    }
    rates = {
        (r.salesperson, r.commission, r.commission_participation): _decimal(r.participation_rate)
        for r in (await db.execute(select(CommissionSalesperson))).scalars()
    }
    participations = {
        p.commission_participation_id: p.name
        for p in (await db.execute(select(CommissionParticipation))).scalars()
    }
    return rules.Settings(agents=agents, rates=rates, participations=participations)


async def _candidates(
    db: AsyncSession, start: datetime, nxt: datetime, chain: datetime | None
) -> list[rules.CandidateLine]:
    """Research R1 (a)–(d): every line the month could owe something on."""
    agents = select(CommissionAgent.employee)
    stored = _stored_rows().subquery()
    zeroed = and_(
        SalesOrder.balance_zeroed_time.is_not(None),
        SalesOrder.balance_zeroed_time >= _SENTINEL,
        SalesOrder.balance_zeroed_time < nxt,
    )
    paid_now = and_(
        SalesOrder.paid.is_(True),
        zeroed,
        or_(
            SalesOrder.balance_zeroed_time >= start,
            and_(
                SalesOrder.balance_zeroed_time >= chain,
                ~exists().where(
                    stored.c.sales_order_detail == SalesOrderDetail.sales_order_detail_id,
                    stored.c.movement == int(CommissionMovement.SALE),
                ),
            )
            if chain is not None
            else false(),
        ),
    )
    applied_in_month = exists().where(
        SalesOrderPayment.sales_order == SalesOrder.sales_order_id,
        SalesOrderPayment.customer_payment == CustomerPayment.customer_payment_id,
        SalesOrderPayment.cancelled.is_(False),
        _APPLICATION_DATE >= start,
        _APPLICATION_DATE < nxt,
    )
    refunded_in_month = exists().where(
        CustomerRefundDetail.sales_order_detail == SalesOrderDetail.sales_order_detail_id,
        CustomerRefund.customer_refund_id == CustomerRefundDetail.customer_refund,
        CustomerRefund.completed.is_(True),
        CustomerRefund.cancelled.is_(False),
        CustomerRefund.date >= start,
        CustomerRefund.date < nxt,
    )
    has_stored_partial = exists().where(
        stored.c.sales_order_detail == SalesOrderDetail.sales_order_detail_id,
        stored.c.scheme == int(CommissionScheme.PARTIAL_PAYMENTS),
        stored.c.movement == int(CommissionMovement.SALE),
    )
    has_stored = exists().where(
        stored.c.sales_order_detail == SalesOrderDetail.sales_order_detail_id
    )

    query = (
        select(
            SalesOrder.sales_order_id,
            SalesOrderDetail.sales_order_detail_id,
            SalesOrder.date,
            Customer.customer_id,
            Customer.name,
            SalesOrder.salesperson,
            Customer.salesperson.label('csp'),
            SalesOrderDetail.product,
            SalesOrderDetail.product_name,
            _SALE_PRICE.label('price'),
            SalesOrderDetail.quantity,
            SalesOrder.balance_zeroed_time,
            SalesOrder.paid,
            CommissionProduct.commission.label('label'),
            Commission.name.label('label_name'),
            Commission.commission_rate,
        )
        .join(SalesOrderDetail, SalesOrderDetail.sales_order == SalesOrder.sales_order_id)
        .join(Customer, Customer.customer_id == SalesOrder.customer)
        .outerjoin(CommissionProduct, CommissionProduct.product == SalesOrderDetail.product)
        .outerjoin(Commission, Commission.commission_id == CommissionProduct.commission)
        .where(
            SalesOrder.completed.is_(True),
            SalesOrder.cancelled.is_(False),
            or_(
                SalesOrder.salesperson.in_(agents),
                Customer.salesperson.in_(agents),
                has_stored,
            ),
            or_(paid_now, applied_in_month, refunded_in_month, has_stored_partial),
        )
    )
    lines = []
    for r in (await db.execute(query)).all():
        zeroed_at = r.balance_zeroed_time
        lines.append(
            rules.CandidateLine(
                sales_order=r.sales_order_id,
                sales_order_detail=r.sales_order_detail_id,
                order_date=r.date,
                customer=r.customer_id,
                customer_name=r.name,
                osp=r.salesperson,
                csp=r.csp,
                product=r.product,
                product_name=r.product_name,
                price=_money(r.price),
                quantity=_decimal(r.quantity),
                balance_zeroed_time=zeroed_at if zeroed_at and zeroed_at >= _SENTINEL else None,
                is_paid=bool(r.paid),
                label=r.label,
                label_name=r.label_name,
                label_rate=_decimal(r.commission_rate) if r.label is not None else Decimal(0),
            )
        )
    return lines


async def _refunds(
    db: AsyncSession, details: list[int], nxt: datetime
) -> list[rules.RefundLine]:
    if not details:
        return []
    rows = (
        await db.execute(
            select(
                CustomerRefundDetail.customer_refund_detail_id,
                CustomerRefundDetail.sales_order_detail,
                CustomerRefund.date,
                (-_REFUND_PRICE).label('price'),
                CustomerRefundDetail.quantity,
            )
            .join(
                CustomerRefund,
                CustomerRefund.customer_refund_id == CustomerRefundDetail.customer_refund,
            )
            .where(
                CustomerRefundDetail.sales_order_detail.in_(details),
                CustomerRefund.completed.is_(True),
                CustomerRefund.cancelled.is_(False),
                CustomerRefund.date < nxt,
            )
        )
    ).all()
    return [
        rules.RefundLine(
            customer_refund_detail=r.customer_refund_detail_id,
            sales_order_detail=r.sales_order_detail,
            date=r.date,
            price=_money(r.price),
            quantity=_decimal(r.quantity),
        )
        for r in rows
    ]


async def _order_money(
    db: AsyncSession, orders: list[int], start: datetime, nxt: datetime
) -> dict[int, rules.OrderMoney]:
    """What each order has been paid, refunded and is worth, as of the month's end (scheme 2)."""
    if not orders:
        return {}
    order_total: dict[int, Decimal] = {}
    for order, amount in (
        await db.execute(
            select(SalesOrderDetail.sales_order, _SALE_PRICE * SalesOrderDetail.quantity).where(
                SalesOrderDetail.sales_order.in_(orders)
            )
        )
    ).all():
        order_total[order] = order_total.get(order, Decimal(0)) + _money(amount)

    applied: dict[int, Decimal] = {}
    last: dict[int, datetime] = {}
    for order, amount, date in (
        await db.execute(
            select(SalesOrderPayment.sales_order, SalesOrderPayment.amount, _APPLICATION_DATE)
            .join(
                CustomerPayment,
                CustomerPayment.customer_payment_id == SalesOrderPayment.customer_payment,
            )
            .where(
                SalesOrderPayment.sales_order.in_(orders),
                SalesOrderPayment.cancelled.is_(False),
                _APPLICATION_DATE < nxt,
            )
        )
    ).all():
        applied[order] = applied.get(order, Decimal(0)) + _decimal(amount)
        if date is not None and date >= start and (order not in last or date > last[order]):
            last[order] = date

    refunded: dict[int, Decimal] = {}
    for order, amount, date in (
        await db.execute(
            select(
                SalesOrderDetail.sales_order,
                _REFUND_PRICE * CustomerRefundDetail.quantity,
                CustomerRefund.date,
            )
            .join(
                SalesOrderDetail,
                SalesOrderDetail.sales_order_detail_id == CustomerRefundDetail.sales_order_detail,
            )
            .join(
                CustomerRefund,
                CustomerRefund.customer_refund_id == CustomerRefundDetail.customer_refund,
            )
            .where(
                SalesOrderDetail.sales_order.in_(orders),
                CustomerRefund.completed.is_(True),
                CustomerRefund.cancelled.is_(False),
                CustomerRefund.date < nxt,
            )
        )
    ).all():
        refunded[order] = refunded.get(order, Decimal(0)) + _money(amount)
        if date >= start and (order not in last or date > last[order]):
            last[order] = date

    return {
        order: rules.OrderMoney(
            applied=applied.get(order, Decimal(0)),
            refunded=refunded.get(order, Decimal(0)),
            order_total=order_total.get(order, Decimal(0)),
            last_activity=last.get(order),
        )
        for order in orders
    }


async def _stored(db: AsyncSession, details: list[int]) -> list[rules.StoredLine]:
    if not details:
        return []
    rows = (
        await db.execute(
            _stored_rows()
            .where(CommissionPeriodLine.sales_order_detail.in_(details))
            .order_by(CommissionPeriod.year, CommissionPeriod.month)
        )
    ).scalars()
    return [
        rules.StoredLine(
            sales_order_detail=r.sales_order_detail,
            salesperson=r.salesperson,
            participation=r.participation,
            participation_name=r.participation_name,
            participation_rate=_decimal(r.participation_rate),
            label=r.label,
            label_name=r.label_name,
            label_rate=_decimal(r.label_rate),
            rate=_decimal(r.rate),
            customer_salesperson=r.customer_salesperson,
            movement=r.movement,
            customer_refund_detail=r.customer_refund_detail,
            scheme=r.scheme,
            commission=_money(r.commission),
            full_commission=_money(r.full_commission),
        )
        for r in rows
    ]


async def compute(
    db: AsyncSession, year: int, month: int, *, salesperson: int | None = None
) -> list[rules.ComputedLine]:
    """Every commission line the month owes, from the facts as they are now (research R1)."""
    start, nxt = month_bounds(year, month)
    chain = await chain_start(db)
    candidates = await _candidates(db, start, nxt, chain)
    details = [c.sales_order_detail for c in candidates]
    orders = sorted({c.sales_order for c in candidates})
    lines = rules.month_lines(
        month_start=start,
        next_month=nxt,
        chain_start=chain,
        candidates=candidates,
        money=await _order_money(db, orders, start, nxt),
        refunds=await _refunds(db, details, nxt),
        stored=await _stored(db, details),
        settings=await _settings(db),
    )
    if salesperson is not None:
        lines = [line for line in lines if line.salesperson == salesperson]
    return lines
