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
from app.services import customer_payment_service
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


def _db(orders: list, customer_names: list | None = None) -> AsyncMock:
    results = [
        SimpleNamespace(scalar_one=lambda: len(orders)),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: orders)),
    ]
    # The customer-name lookup (#174), issued once for the page and only when it has rows.
    if orders:
        rows = customer_names or []
        results.append(SimpleNamespace(all=lambda: rows))
    db = AsyncMock()
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


class TestTheCustomerNameLookup:
    """#174 — the projection read `sales_order.customer_name`, the per-document override.

    That column was null on all 1,840 outstanding orders in the deployment, so every row rendered a
    dash. The search beside it had always matched the customer's own name, which is what made the
    gap odd: a cashier could find an order by a name the row then refused to show.
    """

    @staticmethod
    def _names(rows: list) -> AsyncMock:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
        return db

    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', [1, 5, 20, 100])
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        """The constraint #174 names: the row loop already issues two queries per order, so a
        per-row name lookup would make a page of twenty cost sixty round trips."""
        orders = [SimpleNamespace(customer=i) for i in range(1, size + 1)]
        db = self._names([])

        await customer_payment_service._customer_names(db, orders)

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_repeated_customers_share_the_one_lookup(self) -> None:
        """A page of walk-in sales is one customer repeated — keyed on the distinct ids."""
        orders = [SimpleNamespace(customer=1) for _ in range(20)]
        db = self._names([(1, 'PÚBLICO EN GENERAL')])

        names = await customer_payment_service._customer_names(db, orders)

        assert db.execute.await_count == 1
        assert names == {1: 'PÚBLICO EN GENERAL'}

    @pytest.mark.asyncio
    async def test_an_empty_page_issues_no_query(self) -> None:
        db = self._names([])

        assert await customer_payment_service._customer_names(db, []) == {}
        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_a_missing_customer_row_leaves_the_name_null(self) -> None:
        """Null, not a KeyError: the column is cosmetic and must not 500 a whole page."""
        db = _db([_order()], customer_names=[])

        with patch(
            'app.services.customer_payment_service._order_total',
            AsyncMock(return_value=Decimal('1.00')),
        ), patch(
            'app.services.sales_order_service.applied_amount',
            AsyncMock(return_value=Decimal('0.00')),
        ):
            rows, _ = await search_outstanding(db, current=_current())

        assert rows[0]['customer_display_name'] is None

    @pytest.mark.asyncio
    async def test_the_row_carries_the_name_beside_the_override(self) -> None:
        db = _db([_order()], customer_names=[(2, 'Cliente Dos')])

        with patch(
            'app.services.customer_payment_service._order_total',
            AsyncMock(return_value=Decimal('1.00')),
        ), patch(
            'app.services.sales_order_service.applied_amount',
            AsyncMock(return_value=Decimal('0.00')),
        ):
            rows, _ = await search_outstanding(db, current=_current())

        assert rows[0]['customer_display_name'] == 'Cliente Dos'
        assert rows[0]['customer_name'] is None
