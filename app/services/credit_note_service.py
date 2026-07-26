"""Credit notes — value owed back to a customer, spendable against their other orders.

A credit note is deliberately **not** a second ledger. It is a view over the `customer_payment`
that backs it: the amount issued is `refunded`, and what is left is that payment's unapplied
balance (FR-070). Redemption therefore has no endpoint of its own — it is an ordinary payment
application (FR-070a), which is what makes it bounded, reversible and correctable through the
payments editor without any of that logic being written twice.

`refunded` is never decremented. If it were, it would be a second source of truth that could drift
from the applications, and nothing would say which one was right.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.models.sales import CreditNote, SalesOrderPayment

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def remaining_balance(issued: Decimal, applied: Decimal) -> Decimal:
    """What is still spendable. Floored at zero — an over-applied note owes nothing further."""
    remaining = issued - applied
    return remaining if remaining > 0 else Decimal(0)


# ── Queries ───────────────────────────────────────────────────────────────────


async def _applied_against(db: AsyncSession, customer_payment_id: int) -> Decimal:
    total = (
        await db.execute(
            select(func.sum(SalesOrderPayment.amount)).where(
                SalesOrderPayment.customer_payment == customer_payment_id,
                SalesOrderPayment.cancelled.is_(False),
            )
        )
    ).scalar_one_or_none()
    return total if total is not None else Decimal(0)


async def attach_remaining(db: AsyncSession, note: CreditNote) -> CreditNote:
    note.__dict__['remaining'] = remaining_balance(
        note.refunded, await _applied_against(db, note.customer_payment)
    )
    return note


async def attach_summary_remaining(db: AsyncSession, notes: Sequence[CreditNote]) -> None:
    """Remaining balances for a whole page in one query."""
    payment_ids = [n.customer_payment for n in notes]
    if not payment_ids:
        return

    rows = (
        await db.execute(
            select(SalesOrderPayment.customer_payment, func.sum(SalesOrderPayment.amount))
            .where(
                SalesOrderPayment.customer_payment.in_(payment_ids),
                SalesOrderPayment.cancelled.is_(False),
            )
            .group_by(SalesOrderPayment.customer_payment)
        )
    ).all()
    applied = {pid: amount or Decimal(0) for pid, amount in rows}

    for note in notes:
        note.__dict__['remaining'] = remaining_balance(
            note.refunded, applied.get(note.customer_payment, Decimal(0))
        )


async def get_credit_note(db: AsyncSession, credit_note_id: int) -> CreditNote | None:
    return await db.get(CreditNote, credit_note_id)


async def list_credit_notes(
    db: AsyncSession,
    *,
    current: CurrentUser,
    customer: int | None = None,
    open_only: bool = False,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CreditNote], int]:
    base = select(CreditNote)
    count_q = select(func.count()).select_from(CreditNote)

    if customer is not None:
        base = base.where(CreditNote.customer == customer)
        count_q = count_q.where(CreditNote.customer == customer)

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(CreditNote.credit_note_id.desc()).offset(skip).limit(limit)
    items = list((await db.execute(page)).scalars().all())

    await attach_summary_remaining(db, items)

    if open_only:
        items = [n for n in items if n.__dict__['remaining'] > 0]

    return items, total
