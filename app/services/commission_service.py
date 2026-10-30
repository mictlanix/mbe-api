"""Commissions: facts, computation, periods and settings (spec 025).

The money rules live in `commission_rules`, which is pure. This module gathers the facts a month
needs, hands them to `commission_rules.month_lines`, and stores what a close returns. The facts are
SQLAlchemy Core queries over the mapped columns, not raw SQL, so they type the same on MariaDB and
on the SQLite the integration tests run on.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, case, delete, exists, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.enums import (
    CommissionMovement,
    CommissionPeriodStatus,
    CommissionScheme,
    SourceType,
)
from app.models.commission import (
    Commission,
    CommissionAgent,
    CommissionParticipation,
    CommissionPeriod,
    CommissionPeriodLine,
    CommissionProduct,
    CommissionSalesperson,
)
from app.models.core import Employee
from app.models.customer import Customer
from app.models.product import Product
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.schemas.commission import (
    CommissionAgentCreate,
    CommissionAgentRateCreate,
    CommissionAgentRateUpdate,
    CommissionAgentUpdate,
    CommissionLabelCreate,
    CommissionLabelUpdate,
    CommissionParticipationBody,
)
from app.services import commission_rules as rules
from app.services import incidences
from app.services.references import assert_not_referenced

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


async def _refunds(db: AsyncSession, details: list[int], nxt: datetime) -> list[rules.RefundLine]:
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


# ── Periods ───────────────────────────────────────────────────────────────────

_STATUS_NAMES = {
    CommissionPeriodStatus.OPEN: 'open',
    CommissionPeriodStatus.CLOSED: 'closed',
    CommissionPeriodStatus.PAID: 'paid',
}


@dataclass(frozen=True)
class Total:
    salesperson: int
    label: int | None
    label_name: str | None
    lines: int
    commission: Decimal


@dataclass(frozen=True)
class PeriodView:
    year: int
    month: int
    status: str
    final: bool
    closed_by: int | None
    closed_at: datetime | None
    reopened_by: int | None
    reopened_at: datetime | None
    paid_by: int | None
    paid_at: datetime | None
    #: Stored `CommissionPeriodLine` rows, or computed `commission_rules.ComputedLine`s; both carry
    #: the same attributes.
    lines: Sequence[object]
    totals: list[Total]


def _conflict(code: ErrorCode, detail: str, year: int, month: int) -> DomainError:
    return DomainError(code, detail, {'year': year, 'month': month})


def _totals(lines: Sequence[object]) -> list[Total]:
    grouped: dict[tuple, list] = {}
    for line in lines:
        grouped.setdefault((line.salesperson, line.label, line.label_name), []).append(line)
    return [
        Total(
            salesperson=salesperson,
            label=label,
            label_name=label_name,
            lines=len(rows),
            commission=sum((_money(r.commission) for r in rows), Decimal(0)),
        )
        for (salesperson, label, label_name), rows in sorted(
            grouped.items(), key=lambda item: (item[0][0], item[0][1] is None, item[0][1] or 0)
        )
    ]


async def _period(db: AsyncSession, year: int, month: int, *, lock: bool = False):  # noqa: ANN202
    query = select(CommissionPeriod).where(
        CommissionPeriod.year == year, CommissionPeriod.month == month
    )
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


async def _latest_closed(db: AsyncSession, *, lock: bool = False):  # noqa: ANN202
    query = (
        select(CommissionPeriod)
        .where(CommissionPeriod.status.in_(_CLOSED))
        .order_by(CommissionPeriod.year.desc(), CommissionPeriod.month.desc())
        .limit(1)
    )
    if lock:
        query = query.with_for_update()
    return (await db.execute(query)).scalar_one_or_none()


def _previous(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


async def close(
    db: AsyncSession, year: int, month: int, *, current: CurrentUser
) -> CommissionPeriod:
    """Freeze a month's lines (FR-007). Months close in sequence (FR-010a)."""
    _, nxt = month_bounds(year, month)
    if local_now() < nxt:
        raise _conflict(ErrorCode.COMMISSION_PERIOD_NOT_ENDED, 'Period has not ended', year, month)
    period = await _period(db, year, month, lock=True)
    if period is not None and period.status == CommissionPeriodStatus.PAID:
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_ALREADY_PAID, 'Period is already paid', year, month
        )
    if period is not None and period.status == CommissionPeriodStatus.CLOSED:
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_ALREADY_CLOSED, 'Period is already closed', year, month
        )
    latest = await _latest_closed(db, lock=True)
    if latest is not None and (latest.year, latest.month) != _previous(year, month):
        if (latest.year, latest.month) > (year, month):
            raise _conflict(
                ErrorCode.COMMISSION_PERIOD_LATER_CLOSED, 'A later period is closed', year, month
            )
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_PREVIOUS_NOT_CLOSED,
            'The previous period is not closed',
            year,
            month,
        )

    lines = await compute(db, year, month)
    now = local_now()
    if period is None:
        period = CommissionPeriod(year=year, month=month)
        db.add(period)
    period.status = int(CommissionPeriodStatus.CLOSED)
    period.closed_by = current.employee_id
    period.closed_at = now
    await db.flush()
    db.add_all(
        CommissionPeriodLine(commission_period=period.commission_period_id, **vars(line))
        for line in lines
    )
    await db.commit()
    await db.refresh(period)
    return period


async def reopen(
    db: AsyncSession, year: int, month: int, *, current: CurrentUser
) -> CommissionPeriod:
    """Discard a closed, unpaid month's lines; only the latest closed month (FR-010, FR-010a)."""
    period = await _period(db, year, month, lock=True)
    if period is not None and period.status == CommissionPeriodStatus.PAID:
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_PAID_CANNOT_REOPEN,
            'A paid period cannot be reopened',
            year,
            month,
        )
    if period is None or period.status != CommissionPeriodStatus.CLOSED:
        raise _conflict(ErrorCode.COMMISSION_PERIOD_NOT_CLOSED, 'Period is not closed', year, month)
    latest = await _latest_closed(db, lock=True)
    if latest is not None and (latest.year, latest.month) != (year, month):
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_LATER_CLOSED, 'A later period is closed', year, month
        )

    await db.execute(
        delete(CommissionPeriodLine).where(
            CommissionPeriodLine.commission_period == period.commission_period_id
        )
    )
    period.status = int(CommissionPeriodStatus.OPEN)
    period.reopened_by = current.employee_id
    period.reopened_at = local_now()
    await db.commit()
    await db.refresh(period)
    return period


async def pay(db: AsyncSession, year: int, month: int, *, current: CurrentUser) -> CommissionPeriod:
    """Mark a closed month paid; a paid month is final (FR-011)."""
    period = await _period(db, year, month, lock=True)
    if period is not None and period.status == CommissionPeriodStatus.PAID:
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_ALREADY_PAID, 'Period is already paid', year, month
        )
    if period is None or period.status != CommissionPeriodStatus.CLOSED:
        raise _conflict(
            ErrorCode.COMMISSION_PERIOD_NOT_CLOSED, 'Close the period first', year, month
        )
    period.status = int(CommissionPeriodStatus.PAID)
    period.paid_by = current.employee_id
    period.paid_at = local_now()
    await db.commit()
    await db.refresh(period)
    return period


async def list_periods(db: AsyncSession) -> list[tuple[CommissionPeriod, Decimal]]:
    """Months that have a row, newest first, each with the sum of its stored lines."""
    total = (
        select(func.coalesce(func.sum(CommissionPeriodLine.commission), 0))
        .where(CommissionPeriodLine.commission_period == CommissionPeriod.commission_period_id)
        .scalar_subquery()
    )
    rows = await db.execute(
        select(CommissionPeriod, total).order_by(
            CommissionPeriod.year.desc(), CommissionPeriod.month.desc()
        )
    )
    return [(period, _money(amount)) for period, amount in rows.all()]


async def read_period(
    db: AsyncSession, year: int, month: int, *, salesperson: int | None = None
) -> PeriodView:
    """A month as the report reads it: stored once closed, computed live while open (FR-012)."""
    period = await _period(db, year, month)
    state = CommissionPeriodStatus(period.status) if period else CommissionPeriodStatus.OPEN
    if state == CommissionPeriodStatus.OPEN:
        lines: Sequence[object] = await compute(db, year, month, salesperson=salesperson)
    else:
        query = select(CommissionPeriodLine).where(
            CommissionPeriodLine.commission_period == period.commission_period_id
        )
        if salesperson is not None:
            query = query.where(CommissionPeriodLine.salesperson == salesperson)
        lines = (await db.execute(query)).scalars().all()
    lines = sorted(
        lines,
        key=lambda x: (x.salesperson, x.paid_date, x.sales_order, x.sales_order_detail, x.movement),
    )
    return PeriodView(
        year=year,
        month=month,
        status=_STATUS_NAMES[state],
        final=state != CommissionPeriodStatus.OPEN,
        closed_by=period.closed_by if period else None,
        closed_at=period.closed_at if period else None,
        reopened_by=period.reopened_by if period else None,
        reopened_at=period.reopened_at if period else None,
        paid_by=period.paid_by if period else None,
        paid_at=period.paid_at if period else None,
        lines=lines,
        totals=_totals(lines),
    )


# ── Settings ──────────────────────────────────────────────────────────────────


def _audit(db: AsyncSession, current: CurrentUser, instance_id: int, reason: str, context: str):
    incidences.record(
        db,
        source=SourceType.COMMISSION,
        instance_id=instance_id,
        updater=current.employee_id,
        reason=reason,
        context=context,
    )


def _not_found(resource: type, id: int, what: str) -> DomainError:
    return not_found(resource, id, detail=f'{what} not found')


async def _get_or_404[T](db: AsyncSession, model: type[T], pk: int, what: str) -> T:
    row = await db.get(model, pk)
    if row is None:
        raise _not_found(model, pk, what)
    return row


async def list_labels(db: AsyncSession) -> Sequence[Commission]:
    query = select(Commission).order_by(Commission.name, Commission.commission_id)
    return (await db.execute(query)).scalars().all()


async def create_label(
    db: AsyncSession, data: CommissionLabelCreate, *, current: CurrentUser
) -> Commission:
    label = Commission(
        name=data.name, commission_rate=data.commission_rate, comment=data.comment or '0'
    )
    db.add(label)
    await db.flush()
    _audit(
        db,
        current,
        label.commission_id,
        'Commission label created',
        f'{label.name} rate {data.commission_rate}',
    )
    await db.commit()
    return label


async def update_label(
    db: AsyncSession, commission_id: int, data: CommissionLabelUpdate, *, current: CurrentUser
) -> Commission:
    label = await _get_or_404(db, Commission, commission_id, 'Commission label')
    changes: list[str] = []
    if data.name is not None and data.name != label.name:
        changes.append(f'name {label.name} → {data.name}')
        label.name = data.name
    if data.commission_rate is not None and data.commission_rate != label.commission_rate:
        changes.append(f'{label.name} rate {label.commission_rate:g} → {data.commission_rate:g}')
        label.commission_rate = data.commission_rate
    if data.comment is not None and data.comment != label.comment:
        changes.append(f'comment {label.comment} → {data.comment}')
        label.comment = data.comment
    _audit(
        db, current, commission_id, 'Commission label updated', '; '.join(changes) or 'no changes'
    )
    await db.commit()
    return label


async def delete_label(db: AsyncSession, commission_id: int, *, current: CurrentUser) -> None:
    label = await _get_or_404(db, Commission, commission_id, 'Commission label')
    await assert_not_referenced(db, label)
    _audit(db, current, commission_id, 'Commission label deleted', label.name)
    await db.delete(label)
    await db.commit()


async def list_participations(db: AsyncSession) -> Sequence[CommissionParticipation]:
    query = select(CommissionParticipation).order_by(
        CommissionParticipation.name, CommissionParticipation.commission_participation_id
    )
    return (await db.execute(query)).scalars().all()


async def create_participation(
    db: AsyncSession, data: CommissionParticipationBody, *, current: CurrentUser
) -> CommissionParticipation:
    row = CommissionParticipation(name=data.name)
    db.add(row)
    await db.flush()
    _audit(
        db, current, row.commission_participation_id, 'Commission participation created', data.name
    )
    await db.commit()
    return row


async def update_participation(
    db: AsyncSession,
    participation_id: int,
    data: CommissionParticipationBody,
    *,
    current: CurrentUser,
) -> CommissionParticipation:
    row = await _get_or_404(
        db, CommissionParticipation, participation_id, 'Commission participation'
    )
    _audit(
        db,
        current,
        participation_id,
        'Commission participation updated',
        f'{row.name} → {data.name}',
    )
    row.name = data.name
    await db.commit()
    return row


async def delete_participation(
    db: AsyncSession, participation_id: int, *, current: CurrentUser
) -> None:
    row = await _get_or_404(
        db, CommissionParticipation, participation_id, 'Commission participation'
    )
    await assert_not_referenced(db, row)
    _audit(db, current, participation_id, 'Commission participation deleted', row.name)
    await db.delete(row)
    await db.commit()


def _agent_query():  # noqa: ANN202
    name = (Employee.first_name + ' ' + Employee.last_name).label('name')
    return (
        select(CommissionAgent.employee, name, CommissionAgent.scheme)
        .join(Employee, Employee.employee_id == CommissionAgent.employee)
        .order_by(CommissionAgent.employee)
    )


async def list_agents(db: AsyncSession):  # noqa: ANN201
    return (await db.execute(_agent_query())).all()


async def _read_agent(db: AsyncSession, employee: int):  # noqa: ANN202
    query = _agent_query().where(CommissionAgent.employee == employee)
    return (await db.execute(query)).one()


async def create_agent(  # noqa: ANN201
    db: AsyncSession, data: CommissionAgentCreate, *, current: CurrentUser
):
    if await db.get(Employee, data.employee) is None:
        raise _not_found(Employee, data.employee, 'Employee')
    if await db.get(CommissionAgent, data.employee) is not None:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'Employee is already an agent',
            {'resource': 'commission_agent', 'field': 'employee'},
        )
    db.add(CommissionAgent(employee=data.employee, scheme=int(data.scheme)))
    await db.flush()
    _audit(
        db,
        current,
        data.employee,
        'Commission agent created',
        f'scheme {int(data.scheme)}',
    )
    await db.commit()
    return await _read_agent(db, data.employee)


async def update_agent(  # noqa: ANN201
    db: AsyncSession, employee: int, data: CommissionAgentUpdate, *, current: CurrentUser
):
    agent = await _get_or_404(db, CommissionAgent, employee, 'Commission agent')
    _audit(
        db,
        current,
        employee,
        'Commission agent scheme changed',
        f'scheme {agent.scheme} → {int(data.scheme)}',
    )
    agent.scheme = int(data.scheme)
    await db.commit()
    return await _read_agent(db, employee)


async def delete_agent(db: AsyncSession, employee: int, *, current: CurrentUser) -> None:
    agent = await _get_or_404(db, CommissionAgent, employee, 'Commission agent')
    await assert_not_referenced(db, agent)
    _audit(db, current, employee, 'Commission agent deleted', f'scheme {agent.scheme}')
    await db.delete(agent)
    await db.commit()


async def list_agent_rates(
    db: AsyncSession, *, salesperson: int | None = None, commission: int | None = None
) -> Sequence[CommissionSalesperson]:
    query = select(CommissionSalesperson).order_by(CommissionSalesperson.commission_salesperson_id)
    if salesperson is not None:
        query = query.where(CommissionSalesperson.salesperson == salesperson)
    if commission is not None:
        query = query.where(CommissionSalesperson.commission == commission)
    return (await db.execute(query)).scalars().all()


async def create_agent_rate(
    db: AsyncSession, data: CommissionAgentRateCreate, *, current: CurrentUser
) -> CommissionSalesperson:
    if await db.get(CommissionAgent, data.salesperson) is None:
        raise DomainError(
            ErrorCode.COMMISSION_AGENT_SALESPERSON_NOT_AGENT,
            'Salesperson is not an agent',
            {'salesperson_id': data.salesperson},
        )
    await _get_or_404(db, Commission, data.commission, 'Commission label')
    await _get_or_404(
        db, CommissionParticipation, data.commission_participation, 'Commission participation'
    )
    duplicate = select(CommissionSalesperson.commission_salesperson_id).where(
        CommissionSalesperson.salesperson == data.salesperson,
        CommissionSalesperson.commission == data.commission,
        CommissionSalesperson.commission_participation == data.commission_participation,
    )
    if (await db.execute(duplicate)).first() is not None:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'Rate already exists for this agent, label and participation',
            {
                'resource': 'commission_salesperson',
                'field': 'salesperson,commission,commission_participation',
            },
        )
    row = CommissionSalesperson(
        salesperson=data.salesperson,
        commission=data.commission,
        commission_participation=data.commission_participation,
        participation_rate=data.participation_rate,
    )
    db.add(row)
    await db.flush()
    _audit(
        db,
        current,
        row.commission_salesperson_id,
        'Commission agent rate created',
        f'agent {data.salesperson} label {data.commission} '
        f'participation {data.commission_participation} share {data.participation_rate:g}',
    )
    await db.commit()
    return row


async def update_agent_rate(
    db: AsyncSession, rate_id: int, data: CommissionAgentRateUpdate, *, current: CurrentUser
) -> CommissionSalesperson:
    row = await _get_or_404(db, CommissionSalesperson, rate_id, 'Commission agent rate')
    _audit(
        db,
        current,
        rate_id,
        'Commission agent rate updated',
        f'share {row.participation_rate:g} → {data.participation_rate:g}',
    )
    row.participation_rate = data.participation_rate
    await db.commit()
    return row


async def delete_agent_rate(db: AsyncSession, rate_id: int, *, current: CurrentUser) -> None:
    row = await _get_or_404(db, CommissionSalesperson, rate_id, 'Commission agent rate')
    await assert_not_referenced(db, row)
    _audit(
        db,
        current,
        rate_id,
        'Commission agent rate deleted',
        f'agent {row.salesperson} label {row.commission} share {row.participation_rate:g}',
    )
    await db.delete(row)
    await db.commit()


async def list_product_labels(
    db: AsyncSession,
    *,
    commission: int | None = None,
    product: int | None = None,
    skip: int = 0,
    limit: int = 20,
):  # noqa: ANN201
    filters = []
    if commission is not None:
        filters.append(CommissionProduct.commission == commission)
    if product is not None:
        filters.append(CommissionProduct.product == product)
    total: int = (
        await db.execute(select(func.count()).select_from(CommissionProduct).where(*filters))
    ).scalar_one()
    query = (
        select(
            CommissionProduct.product,
            Product.code.label('product_code'),
            Product.name.label('product_name'),
            CommissionProduct.commission,
        )
        .join(Product, Product.product_id == CommissionProduct.product)
        .where(*filters)
        .order_by(CommissionProduct.product)
        .offset(skip)
        .limit(limit)
    )
    return (await db.execute(query)).all(), total


async def _read_product_label(db: AsyncSession, product: int):  # noqa: ANN202
    query = (
        select(
            CommissionProduct.product,
            Product.code.label('product_code'),
            Product.name.label('product_name'),
            CommissionProduct.commission,
        )
        .join(Product, Product.product_id == CommissionProduct.product)
        .where(CommissionProduct.product == product)
    )
    return (await db.execute(query)).one()


async def set_product_label(  # noqa: ANN201
    db: AsyncSession, product: int, commission: int, *, current: CurrentUser
):
    await _get_or_404(db, Product, product, 'Product')
    await _get_or_404(db, Commission, commission, 'Commission label')
    existing = (
        (await db.execute(select(CommissionProduct).where(CommissionProduct.product == product)))
        .scalars()
        .first()
    )
    if existing is None:
        db.add(CommissionProduct(product=product, commission=commission))
        reason, context = 'Product commission label set', f'label {commission}'
    else:
        reason = 'Product commission label changed'
        context = f'label {existing.commission} → {commission}'
        existing.commission = commission
    _audit(db, current, product, reason, context)
    await db.commit()
    return await _read_product_label(db, product)


async def delete_product_label(db: AsyncSession, product: int, *, current: CurrentUser) -> None:
    row = (
        (await db.execute(select(CommissionProduct).where(CommissionProduct.product == product)))
        .scalars()
        .first()
    )
    if row is None:
        raise _not_found(CommissionProduct, product, 'Product commission label')
    await assert_not_referenced(db, row)
    _audit(db, current, product, 'Product commission label removed', f'label {row.commission}')
    await db.delete(row)
    await db.commit()
