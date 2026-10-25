"""The invoice editor's pure rules (spec 027, research R6 and R7)."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services import invoice_service as svc

_T0 = datetime(2026, 10, 1, 9, 0)


def _at(hours: int) -> datetime:
    return _T0.replace(hour=9 + hours)


def _invoice(hours: int, quantity: str) -> svc.LineEvent:
    return svc.LineEvent(_at(hours), svc.INVOICE, Decimal(quantity))


def _refund(hours: int, quantity: str) -> svc.LineEvent:
    return svc.LineEvent(_at(hours), svc.REFUND, Decimal(quantity))


@pytest.mark.parametrize(
    ('events', 'expected'),
    [
        # The spec's two examples (FR-006).
        ([_refund(0, '3'), _invoice(1, '7')], '0'),
        ([_invoice(0, '7'), _refund(1, '3')], '0'),
        # A refund before any invoice takes those units out of what can be invoiced.
        ([_refund(0, '3')], '7'),
        # A refund only reaches the units not yet invoiced: 10 − 4 − 3 − 2 = 1.
        ([_invoice(0, '4'), _refund(1, '3'), _invoice(2, '2')], '1'),
        # A refund of already-invoiced goods frees nothing: that needs a credit note.
        ([_invoice(0, '10'), _refund(1, '4')], '0'),
        ([_invoice(0, '8'), _refund(1, '5')], '0'),
        # At equal times the refund goes first, the conservative order.
        ([_invoice(0, '7'), _refund(0, '3')], '0'),
        ([_invoice(0, '4'), _refund(0, '3')], '3'),
        # Never negative.
        ([_invoice(0, '12')], '0'),
        ([], '10'),
    ],
)
def test_remaining_quantity(events: list[svc.LineEvent], expected: str) -> None:
    assert svc.remaining_quantity(Decimal('10'), events) == Decimal(expected)


def test_remaining_quantity_sorts_its_events() -> None:
    assert svc.remaining_quantity(Decimal('10'), [_invoice(1, '7'), _refund(0, '3')]) == 0


def _batch(batch: str, type_: int = 0) -> SimpleNamespace:
    return SimpleNamespace(batch=batch, type=type_)


def test_default_batch_is_the_stores_when_it_is_an_invoice_batch_of_the_issuer() -> None:
    assert svc.default_batch('B', [_batch('A'), _batch('B')]) == 'B'


@pytest.mark.parametrize('store_default', [None, '', 'Z'])
def test_default_batch_falls_back_to_the_first(store_default: str | None) -> None:
    assert svc.default_batch(store_default, [_batch('A'), _batch('B')]) == 'A'


def test_default_batch_without_invoice_batches_is_none() -> None:
    assert svc.default_batch('A', []) is None


@pytest.mark.parametrize(
    ('credit_days', 'credit_limit', 'expected'),
    [
        (30, Decimal('1000'), svc.PaymentTerms.NET_D),
        (0, Decimal('1000'), svc.PaymentTerms.IMMEDIATE),
        (30, Decimal('0'), svc.PaymentTerms.IMMEDIATE),
        (0, Decimal('0'), svc.PaymentTerms.IMMEDIATE),
    ],
)
def test_default_terms_follow_legacy_has_credit(
    credit_days: int, credit_limit: Decimal, expected: svc.PaymentTerms
) -> None:
    customer = SimpleNamespace(credit_days=credit_days, credit_limit=credit_limit)
    assert svc.default_terms(customer) == expected


@pytest.mark.parametrize(
    ('issued', 'ok'),
    [
        (datetime(2026, 10, 25, 12, 0), True),
        (datetime(2026, 10, 22, 12, 0, 1), True),
        (datetime(2026, 10, 22, 12, 0), False),
        (datetime(2026, 10, 25, 12, 0, 1), False),
    ],
)
def test_issue_date_window(issued: datetime, ok: bool) -> None:
    assert svc.issue_date_ok(issued, now=datetime(2026, 10, 25, 12, 0)) is ok


# ── Payment receipt balances (spec 028, research R2) ──────────────────────────


@pytest.mark.parametrize(('earlier', 'expected'), [(0, 1), (1, 2), (4, 5)])
def test_installment_follows_the_earlier_receipts(earlier: int, expected: int) -> None:
    assert svc.installment(earlier) == expected


@pytest.mark.parametrize(
    ('total', 'paid', 'credited', 'expected'),
    [
        ('10000.00', '0', '0', '10000.00'),
        ('10000.00', '4000.00', '0', '6000.00'),
        ('10000.00', '4000.00', '1160.00', '4840.00'),
        ('100.00', '100.00', '0', '0.00'),
    ],
)
def test_previous_balance(total: str, paid: str, credited: str, expected: str) -> None:
    assert svc.previous_balance(Decimal(total), Decimal(paid), Decimal(credited)) == Decimal(
        expected
    )


@pytest.mark.parametrize(
    ('balance', 'unassigned', 'expected'),
    [
        ('6000.00', '4000.00', '4000.00'),
        ('3000.00', '4000.00', '3000.00'),
        ('3000.00', None, '3000.00'),
        ('3000.00', '0', '0'),
        ('3000.00', '-5', '0'),
    ],
)
def test_default_amount_is_the_smaller_of_balance_and_unassigned(
    balance: str, unassigned: str | None, expected: str
) -> None:
    left = Decimal(unassigned) if unassigned is not None else None
    assert svc.default_amount(Decimal(balance), left) == Decimal(expected)
