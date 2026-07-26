"""Refund eligibility, refundable-quantity arithmetic, and confirmation-time reconciliation.

This is where the lifecycle clarifications concentrate. Two invariants matter most:

- SC-006: no customer can be refunded more units of a line than were sold, under any interleaving
  of concurrent refunds. `reconcile_lines` is what enforces it once the source order is locked.
- FR-064: a refundable order is already fully paid, so a refund never touches its `paid` flag and
  never writes `balance_zeroed_time`.
"""

from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services.customer_refund_service import (
    assert_quantity_refundable,
    assert_refundable_order,
    reconcile_lines,
    refundable_quantity,
)


def _order(*, completed: bool = True, paid: bool = True) -> SimpleNamespace:
    return SimpleNamespace(completed=completed, paid=paid)


def _line(quantity: str) -> SimpleNamespace:
    return SimpleNamespace(quantity=Decimal(quantity))


class TestAssertRefundableOrder:
    def test_completed_and_paid_order_is_refundable(self) -> None:
        assert_refundable_order(_order())

    def test_unpaid_order_is_refused_and_directed_to_cancel(self) -> None:
        """FR-060 — an unpaid order is unwound by cancelling, not refunding."""
        with pytest.raises(HTTPException) as exc:
            assert_refundable_order(_order(paid=False))

        assert exc.value.status_code == 409
        assert 'not paid' in exc.value.detail.lower()
        assert 'cancelling' in exc.value.detail.lower()

    def test_draft_order_is_refused_for_a_different_reason(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_refundable_order(_order(completed=False, paid=False))

        assert 'not completed' in exc.value.detail.lower()

    def test_the_two_refusals_are_distinguishable(self) -> None:
        """The clerk must learn whether to confirm the order or cancel it."""
        with pytest.raises(HTTPException) as not_completed:
            assert_refundable_order(_order(completed=False, paid=False))
        with pytest.raises(HTTPException) as not_paid:
            assert_refundable_order(_order(completed=True, paid=False))

        assert not_completed.value.detail != not_paid.value.detail


class TestRefundableQuantity:
    def test_nothing_refunded_yet_leaves_the_whole_quantity(self) -> None:
        assert refundable_quantity(Decimal('10'), Decimal('0')) == Decimal('10')

    def test_partial_prior_refund_leaves_the_remainder(self) -> None:
        assert refundable_quantity(Decimal('10'), Decimal('4')) == Decimal('6')

    def test_fully_refunded_leaves_nothing(self) -> None:
        assert refundable_quantity(Decimal('10'), Decimal('10')) == Decimal('0')

    def test_never_goes_negative(self) -> None:
        """Defensive: an over-refunded line reports zero, not a negative allowance."""
        assert refundable_quantity(Decimal('10'), Decimal('12')) == Decimal('0')


class TestAssertQuantityRefundable:
    def test_within_the_allowance_passes(self) -> None:
        assert_quantity_refundable(Decimal('4'), Decimal('6'))

    def test_exactly_the_allowance_passes(self) -> None:
        assert_quantity_refundable(Decimal('6'), Decimal('6'))

    def test_above_the_allowance_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_quantity_refundable(Decimal('7'), Decimal('6'))

        assert exc.value.status_code == 422


class TestReconcileLines:
    def test_keeps_lines_that_fit(self) -> None:
        keep, drop = reconcile_lines([(_line('4'), Decimal('10'))])

        assert len(keep) == 1
        assert keep[0][1] == Decimal('4')
        assert drop == []

    def test_drops_zero_quantity_lines(self) -> None:
        """A pre-populated line the clerk never filled in should not reach the ledger."""
        keep, drop = reconcile_lines([(_line('0'), Decimal('10'))])

        assert keep == []
        assert len(drop) == 1

    def test_adjusts_a_line_a_concurrent_refund_partly_consumed(self) -> None:
        """SC-006 — the claim is trimmed to what is still available, not refused outright."""
        keep, drop = reconcile_lines([(_line('8'), Decimal('3'))])

        assert keep[0][1] == Decimal('3')
        assert drop == []

    def test_drops_a_line_a_concurrent_refund_fully_consumed(self) -> None:
        keep, drop = reconcile_lines([(_line('5'), Decimal('0'))])

        assert keep == []
        assert len(drop) == 1

    def test_mixed_set_is_split_correctly(self) -> None:
        lines = [
            (_line('4'), Decimal('10')),
            (_line('0'), Decimal('10')),
            (_line('9'), Decimal('2')),
            (_line('3'), Decimal('0')),
        ]

        keep, drop = reconcile_lines(lines)

        assert [q for _, q in keep] == [Decimal('4'), Decimal('2')]
        assert len(drop) == 2

    def test_empty_input_yields_nothing(self) -> None:
        assert reconcile_lines([]) == ([], [])

    def test_two_refunds_cannot_together_exceed_what_was_sold(self) -> None:
        """The SC-006 scenario: 10 sold, one refund took 7, a second claims 6."""
        sold = Decimal('10')
        first_refund = Decimal('7')
        still_refundable = refundable_quantity(sold, first_refund)

        keep, _ = reconcile_lines([(_line('6'), still_refundable)])

        assert keep[0][1] == Decimal('3')
        assert first_refund + keep[0][1] == sold
