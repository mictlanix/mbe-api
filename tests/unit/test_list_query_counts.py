"""List endpoints must issue a constant number of queries, not one per row (T093).

Every list service originally attached its derived figures inside a loop, which is an N+1: a page
of 20 cost 20-60 extra round trips. The `attach_summary_*` helpers batch each page instead.

These tests assert the count stays **flat** as the page grows. A plain "does it work" test passes
either way, so only a count catches the regression — which is exactly how the N+1 got written in
the first place.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.cash_session_service import attach_summary_amounts
from app.services.credit_note_service import attach_summary_remaining
from app.services.customer_payment_service import attach_summary_unapplied
from app.services.customer_refund_service import (
    attach_summary_totals as refund_summary_totals,
)
from app.services.sales_order_service import attach_summary_totals as order_summary_totals
from app.services.sales_quote_service import attach_summary_totals as quote_summary_totals


def _db(*result_sets) -> AsyncMock:
    """A session whose `execute` returns the given result sets in order, counting calls."""
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(result_sets))
    return db


def _rows(rows: list) -> SimpleNamespace:
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: rows), all=lambda: rows
    )


PAGE_SIZES = [1, 5, 50]


class TestSalesOrders:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_two_queries_regardless_of_page_size(self, size: int) -> None:
        orders = [
            SimpleNamespace(sales_order_id=i, completed=False, cancelled=False, paid=False)
            for i in range(1, size + 1)
        ]
        db = _db(_rows([]), _rows([]))

        await order_summary_totals(db, orders)

        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_empty_page_issues_no_queries(self) -> None:
        db = _db()

        await order_summary_totals(db, [])

        assert db.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_totals_are_still_attached_to_every_row(self) -> None:
        """Batching must not silently drop the values it exists to compute."""
        orders = [
            SimpleNamespace(sales_order_id=i, completed=True, cancelled=False, paid=False)
            for i in (1, 2)
        ]
        line = SimpleNamespace(
            sales_order=1,
            quantity=Decimal('2'),
            price=Decimal('100'),
            discount_rate=Decimal('0'),
            tax_rate=Decimal('0.16'),
            tax_included=False,
        )
        db = _db(_rows([line]), _rows([]))

        await order_summary_totals(db, orders)

        assert orders[0].__dict__['total'] == Decimal('232.00')
        assert orders[1].__dict__['total'] == Decimal('0.00')
        assert orders[0].__dict__['status'] == 'completed'


class TestSalesQuotes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        quotes = [
            SimpleNamespace(
                sales_quote_id=i, completed=False, cancelled=False, due_date=None
            )
            for i in range(1, size + 1)
        ]
        db = _db(_rows([]))

        await quote_summary_totals(db, quotes)

        assert db.execute.await_count == 1


class TestCustomerPayments:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        payments = [
            SimpleNamespace(customer_payment_id=i, amount=Decimal('100'))
            for i in range(1, size + 1)
        ]
        db = _db(_rows([]))

        await attach_summary_unapplied(db, payments)

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_unapplied_reflects_the_batched_aggregate(self) -> None:
        payments = [
            SimpleNamespace(customer_payment_id=1, amount=Decimal('100')),
            SimpleNamespace(customer_payment_id=2, amount=Decimal('50')),
        ]
        db = _db(_rows([(1, Decimal('40'))]))

        await attach_summary_unapplied(db, payments)

        assert payments[0].__dict__['unapplied'] == Decimal('60')
        assert payments[1].__dict__['unapplied'] == Decimal('50')


class TestCustomerRefunds:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        refunds = [
            SimpleNamespace(customer_refund_id=i, completed=False, cancelled=False)
            for i in range(1, size + 1)
        ]
        db = _db(_rows([]))

        await refund_summary_totals(db, refunds)

        assert db.execute.await_count == 1


class TestCreditNotes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_one_query_regardless_of_page_size(self, size: int) -> None:
        notes = [
            SimpleNamespace(credit_note_id=i, customer_payment=i, refunded=Decimal('100'))
            for i in range(1, size + 1)
        ]
        db = _db(_rows([]))

        await attach_summary_remaining(db, notes)

        assert db.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_remaining_reflects_the_batched_aggregate(self) -> None:
        notes = [
            SimpleNamespace(credit_note_id=1, customer_payment=9, refunded=Decimal('116')),
        ]
        db = _db(_rows([(9, Decimal('16'))]))

        await attach_summary_remaining(db, notes)

        assert notes[0].__dict__['remaining'] == Decimal('100')


class TestCashSessions:
    @pytest.mark.asyncio
    @pytest.mark.parametrize('size', PAGE_SIZES)
    async def test_two_queries_regardless_of_page_size(self, size: int) -> None:
        sessions = [SimpleNamespace(cash_session_id=i) for i in range(1, size + 1)]
        db = _db(_rows([]), _rows([]))

        await attach_summary_amounts(db, sessions)

        assert db.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_opening_amount_and_methods_are_attached_per_session(self) -> None:
        sessions = [SimpleNamespace(cash_session_id=1), SimpleNamespace(cash_session_id=2)]
        db = _db(
            _rows([(1, Decimal('500'))]),
            _rows([(1, 1, Decimal('1200'))]),
        )

        await attach_summary_amounts(db, sessions)

        assert sessions[0].__dict__['opening_amount'] == Decimal('500')
        assert sessions[0].__dict__['payments_by_method'] == [
            {'method': 1, 'total': Decimal('1200')}
        ]
        assert sessions[1].__dict__['opening_amount'] == Decimal('0')
        assert sessions[1].__dict__['payments_by_method'] == []
