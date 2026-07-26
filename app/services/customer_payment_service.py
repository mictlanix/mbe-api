"""Customer payments and their application to orders.

A payment exists independently of any order: it is money received, and applying it is a separate,
reversible act. Applications are never deleted — reversing one flips `cancelled` and writes an
incidence entry naming who, when and why (FR-045, FR-045a), which is what SC-009 checks.

The paid flag is derived state kept in sync here: set when non-cancelled applications cover the
order's total, cleared when they no longer do (FR-044). Nothing else in the codebase writes it.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, SourceType
from app.models.core import Employee
from app.models.customer import Customer
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderPayment
from app.schemas.customer_payment import ApplicationCreate, CustomerPaymentCreate
from app.services import incidences, sales_order_service, totals

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def assert_order_payable(order: object) -> None:
    """Only a completed, uncancelled order can be paid (FR-042).

    This is what makes `paid` imply "completed and uncancelled" everywhere else, and why the
    refund path needs no cancellation check of its own.
    """
    if getattr(order, 'cancelled', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='A cancelled order cannot be paid'
        )
    if not getattr(order, 'completed', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Only a completed order can be paid; confirm it first',
        )


def assert_same_currency(payment_currency: CurrencyCode, order_currency: CurrencyCode) -> None:
    """FR-043 — refuse rather than silently convert."""
    if payment_currency != order_currency:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Payment currency does not match the order currency',
        )


def assert_within_unapplied(amount: Decimal, unapplied: Decimal) -> None:
    if amount > unapplied:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Amount {amount} exceeds the unapplied balance of {unapplied}',
        )


def assert_same_customer(payment_customer: int, order_customer: int) -> None:
    if payment_customer != order_customer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Payment belongs to a different customer than the order',
        )


def covers_total(applied: Decimal, total: Decimal) -> bool:
    """Whether the order is settled. Equality counts — a cent short is not paid."""
    return total > 0 and applied >= total


def is_barcode_or_id(term: str) -> bool:
    return term.isdigit()


# ── Payments ──────────────────────────────────────────────────────────────────


def _employee(current: CurrentUser) -> int:
    if current.employee_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Your user account is not linked to an employee and cannot record payments',
        )
    return current.employee_id


async def unapplied_amount(db: AsyncSession, customer_payment_id: int) -> Decimal:
    """The payment's amount less every non-cancelled application. Never stored."""
    payment = await db.get(CustomerPayment, customer_payment_id)
    if payment is None:
        return Decimal(0)
    applied = (
        await db.execute(
            select(func.sum(SalesOrderPayment.amount)).where(
                SalesOrderPayment.customer_payment == customer_payment_id,
                SalesOrderPayment.cancelled.is_(False),
            )
        )
    ).scalar_one_or_none() or Decimal(0)
    return payment.amount - applied


async def attach_unapplied(db: AsyncSession, payment: CustomerPayment) -> CustomerPayment:
    payment.__dict__['unapplied'] = await unapplied_amount(db, payment.customer_payment_id)
    return payment


async def attach_summary_unapplied(
    db: AsyncSession, payments: Sequence[CustomerPayment]
) -> None:
    """Unapplied amounts for a whole page in one query.

    `attach_unapplied` re-reads the payment and aggregates per call, which is an N+1 when looped
    over a page. This aggregates every payment on the page at once.
    """
    ids = [p.customer_payment_id for p in payments]
    if not ids:
        return

    rows = (
        await db.execute(
            select(SalesOrderPayment.customer_payment, func.sum(SalesOrderPayment.amount))
            .where(
                SalesOrderPayment.customer_payment.in_(ids),
                SalesOrderPayment.cancelled.is_(False),
            )
            .group_by(SalesOrderPayment.customer_payment)
        )
    ).all()
    applied = {pid: amount or Decimal(0) for pid, amount in rows}

    for payment in payments:
        payment.__dict__['unapplied'] = payment.amount - applied.get(
            payment.customer_payment_id, Decimal(0)
        )


async def create_payment(
    db: AsyncSession, data: CustomerPaymentCreate, *, current: CurrentUser
) -> CustomerPayment:
    employee = _employee(current)

    if await db.get(Customer, data.customer) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Customer not found')

    now = data.date or datetime.now()
    session_id = await _open_session_id(db, employee)

    payment = CustomerPayment(
        customer=data.customer,
        amount=data.amount,
        method=int(data.method),
        commission=None,
        payment_charge=data.payment_charge,
        date=now,
        cash_session=session_id,
        reference=data.reference,
        facility=current.facility_id,
        serial=0,
        creator=employee,
        updater=employee,
        verifier=None,
        creation_time=now,
        modification_time=now,
        currency=data.currency if data.currency is not None else settings.default_currency,
        payment_type=int(data.payment_type),
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)
    return await attach_unapplied(db, payment)


async def _open_session_id(db: AsyncSession, employee: int) -> int | None:
    """The cashier's open session, when there is one. A payment does not require one.

    Ordered and limited rather than asserting uniqueness: legacy data leaves some cashiers with
    several sessions open, and `scalar_one_or_none` raised on those, turning a payment into a 500.
    """
    from app.models.core import CashSession

    return (
        await db.execute(
            select(CashSession.cash_session_id)
            .where(CashSession.cashier == employee, CashSession.end.is_(None))
            .order_by(CashSession.start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_payment(db: AsyncSession, customer_payment_id: int) -> CustomerPayment | None:
    return await db.get(CustomerPayment, customer_payment_id)


async def list_payments(
    db: AsyncSession,
    *,
    current: CurrentUser,
    customer: int | None = None,
    cash_session: int | None = None,
    facility: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    method: int | None = None,
    verified: bool | None = None,
    unverified_only: bool = False,
    amount_min: Decimal | None = None,
    amount_max: Decimal | None = None,
    reference: str | None = None,
    cross_facility: bool = False,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CustomerPayment], int]:
    """Explicit filters only — no implicit scoping to the caller's cash session (FR-009a)."""
    base = select(CustomerPayment)
    count_q = select(func.count()).select_from(CustomerPayment)

    def both(clause):  # noqa: ANN001, ANN202
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    if not cross_facility:
        both(CustomerPayment.facility == (facility or current.facility_id))
    elif facility is not None:
        both(CustomerPayment.facility == facility)

    if customer is not None:
        both(CustomerPayment.customer == customer)
    if cash_session is not None:
        both(CustomerPayment.cash_session == cash_session)
    if date_from is not None:
        both(CustomerPayment.date >= date_from)
    if date_to is not None:
        both(CustomerPayment.date <= date_to)
    if method is not None:
        both(CustomerPayment.method == method)
    if amount_min is not None:
        both(CustomerPayment.amount >= amount_min)
    if amount_max is not None:
        both(CustomerPayment.amount <= amount_max)
    if reference:
        both(CustomerPayment.reference.ilike(f'%{reference}%'))
    if unverified_only or verified is False:
        both(CustomerPayment.verifier.is_(None))
    elif verified is True:
        both(CustomerPayment.verifier.is_not(None))

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(CustomerPayment.customer_payment_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    await attach_summary_unapplied(db, items)
    return items, total


# ── Applications ──────────────────────────────────────────────────────────────


async def list_applications(
    db: AsyncSession, customer_payment_id: int
) -> Sequence[SalesOrderPayment]:
    """Every application, cancelled ones included — the payments editor depends on it (FR-073)."""
    return (
        (
            await db.execute(
                select(SalesOrderPayment)
                .where(SalesOrderPayment.customer_payment == customer_payment_id)
                .order_by(SalesOrderPayment.sales_order_payment_id)
            )
        )
        .scalars()
        .all()
    )


async def apply_payment(
    db: AsyncSession,
    payment: CustomerPayment,
    data: ApplicationCreate,
    *,
    current: CurrentUser,
) -> SalesOrderPayment:
    employee = _employee(current)

    order = await db.get(SalesOrder, data.sales_order)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')

    assert_order_payable(order)
    assert_same_customer(payment.customer, order.customer)
    assert_same_currency(CurrencyCode(payment.currency), CurrencyCode(order.currency))
    assert_within_unapplied(data.amount, await unapplied_amount(db, payment.customer_payment_id))

    application = SalesOrderPayment(
        sales_order=order.sales_order_id,
        customer_payment=payment.customer_payment_id,
        amount=data.amount,
        amount_change=data.amount_change,
        applier=employee,
        date=datetime.now(),
        confirmed=True,
        cancelled=False,
    )
    db.add(application)
    await db.flush()
    await _sync_paid_flag(db, order)
    await db.commit()
    await db.refresh(application)
    return application


async def reverse_application(
    db: AsyncSession,
    payment: CustomerPayment,
    application: SalesOrderPayment,
    *,
    reason: str,
    current: CurrentUser,
) -> SalesOrderPayment:
    """Undo an application without destroying it (FR-045, FR-045a)."""
    employee = _employee(current)

    if application.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Application is already reversed'
        )

    # Raises 422 on a blank reason before anything is mutated
    incidences.record(
        db,
        source=SourceType.CUSTOMER_PAYMENT,
        instance_id=payment.customer_payment_id,
        updater=employee,
        reason=reason,
        context=(
            f'Reversed application {application.sales_order_payment_id} of '
            f'{application.amount} against sales order {application.sales_order}'
        ),
    )

    application.cancelled = True
    order = await db.get(SalesOrder, application.sales_order)
    if order is not None:
        await db.flush()
        await _sync_paid_flag(db, order)

    await db.commit()
    await db.refresh(application)
    return application


async def _sync_paid_flag(db: AsyncSession, order: SalesOrder) -> None:
    """Set or clear `paid` from the applications that currently stand (FR-044)."""
    await db.refresh(order)
    applied = await sales_order_service.applied_amount(db, order.sales_order_id)
    computed = await _order_total(db, order)
    order.paid = covers_total(applied, computed)


async def _order_total(db: AsyncSession, order: SalesOrder) -> Decimal:
    from app.models.sales import SalesOrderDetail

    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == order.sales_order_id
                )
            )
        )
        .scalars()
        .all()
    )
    return totals.document_totals(
        [
            totals.Line(
                quantity=line.quantity,
                price=line.price,
                discount_rate=line.discount_rate,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
            for line in lines
        ]
    ).total


async def get_application(
    db: AsyncSession, payment: CustomerPayment, application_id: int
) -> SalesOrderPayment | None:
    application = await db.get(SalesOrderPayment, application_id)
    if application is None or application.customer_payment != payment.customer_payment_id:
        return None
    return application


# ── Verification ──────────────────────────────────────────────────────────────


async def verify_payment(
    db: AsyncSession, payment: CustomerPayment, *, current: CurrentUser
) -> CustomerPayment:
    employee = _employee(current)
    if payment.verifier is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Payment is already verified'
        )
    payment.verifier = employee
    payment.updater = employee
    payment.modification_time = datetime.now()
    await db.commit()
    await db.refresh(payment)
    return await attach_unapplied(db, payment)


async def reject_payment(
    db: AsyncSession, payment: CustomerPayment, *, reason: str, current: CurrentUser
) -> CustomerPayment:
    """Flag a payment for investigation, leaving the reason on the record (FR-072)."""
    employee = _employee(current)
    incidences.record(
        db,
        source=SourceType.CUSTOMER_PAYMENT,
        instance_id=payment.customer_payment_id,
        updater=employee,
        reason=reason,
        context=f'Rejected payment {payment.customer_payment_id} of {payment.amount}',
    )
    await db.commit()
    await db.refresh(payment)
    return await attach_unapplied(db, payment)


# ── Outstanding orders ────────────────────────────────────────────────────────


async def search_outstanding(
    db: AsyncSession,
    *,
    current: CurrentUser,
    search: str | None = None,
    customer: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """Unpaid confirmed orders with their balances (FR-046).

    A numeric term matches the order id or its folio; anything else matches the customer's name,
    either salesperson's nickname, or the order's customer-name override.
    """
    base = (
        select(SalesOrder)
        .where(
            SalesOrder.completed.is_(True),
            SalesOrder.cancelled.is_(False),
            SalesOrder.paid.is_(False),
            SalesOrder.facility == current.facility_id,
        )
    )
    count_q = (
        select(func.count())
        .select_from(SalesOrder)
        .where(
            SalesOrder.completed.is_(True),
            SalesOrder.cancelled.is_(False),
            SalesOrder.paid.is_(False),
            SalesOrder.facility == current.facility_id,
        )
    )

    if customer is not None:
        base = base.where(SalesOrder.customer == customer)
        count_q = count_q.where(SalesOrder.customer == customer)

    if search:
        if is_barcode_or_id(search):
            clause = or_(
                SalesOrder.sales_order_id == int(search), SalesOrder.serial == int(search)
            )
        else:
            like = f'%{search}%'
            customer_ids = select(Customer.customer_id).where(Customer.name.ilike(like))
            employee_ids = select(Employee.employee_id).where(Employee.nickname.ilike(like))
            clause = or_(
                SalesOrder.customer.in_(customer_ids),
                SalesOrder.salesperson.in_(employee_ids),
                SalesOrder.customer_name.ilike(like),
            )
        base = base.where(clause)
        count_q = count_q.where(clause)

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(SalesOrder.sales_order_id.desc()).offset(skip).limit(limit)
    orders = (await db.execute(page)).scalars().all()

    rows: list[dict] = []
    for order in orders:
        order_total = await _order_total(db, order)
        applied = await sales_order_service.applied_amount(db, order.sales_order_id)
        rows.append(
            {
                'sales_order_id': order.sales_order_id,
                'serial': order.serial,
                'customer': order.customer,
                'customer_name': order.customer_name,
                'date': order.date,
                'due_date': order.due_date,
                'currency': order.currency,
                'total': order_total,
                'balance': totals.remaining(order_total, [applied]),
            }
        )
    return rows, total
