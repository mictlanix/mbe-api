"""Accounts receivable and accounts payable: one design seen from two sides (spec 033).

A customer's credit orders and a supplier's purchase orders are the same thing to these views: open
documents with a balance and a due date. The aging, the party's figures and the statement are
written once here, and each side supplies its own documents, credit and party data.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.enums import CurrencyCode
from app.services import totals

BUCKETS = ('current', '1-30', '31-60', '61-90', '90+')
_RATIO = Decimal('0.0001')


@dataclass(frozen=True)
class Document:
    """An open document on either side, in the shape the drill-down shows (data-model.md)."""

    id: int
    party: int
    serial: int | None
    date: datetime
    due_date: datetime | None
    facility: int | None
    facility_code: str | None
    salesperson: int | None
    #: `{'id': …, 'label': …}` for each invoice or receipt.
    linked: tuple[dict[str, Any], ...]
    currency: CurrencyCode
    exchange_rate: Decimal
    total: Decimal
    deductions: Decimal
    applied: Decimal
    balance: Decimal


# ── Aging and figures (pure) ──────────────────────────────────────────────────


def days_past_due(due: datetime | None, today: date) -> int:
    """Local days since the due date; 0 with no due date or one of today or later."""
    if due is None:
        return 0
    return max((today - due.date()).days, 0)


def bucket(days: int) -> str:
    if days <= 0:
        return 'current'
    if days <= 30:
        return '1-30'
    if days <= 60:
        return '31-60'
    if days <= 90:
        return '61-90'
    return '90+'


def summarize(
    documents: Sequence[Document],
    *,
    today: date,
    credit: Decimal,
    credit_limit: Decimal | None,
    owed_company: Decimal,
) -> dict[str, Any]:
    """A party's figures from its open documents in scope (FR-014, FR-015).

    Owed is in base currency at each document's rate, as the credit check sums it. Each document
    lands in exactly one bucket, so the buckets add up to owed. Utilization and available credit
    read `owed_company`, what the party owes across every facility, so a user scoped to one
    facility sees the same limit picture as everyone else; with no limit they stay blank.
    """
    buckets = dict.fromkeys(BUCKETS, Decimal(0))
    for document in documents:
        base = document.balance * document.exchange_rate
        buckets[bucket(days_past_due(document.due_date, today))] += base
    buckets = {name: value.quantize(totals.CENTS) for name, value in buckets.items()}
    owed = sum(buckets.values(), Decimal(0))
    limited = credit_limit is not None and credit_limit > 0
    return {
        'owed': owed,
        'overdue': owed - buckets['current'],
        'buckets': buckets,
        'oldest': min((document.date for document in documents), default=None),
        'credit': credit,
        'net': owed - credit,
        'utilization': (owed_company / credit_limit).quantize(_RATIO) if limited else None,
        'available_credit': credit_limit - owed_company if limited else None,
    }
