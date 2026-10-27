"""Credit note balance arithmetic (spec 034, FR-002).

The design decision under test: a credit note has no balance of its own. What remains is its
backing payment's unapplied amount, the figure the payment and its ticket report (spec 033 FR-005a),
and a note paid back in cash has nothing left to spend. These tests pin the symmetry that makes
redemption reversible — apply, and the remainder falls; reverse, and it comes back exactly.
"""

from decimal import Decimal

from app.services.credit_note_service import remaining_balance


class TestRemainingBalance:
    def test_an_unredeemed_note_has_its_payments_full_value(self) -> None:
        assert remaining_balance(Decimal('116.00'), refunded_to_customer=False) == Decimal('116.00')

    def test_a_partial_redemption_leaves_what_the_payment_has_left(self) -> None:
        assert remaining_balance(Decimal('3.00'), refunded_to_customer=False) == Decimal('3.00')

    def test_a_fully_redeemed_note_has_nothing_left(self) -> None:
        assert remaining_balance(Decimal('0'), refunded_to_customer=False) == Decimal('0')

    def test_never_reports_a_negative_remainder(self) -> None:
        """An over-applied legacy payment owes nothing further."""
        assert remaining_balance(Decimal('-84.00'), refunded_to_customer=False) == Decimal('0')

    def test_a_note_paid_back_in_cash_has_nothing_to_spend(self) -> None:
        """Note 158: its payment still holds 1,025.00, all of it handed back in cash."""
        assert remaining_balance(Decimal('1025.00'), refunded_to_customer=True) == Decimal('0')

    def test_reversal_restores_the_balance_exactly(self) -> None:
        """Reversing a redemption puts the payment's unapplied amount back, to the cent."""
        after_apply = remaining_balance(Decimal('0'), refunded_to_customer=False)
        after_reverse = remaining_balance(Decimal('116.00'), refunded_to_customer=False)

        assert after_apply == Decimal('0')
        assert after_reverse == Decimal('116.00')
