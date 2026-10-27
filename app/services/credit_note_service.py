"""Credit notes — value owed back to a customer, spendable against their other orders.

A credit note is deliberately **not** a second ledger. It is a view over the `customer_payment`
that backs it (FR-070, corrected by spec 034): the amount issued is that payment's amount, and what
is left is that payment's unapplied amount, change handed back included (spec 033 FR-005a). The
current system writes a note with `refunded = 0` and puts the amount on the payment
(`CustomerRefundsController.cs:353-378`), so reading the note's own columns finds nothing.

`refunded` and `cash_session` on the note mean it was paid back to the customer in cash: the current
system's `IsRefundedToCustomer` (`CreditNote.cs:53`). Such a note has nothing left to spend.

Redemption has no endpoint of its own — it is an ordinary payment application (FR-070a), which is
what makes it bounded, reversible and correctable through the payments editor without any of that
logic being written twice.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.models.sales import CreditNote, CustomerPayment, SalesOrderPayment
from app.services import totals

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def remaining_balance(unapplied: Decimal, *, refunded_to_customer: bool) -> Decimal:
    """What is still spendable: the backing payment's unapplied amount, floored at zero, and
    nothing at all once the note was paid back in cash (spec 034, FR-002)."""
    if refunded_to_customer or unapplied < 0:
        return Decimal(0)
    return unapplied


# ── Queries ───────────────────────────────────────────────────────────────────


def _unapplied():  # noqa: ANN202 — a SQL expression
    """The backing payment's unapplied amount, `unapplied_amount`'s arithmetic in SQL.

    A correlated sum per note, which the index on `sales_order_payment.customer_payment` answers,
    rather than an aggregate over every application in the table.
    """
    used = (
        select(
            func.coalesce(func.sum(SalesOrderPayment.amount + SalesOrderPayment.amount_change), 0)
        )
        .where(
            SalesOrderPayment.customer_payment == CreditNote.customer_payment,
            SalesOrderPayment.cancelled.is_(False),
        )
        .scalar_subquery()
    )
    return CustomerPayment.amount - used


def _notes_query() -> Select:
    """Each note with its payment's amount and facility, and its unapplied amount."""
    return select(
        CreditNote,
        CustomerPayment.amount,
        CustomerPayment.facility,
        _unapplied().label('unapplied'),
    ).join(CustomerPayment, CustomerPayment.customer_payment_id == CreditNote.customer_payment)


def _open(query: Select) -> Select:
    """The spendable pool (FR-004): the current system's `GetCreditNotes` — not paid back in cash,
    not the walk-in customer — with a balance above 0.01."""
    return query.where(
        _unapplied() > totals.SETTLED,
        CreditNote.cash_session.is_(None),
        CreditNote.customer != settings.default_customer_id,
    )


def _attach(note: CreditNote, amount: Decimal, facility: int, unapplied: Decimal) -> CreditNote:
    refunded_to_customer = note.cash_session is not None
    note.__dict__['amount'] = amount
    note.__dict__['facility'] = facility
    note.__dict__['refunded_to_customer'] = refunded_to_customer
    note.__dict__['remaining'] = remaining_balance(
        Decimal(unapplied), refunded_to_customer=refunded_to_customer
    )
    return note


async def get_credit_note(db: AsyncSession, credit_note_id: int) -> CreditNote | None:
    row = (
        await db.execute(_notes_query().where(CreditNote.credit_note_id == credit_note_id))
    ).one_or_none()
    return None if row is None else _attach(*row)


async def list_credit_notes(
    db: AsyncSession,
    *,
    current: CurrentUser,
    customer: int | None = None,
    open_only: bool = False,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CreditNote], int]:
    """Notes newest first. `open_only` filters in the query, so the page and `total` agree."""
    query = _notes_query()
    if customer is not None:
        query = query.where(CreditNote.customer == customer)
    if open_only:
        query = _open(query)

    total: int = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    page = query.order_by(CreditNote.credit_note_id.desc()).offset(skip).limit(limit)
    return [_attach(*row) for row in (await db.execute(page)).all()], total
