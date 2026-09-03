"""Quote defaults and the conversion guard.

Conversion is where a quote stops being an offer and becomes a commitment, so the three refusals
each name their own cause — a salesperson dealing with an expired quote must re-quote, which is a
different action from confirming a draft.
"""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.schemas.sales_quote import SalesQuoteUpdate
from app.services import documents, sales_quote_service
from app.services.sales_quote_service import (
    assert_convertible,
    default_due_date,
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
        assert documents.default_salesperson(42, 7) == 42

    def test_salesperson_falls_back_to_the_caller(self) -> None:
        """FR-030 — `customer.salesperson` is nullable, so a fallback is required."""
        assert documents.default_salesperson(None, 7) == 7


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


class TestTheSalespersonFollowsTheCustomer:
    """#195 — `update_quote` had the same shape and the same gap as `update_order`.

    One difference in the implementation: the order side gets its "did the customer actually
    move" flag from repricing, and the quote side does not reprice, so it computes the flag for
    this rule alone.
    """

    @staticmethod
    async def _update(
        *,
        from_customer: int,
        to_customer: int,
        quote_salesperson: int = 4,
        customer_salesperson: int | None = None,
        sent: SalesQuoteUpdate | None = None,
    ) -> SimpleNamespace:
        quote = SimpleNamespace(
            sales_quote_id=1,
            customer=from_customer,
            salesperson=quote_salesperson,
            completed=False,
            cancelled=False,
            updater=None,
            modification_time=None,
        )
        incoming = SimpleNamespace(customer_id=to_customer, salesperson=customer_salesperson)
        with (
            patch.object(sales_quote_service, '_customer_or_404', AsyncMock(return_value=incoming)),
            patch.object(sales_quote_service, 'attach_derived', AsyncMock(return_value=quote)),
        ):
            await sales_quote_service.update_quote(
                AsyncMock(),
                quote,
                sent if sent is not None else SalesQuoteUpdate(customer=to_customer),
                current=SimpleNamespace(employee_id=7),
            )
        return quote

    @pytest.mark.asyncio
    async def test_it_follows_a_customer_that_has_one(self) -> None:
        quote = await self._update(from_customer=2, to_customer=5, customer_salesperson=9)

        assert quote.salesperson == 9

    @pytest.mark.asyncio
    async def test_a_customer_without_one_leaves_the_quote_alone(self) -> None:
        quote = await self._update(from_customer=2, to_customer=5, customer_salesperson=None)

        assert quote.salesperson == 4

    @pytest.mark.asyncio
    async def test_an_explicit_salesperson_beats_the_customer(self) -> None:
        quote = await self._update(
            from_customer=2,
            to_customer=5,
            customer_salesperson=9,
            sent=SalesQuoteUpdate(customer=5, salesperson=11),
        )

        assert quote.salesperson == 11

    @pytest.mark.asyncio
    async def test_resending_the_same_customer_does_not_re_derive_it(self) -> None:
        quote = await self._update(from_customer=2, to_customer=2, customer_salesperson=9)

        assert quote.salesperson == 4
