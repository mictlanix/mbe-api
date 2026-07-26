"""The outstanding-orders search a cashier uses to find what a customer owes (FR-046).

Two behaviours matter: a numeric term is an identifier (order id or folio) rather than a name
fragment, and the result set is confined to orders that can actually take money — completed,
uncancelled and unpaid.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.deps import CurrentUser
from app.services.customer_payment_service import is_barcode_or_id, search_outstanding


class TestSearchTermRouting:
    def test_digits_are_treated_as_an_identifier(self) -> None:
        assert is_barcode_or_id('4212') is True

    def test_a_name_is_not(self) -> None:
        assert is_barcode_or_id('ACME Corp') is False

    def test_an_alphanumeric_code_is_not(self) -> None:
        assert is_barcode_or_id('42B') is False

    def test_an_empty_term_is_not(self) -> None:
        assert is_barcode_or_id('') is False

    def test_a_term_with_spaces_is_not(self) -> None:
        assert is_barcode_or_id('42 12') is False


def _current() -> CurrentUser:
    return CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
    )


def _db(orders: list) -> AsyncMock:
    db = AsyncMock()
    results = [
        SimpleNamespace(scalar_one=lambda: len(orders)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: orders)),
    ]
    db.execute = AsyncMock(side_effect=results)
    return db


def _order() -> SimpleNamespace:
    return SimpleNamespace(
        sales_order_id=1,
        serial=42,
        customer=2,
        customer_name=None,
        date='2026-07-25',
        due_date='2026-08-24',
        currency=0,
    )


class TestSearchOutstanding:
    @pytest.mark.asyncio
    async def test_restricts_to_completed_unpaid_uncancelled_orders(self) -> None:
        """Only an order that can take money belongs in this list."""
        db = _db([])

        await search_outstanding(db, current=_current())

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'completed' in sql
        assert 'cancelled' in sql
        assert 'paid' in sql

    @pytest.mark.asyncio
    async def test_scopes_to_the_callers_facility(self) -> None:
        db = _db([])

        await search_outstanding(db, current=_current())

        assert 'facility' in str(db.execute.await_args_list[0].args[0]).lower()

    @pytest.mark.asyncio
    async def test_reports_each_orders_balance(self) -> None:
        db = _db([_order()])

        with patch(
            'app.services.customer_payment_service._order_total',
            AsyncMock(return_value=Decimal('290.00')),
        ), patch(
            'app.services.sales_order_service.applied_amount',
            AsyncMock(return_value=Decimal('100.00')),
        ):
            rows, total = await search_outstanding(db, current=_current())

        assert total == 1
        assert rows[0]['total'] == Decimal('290.00')
        assert rows[0]['balance'] == Decimal('190.00')

    @pytest.mark.asyncio
    async def test_fully_applied_order_reports_zero_balance(self) -> None:
        db = _db([_order()])

        with patch(
            'app.services.customer_payment_service._order_total',
            AsyncMock(return_value=Decimal('290.00')),
        ), patch(
            'app.services.sales_order_service.applied_amount',
            AsyncMock(return_value=Decimal('290.00')),
        ):
            rows, _ = await search_outstanding(db, current=_current())

        assert rows[0]['balance'] == Decimal('0.00')
