"""Credit note balance arithmetic.

The design decision under test: a credit note has no balance of its own. `refunded` is the amount
issued and never moves; what remains is derived from the backing payment's non-cancelled
applications (FR-070). These tests pin the symmetry that makes redemption reversible — apply, and
the remainder falls; reverse, and it comes back exactly.
"""

from decimal import Decimal

from app.services.credit_note_service import remaining_balance


class TestRemainingBalance:
    def test_unredeemed_note_has_its_full_value(self) -> None:
        assert remaining_balance(Decimal('116.00'), Decimal('0')) == Decimal('116.00')

    def test_partial_redemption_leaves_the_remainder(self) -> None:
        assert remaining_balance(Decimal('116.00'), Decimal('16.00')) == Decimal('100.00')

    def test_fully_redeemed_note_has_nothing_left(self) -> None:
        assert remaining_balance(Decimal('116.00'), Decimal('116.00')) == Decimal('0')

    def test_never_reports_a_negative_remainder(self) -> None:
        assert remaining_balance(Decimal('116.00'), Decimal('200.00')) == Decimal('0')

    def test_reversal_restores_the_balance_exactly(self) -> None:
        """US6 scenario 4 — reversing a redemption puts the credit back, to the cent."""
        issued = Decimal('116.00')

        after_apply = remaining_balance(issued, Decimal('116.00'))
        after_reverse = remaining_balance(issued, Decimal('0'))

        assert after_apply == Decimal('0')
        assert after_reverse == issued

    def test_issued_amount_is_never_decremented(self) -> None:
        """The function is pure: `refunded` is an input, so nothing can mutate it."""
        issued = Decimal('116.00')

        remaining_balance(issued, Decimal('50.00'))

        assert issued == Decimal('116.00')

    def test_a_note_issued_for_zero_has_nothing_to_spend(self) -> None:
        assert remaining_balance(Decimal('0'), Decimal('0')) == Decimal('0')
