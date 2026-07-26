"""Payment application rules and the paid-flag arithmetic.

`paid` is derived state with exactly one writer (this service). SC-004 requires that reversing an
application restores an order's balance *exactly*, so the set/clear logic is symmetric here.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.enums import CurrencyCode
from app.services.customer_payment_service import (
    assert_order_payable,
    assert_same_currency,
    assert_same_customer,
    assert_within_unapplied,
    covers_total,
)


def _order(*, completed: bool = True, cancelled: bool = False) -> SimpleNamespace:
    return SimpleNamespace(completed=completed, cancelled=cancelled)


class TestAssertOrderPayable:
    def test_completed_uncancelled_order_is_payable(self) -> None:
        assert_order_payable(_order())

    def test_draft_order_is_refused(self) -> None:
        """FR-042 — only a completed order can be paid."""
        with pytest.raises(HTTPException) as exc:
            assert_order_payable(_order(completed=False))

        assert exc.value.status_code == 409
        assert 'confirm' in exc.value.detail.lower()

    def test_cancelled_order_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_order_payable(_order(cancelled=True))

        assert exc.value.status_code == 409

    def test_cancelled_takes_precedence_over_incomplete(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_order_payable(_order(completed=False, cancelled=True))

        assert 'cancelled' in exc.value.detail.lower()


class TestAssertSameCurrency:
    def test_matching_currency_passes(self) -> None:
        assert_same_currency(CurrencyCode.MXN, CurrencyCode.MXN)

    def test_mismatch_is_refused_not_converted(self) -> None:
        """FR-043 — silently converting would invent an exchange rate nobody chose."""
        with pytest.raises(HTTPException) as exc:
            assert_same_currency(CurrencyCode.USD, CurrencyCode.MXN)

        assert exc.value.status_code == 422


class TestAssertWithinUnapplied:
    def test_amount_equal_to_unapplied_is_allowed(self) -> None:
        assert_within_unapplied(Decimal('100'), Decimal('100'))

    def test_amount_below_unapplied_is_allowed(self) -> None:
        assert_within_unapplied(Decimal('40'), Decimal('100'))

    def test_over_application_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_within_unapplied(Decimal('101'), Decimal('100'))

        assert exc.value.status_code == 422


class TestAssertSameCustomer:
    def test_same_customer_passes(self) -> None:
        assert_same_customer(5, 5)

    def test_different_customer_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_same_customer(5, 6)

        assert exc.value.status_code == 422


class TestCoversTotal:
    def test_exact_coverage_marks_paid(self) -> None:
        assert covers_total(Decimal('290.00'), Decimal('290.00')) is True

    def test_overpayment_marks_paid(self) -> None:
        assert covers_total(Decimal('300.00'), Decimal('290.00')) is True

    def test_one_cent_short_is_not_paid(self) -> None:
        assert covers_total(Decimal('289.99'), Decimal('290.00')) is False

    def test_nothing_applied_is_not_paid(self) -> None:
        assert covers_total(Decimal('0'), Decimal('290.00')) is False

    def test_zero_total_order_is_never_paid(self) -> None:
        """An order totalling zero should not be marked paid by an empty application set."""
        assert covers_total(Decimal('0'), Decimal('0')) is False

    def test_reversal_clears_the_flag_symmetrically(self) -> None:
        """Applying then reversing must return the flag to where it started."""
        total = Decimal('100.00')
        assert covers_total(Decimal('100.00'), total) is True
        assert covers_total(Decimal('0'), total) is False
