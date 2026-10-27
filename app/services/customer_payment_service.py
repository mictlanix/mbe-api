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
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
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


def settle(order: object, balance: Decimal) -> None:
    """Flag an unpaid order paid once its balance is 0.01 or less (spec 033, FR-011).

    Called after anything that lowers a balance: an application, a refund's completion, a credit
    note's confirmation. It never clears `paid`, and it leaves an order already flagged paid alone:
    a refund on a legacy paid order settles nothing new (FR-065a), so it mustn't gain a zero time.

    The moment the balance reached zero dates the order's commission month (spec 025 FR-008a), as
    the current system's `PaymentsController` stamps it in every payment path. Never cleared: a
    reversal does not move the month an order was first settled in. `0001-01-01` is a legacy
    sentinel for "unset".
    """
    if order.paid or balance > totals.SETTLED:
        return
    order.paid = True
    if order.balance_zeroed_time is None or order.balance_zeroed_time.year < 1900:
        order.balance_zeroed_time = local_now()


def reopen(order: object, before: Decimal, after: Decimal) -> None:
    """Clear `paid` when an event takes the balance from 0.01 or less to above it (spec 033, FR-011,
    clarification Q2).

    Called after anything that raises a balance: an application's reversal, a credit note going back
    to draft. A paid order that already owed before the event stays paid, so a legacy paid order
    never reopens. The zero time stays: it dates the first settlement.
    """
    if before <= totals.SETTLED < after:
        order.paid = False


def is_barcode_or_id(term: str) -> bool:
    return term.isdigit()


# ── Payments ──────────────────────────────────────────────────────────────────


async def unapplied_amount(db: AsyncSession, customer_payment_id: int) -> Decimal:
    """The payment's amount less every non-cancelled application and the change it handed back.
    Never stored.

    Change is money the payment returned to the customer, so it can't be applied again. The
    payment's ticket and the current system's `Balance` subtract it too, and so does the customer's
    credit (spec 033, FR-005a): payment 300789 in mbe_dev is 600, 530 applied, 70 handed back, 0.
    """
    payment = await db.get(CustomerPayment, customer_payment_id)
    if payment is None:
        return Decimal(0)
    applied = (
        await db.execute(
            select(func.sum(SalesOrderPayment.amount + SalesOrderPayment.amount_change)).where(
                SalesOrderPayment.customer_payment == customer_payment_id,
                SalesOrderPayment.cancelled.is_(False),
            )
        )
    ).scalar_one_or_none() or Decimal(0)
    return payment.amount - applied


async def attach_unapplied(db: AsyncSession, payment: CustomerPayment) -> CustomerPayment:
    payment.__dict__['unapplied'] = await unapplied_amount(db, payment.customer_payment_id)
    return payment


async def attach_summary_unapplied(db: AsyncSession, payments: Sequence[CustomerPayment]) -> None:
    """Unapplied amounts for a whole page in one query.

    `attach_unapplied` re-reads the payment and aggregates per call, which is an N+1 when looped
    over a page. This aggregates every payment on the page at once.
    """
    ids = [p.customer_payment_id for p in payments]
    if not ids:
        return

    rows = (
        await db.execute(
            select(
                SalesOrderPayment.customer_payment,
                func.sum(SalesOrderPayment.amount + SalesOrderPayment.amount_change),
            )
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


async def attach_customer_names(db: AsyncSession, payments: Sequence[CustomerPayment]) -> None:
    """The customers' own names for a page of payments, in one query (#270)."""
    names = await _customer_names(db, payments)
    for payment in payments:
        # `__dict__`, as in `attach_summary_unapplied`: there is no such column on the model.
        payment.__dict__['customer_display_name'] = names.get(payment.customer)


async def create_payment(
    db: AsyncSession, data: CustomerPaymentCreate, *, current: CurrentUser
) -> CustomerPayment:
    employee = current.employee_id

    if await db.get(Customer, data.customer) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Customer not found')

    now = data.date or local_now()
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
    search: str | None = None,
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
    if search:
        named = select(Customer.customer_id).where(Customer.name.ilike(f'%{search}%'))
        both(CustomerPayment.customer.in_(named))
    if unverified_only or verified is False:
        both(CustomerPayment.verifier.is_(None))
    elif verified is True:
        both(CustomerPayment.verifier.is_not(None))

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(CustomerPayment.customer_payment_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    await attach_summary_unapplied(db, items)
    await attach_customer_names(db, items)
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


async def list_order_applications(db: AsyncSession, sales_order_id: int) -> list[dict]:
    """The payments standing against one order — the reverse of `list_applications` (#134).

    Cancelled applications are included for the same reason they are there in the other
    direction: a reversal stays visible. Each row carries its payment's own fields so a caller
    rendering the list does not need one follow-up request per application.
    """
    rows = (
        await db.execute(
            select(SalesOrderPayment, CustomerPayment)
            .join(
                CustomerPayment,
                CustomerPayment.customer_payment_id == SalesOrderPayment.customer_payment,
            )
            .where(SalesOrderPayment.sales_order == sales_order_id)
            .order_by(SalesOrderPayment.sales_order_payment_id)
        )
    ).all()
    return [
        {
            'sales_order_payment_id': application.sales_order_payment_id,
            'sales_order': application.sales_order,
            'customer_payment': application.customer_payment,
            'amount': application.amount,
            'amount_change': application.amount_change,
            'applier': application.applier,
            'date': application.date,
            'cancelled': application.cancelled,
            'method': payment.method,
            'currency': payment.currency,
            'reference': payment.reference,
            'payment_date': payment.date,
            'payment_type': payment.payment_type,
            'verifier': payment.verifier,
        }
        for application, payment in rows
    ]


async def apply_payment(
    db: AsyncSession,
    payment: CustomerPayment,
    data: ApplicationCreate,
    *,
    current: CurrentUser,
) -> SalesOrderPayment:
    employee = current.employee_id

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
        date=local_now(),
        confirmed=True,
        cancelled=False,
    )
    db.add(application)
    await db.flush()
    settle(order, await _balance(db, order))
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
    employee = current.employee_id

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

    order = await db.get(SalesOrder, application.sales_order)
    before = await _balance(db, order) if order is not None else None
    application.cancelled = True
    if order is not None:
        await db.flush()
        reopen(order, before, await _balance(db, order))

    await db.commit()
    await db.refresh(application)
    return application


async def _balance(db: AsyncSession, order: SalesOrder) -> Decimal:
    """The order's balance by the one rule (spec 033, FR-001)."""
    return (await sales_order_service.order_balances(db, [order]))[order.sales_order_id].balance


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
    employee = current.employee_id
    if payment.verifier is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Payment is already verified'
        )
    payment.verifier = employee
    payment.updater = employee
    payment.modification_time = local_now()
    await db.commit()
    await db.refresh(payment)
    return await attach_unapplied(db, payment)


async def reject_payment(
    db: AsyncSession, payment: CustomerPayment, *, reason: str, current: CurrentUser
) -> CustomerPayment:
    """Flag a payment for investigation, leaving the reason on the record (FR-072)."""
    employee = current.employee_id
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


async def _customer_names(
    db: AsyncSession, orders: Sequence[SalesOrder | CustomerPayment]
) -> dict[int, str]:
    """The customers' own names for a page of orders, in one query keyed on the distinct ids (#174).

    One query, and deliberately so: resolving the name per row would turn a page of twenty into
    twenty round trips. A page of walk-in sales is one customer repeated and fetches a single row.

    Mirrors `sales_order_service.attach_customer_names`, which does the same job for
    `SalesOrderSummary`; it returns a mapping rather than writing onto the instances because these
    rows are built as dicts, not projected from the model.
    """
    ids = {order.customer for order in orders}
    if not ids:
        return {}
    return dict(
        (
            await db.execute(
                select(Customer.customer_id, Customer.name).where(Customer.customer_id.in_(ids))
            )
        ).all()
    )


async def search_outstanding(
    db: AsyncSession,
    *,
    current: CurrentUser,
    search: str | None = None,
    customer: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[dict], int]:
    """Confirmed orders that still owe, with their balances (FR-046; spec 033, FR-010).

    A numeric term matches the order id or its folio; anything else matches the customer's name,
    either salesperson's nickname, or the order's customer-name override.

    Owing is the one balance rule's figure above 0.01, which no column holds, so the page is cut
    after the balances are computed: every unpaid order of the facility that matches, at most about
    a thousand in mbe_dev. Every payment term stays, since cash sales are paid through this list.
    """
    query = select(SalesOrder).where(
        SalesOrder.completed.is_(True),
        SalesOrder.cancelled.is_(False),
        SalesOrder.paid.is_(False),
        SalesOrder.facility == current.facility_id,
    )

    if customer is not None:
        query = query.where(SalesOrder.customer == customer)

    if search:
        if is_barcode_or_id(search):
            clause = or_(SalesOrder.sales_order_id == int(search), SalesOrder.serial == int(search))
        else:
            like = f'%{search}%'
            customer_ids = select(Customer.customer_id).where(Customer.name.ilike(like))
            employee_ids = select(Employee.employee_id).where(Employee.nickname.ilike(like))
            clause = or_(
                SalesOrder.customer.in_(customer_ids),
                SalesOrder.salesperson.in_(employee_ids),
                SalesOrder.customer_name.ilike(like),
            )
        query = query.where(clause)

    candidates = list(
        (await db.execute(query.order_by(SalesOrder.sales_order_id.desc()))).scalars().all()
    )
    balances = await sales_order_service.order_balances(db, candidates)
    owing = [
        order for order in candidates if balances[order.sales_order_id].balance > totals.SETTLED
    ]
    page = owing[skip : skip + limit]
    display_names = await _customer_names(db, page)

    rows: list[dict] = []
    for order in page:
        balance = balances[order.sales_order_id]
        rows.append(
            {
                'sales_order_id': order.sales_order_id,
                'serial': order.serial,
                'customer': order.customer,
                'customer_name': order.customer_name,
                'customer_display_name': display_names.get(order.customer),
                'date': order.date,
                'due_date': order.due_date,
                'currency': order.currency,
                'total': balance.total,
                'balance': balance.balance,
            }
        )
    return rows, len(owing)


async def payments_with_credit(
    db: AsyncSession, *, customers: Sequence[int], facility: int | None = None
) -> list[CustomerPayment]:
    """The customers' payments with more than 0.01 left to apply, oldest first, each carrying
    `unapplied` (spec 033, FR-005a, FR-020).

    Unapplied is computed in the query, the same arithmetic as `unapplied_amount`, so the walk-in
    customer's tens of thousands of fully used cash payments never leave the database.
    """
    if not customers:
        return []
    used = func.coalesce(func.sum(SalesOrderPayment.amount + SalesOrderPayment.amount_change), 0)
    unapplied = CustomerPayment.amount - used
    query = (
        select(CustomerPayment, unapplied)
        .outerjoin(
            SalesOrderPayment,
            and_(
                SalesOrderPayment.customer_payment == CustomerPayment.customer_payment_id,
                SalesOrderPayment.cancelled.is_(False),
            ),
        )
        .where(CustomerPayment.customer.in_(list(customers)))
        .group_by(CustomerPayment.customer_payment_id)
        .having(unapplied > totals.SETTLED)
        .order_by(CustomerPayment.date, CustomerPayment.customer_payment_id)
    )
    if facility is not None:
        query = query.where(CustomerPayment.facility == facility)
    payments = []
    for payment, left in (await db.execute(query)).all():
        payment.__dict__['unapplied'] = Decimal(left)
        payments.append(payment)
    return payments
