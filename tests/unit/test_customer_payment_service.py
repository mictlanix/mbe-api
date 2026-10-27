"""Payment application rules and the paid-flag arithmetic.

`paid` is derived state with exactly one writer (this service). SC-004 requires that reversing an
application restores an order's balance *exactly*, so the set/clear logic is symmetric here.
"""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.enums import CurrencyCode
from app.services import customer_payment_service
from app.services.customer_payment_service import (
    assert_order_payable,
    assert_same_currency,
    assert_same_customer,
    assert_within_unapplied,
    list_order_applications,
    reopen,
    settle,
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


class TestListOrderApplications:
    """#134 — the order→payments direction, flattened so a list renders in one request."""

    @staticmethod
    def _db(rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
        return db

    @pytest.mark.asyncio
    async def test_projects_the_payment_fields_a_row_needs(self) -> None:
        application = SimpleNamespace(
            sales_order_payment_id=9,
            sales_order=1,
            customer_payment=4,
            amount=Decimal('100'),
            amount_change=Decimal('0'),
            applier=7,
            date='applied-on',
            cancelled=False,
        )
        payment = SimpleNamespace(
            method=4,
            currency=CurrencyCode.MXN,
            reference='AUTH-771',
            date='taken-on',
            payment_type=1,
            verifier=None,
        )
        db = self._db([(application, payment)])

        rows = await list_order_applications(db, 1)

        assert len(rows) == 1
        assert rows[0]['reference'] == 'AUTH-771'
        assert rows[0]['method'] == 4
        # The application's date and the payment's are separate keys — they differ whenever a
        # payment is applied later than it was taken.
        assert rows[0]['date'] == 'applied-on'
        assert rows[0]['payment_date'] == 'taken-on'

    @pytest.mark.asyncio
    async def test_one_query_regardless_of_how_many_applications(self) -> None:
        """The join is the point — no follow-up fetch per application (the N+1 rule)."""
        application = SimpleNamespace(
            sales_order_payment_id=9,
            sales_order=1,
            customer_payment=4,
            amount=Decimal('100'),
            amount_change=Decimal('0'),
            applier=7,
            date=None,
            cancelled=False,
        )
        payment = SimpleNamespace(
            method=1, currency=CurrencyCode.MXN, reference=None, date=None,
            payment_type=1, verifier=None,
        )
        db = self._db([(application, payment)] * 25)

        rows = await list_order_applications(db, 1)

        assert len(rows) == 25
        assert db.execute.await_count == 1


class TestSettle:
    """Spec 033 FR-011: an order is flagged paid once its balance is 0.01 or less. The moment is
    stamped once and dates the order's commission month (spec 025 FR-008a)."""

    NOW = datetime(2026, 10, 22, 12, 0)

    def _settle(self, balance: str, *, paid: bool = False, stamp: object = None) -> SimpleNamespace:
        order = SimpleNamespace(paid=paid, balance_zeroed_time=stamp)
        with patch.object(customer_payment_service, 'local_now', return_value=self.NOW):
            settle(order, Decimal(balance))
        return order

    @pytest.mark.parametrize('balance', ['0', '0.01'])
    def test_nothing_left_sets_paid_and_stamps_it(self, balance: str) -> None:
        order = self._settle(balance)

        assert order.paid is True
        assert order.balance_zeroed_time == self.NOW

    def test_the_legacy_sentinel_counts_as_unset(self) -> None:
        assert self._settle('0', stamp=datetime(1, 1, 1)).balance_zeroed_time == self.NOW

    def test_an_existing_stamp_is_kept(self) -> None:
        earlier = datetime(2026, 9, 1)

        assert self._settle('0', stamp=earlier).balance_zeroed_time == earlier

    def test_two_cents_left_changes_nothing(self) -> None:
        order = self._settle('0.02')

        assert (order.paid, order.balance_zeroed_time) == (False, None)

    def test_it_never_clears_paid(self) -> None:
        assert self._settle('500', paid=True).paid is True

    def test_an_order_already_paid_is_not_stamped(self) -> None:
        """FR-065a: a refund on a legacy paid order settles nothing new, so it gains no commission
        month."""
        assert self._settle('0', paid=True).balance_zeroed_time is None


class TestReopen:
    """Spec 033 FR-011, clarification Q2: `paid` is cleared only when an event takes the balance
    from 0.01 or less to above it. A paid order that already owed stays paid."""

    STAMP = datetime(2026, 9, 1)

    @pytest.mark.parametrize(
        ('before', 'after', 'cleared'),
        [
            ('0', '0.02', True),
            ('0.01', '0.02', True),
            ('0.02', '50', False),
            ('0', '0.01', False),
        ],
        ids=['settled-to-owing', 'edge-to-owing', 'legacy-already-owing', 'still-settled'],
    )
    def test_only_a_crossing_clears_paid(self, before: str, after: str, cleared: bool) -> None:
        order = SimpleNamespace(paid=True, balance_zeroed_time=self.STAMP)

        reopen(order, Decimal(before), Decimal(after))

        assert order.paid is not cleared
        assert order.balance_zeroed_time == self.STAMP, 'the commission month never moves'
