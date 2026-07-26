"""Quote defaults and the conversion guard.

Conversion is where a quote stops being an offer and becomes a commitment, so the three refusals
each name their own cause — a salesperson dealing with an expired quote must re-quote, which is a
different action from confirming a draft.
"""

from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException

from app.services.sales_quote_service import (
    assert_convertible,
    default_due_date,
    default_salesperson,
    has_expired,
)

NOW = datetime(2026, 7, 25, 12)


class _Quote:
    def __init__(self, *, completed=True, cancelled=False, due_date=None) -> None:
        self.completed = completed
        self.cancelled = cancelled
        self.due_date = due_date if due_date is not None else NOW + timedelta(days=1)


class TestDefaults:
    def test_due_date_is_today_plus_the_validity_period(self) -> None:
        assert default_due_date(NOW, validity_days=30) == datetime(2026, 8, 24, 12)

    def test_zero_validity_expires_the_same_day(self) -> None:
        assert default_due_date(NOW, validity_days=0) == NOW

    def test_salesperson_prefers_the_customers_assigned_one(self) -> None:
        assert default_salesperson(42, 7) == 42

    def test_salesperson_falls_back_to_the_caller(self) -> None:
        """FR-030 — `customer.salesperson` is nullable, so a fallback is required."""
        assert default_salesperson(None, 7) == 7


class TestHasExpired:
    def test_future_due_date_has_not_expired(self) -> None:
        assert has_expired(_Quote(due_date=NOW + timedelta(days=1)), now=NOW) is False

    def test_past_due_date_has_expired(self) -> None:
        assert has_expired(_Quote(due_date=NOW - timedelta(seconds=1)), now=NOW) is True

    def test_expiry_ignores_document_state(self) -> None:
        """A draft can be expired too; expiry is about the date alone."""
        stale_draft = _Quote(completed=False, due_date=NOW - timedelta(days=5))

        assert has_expired(stale_draft, now=NOW) is True


class TestAssertConvertible:
    def test_confirmed_unexpired_quote_converts(self) -> None:
        assert_convertible(_Quote(), now=NOW)

    def test_draft_is_refused_and_says_to_confirm(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_convertible(_Quote(completed=False), now=NOW)

        assert exc.value.status_code == 409
        assert 'confirm' in exc.value.detail.lower()

    def test_cancelled_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_convertible(_Quote(cancelled=True), now=NOW)

        assert exc.value.status_code == 409
        assert 'cancelled' in exc.value.detail.lower()

    def test_expired_is_refused_and_says_to_re_quote(self) -> None:
        expired = _Quote(due_date=NOW - timedelta(days=1))

        with pytest.raises(HTTPException) as exc:
            assert_convertible(expired, now=NOW)

        assert exc.value.status_code == 409
        assert 'expired' in exc.value.detail.lower()
        assert 'duplicate' in exc.value.detail.lower()

    def test_cancelled_takes_precedence_over_expired(self) -> None:
        both = _Quote(cancelled=True, due_date=NOW - timedelta(days=1))

        with pytest.raises(HTTPException) as exc:
            assert_convertible(both, now=NOW)

        assert 'cancelled' in exc.value.detail.lower()

    def test_a_quote_may_be_converted_more_than_once(self) -> None:
        """Spec Assumption 11 — the legacy system does not block it, and neither do we."""
        quote = _Quote()

        assert_convertible(quote, now=NOW)
        assert_convertible(quote, now=NOW)
