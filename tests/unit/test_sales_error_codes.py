"""Coded refusals of the sales, quote and customer services (spec 036, user story 2)."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.errors import DomainError
from app.schemas.sales_quote import SalesQuoteCreate, SalesQuoteLineUpdate
from app.services import customer_service, sales_order_service, sales_quote_service

NOW = datetime(2026, 7, 25, 12)


def _current(**overrides) -> CurrentUser:
    base = dict(
        user_id='u',
        session_version=1,
        administrator=True,
        employee_id=7,
        facility_id=1,
        point_sale_id=3,
    )
    return CurrentUser(**(base | overrides))


def _customer(customer_id: int = 7, limit: str = '5000.00') -> SimpleNamespace:
    return SimpleNamespace(customer_id=customer_id, credit_limit=Decimal(limit), credit_days=30)


# ── Credit rules ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_credit_order_over_the_limit_is_refused() -> None:
    with patch.object(
        sales_order_service, '_customer_credit_debt', AsyncMock(return_value=Decimal('18000.00'))
    ):
        with pytest.raises(DomainError) as refusal:
            await sales_order_service._assert_within_credit_limit(
                None, _customer(limit='18000.00'), adding=Decimal('450.00')
            )

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'customer.credit_limit_exceeded'
    assert refusal.value.params == {
        'customer_id': 7,
        'outstanding': '18450.00',
        'limit': '18000.00',
    }
    assert 'over their credit limit' in refusal.value.detail


@pytest.mark.asyncio
async def test_a_cash_order_for_a_customer_at_their_limit_is_refused() -> None:
    with patch.object(
        sales_order_service, '_customer_credit_debt', AsyncMock(return_value=Decimal('5000.00'))
    ):
        with pytest.raises(DomainError) as refusal:
            await sales_order_service._assert_cash_allowed(None, _customer())

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'customer.credit_limit_reached'
    assert refusal.value.params == {
        'customer_id': 7,
        'outstanding': '5000.00',
        'limit': '5000.00',
    }


@pytest.mark.asyncio
async def test_confirming_for_a_customer_on_hold_is_refused() -> None:
    assert settings.default_customer_id != 7
    with patch.object(sales_order_service, '_overdue_credit_orders', AsyncMock(return_value=2)):
        with pytest.raises(DomainError) as refusal:
            await sales_order_service._assert_not_on_credit_hold(None, 7)

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'customer.credit_hold'
    assert refusal.value.params == {'customer_id': 7, 'overdue_count': 2, 'action': 'confirm'}
    assert 'before this order can be confirmed' in refusal.value.detail


# ── User context ──────────────────────────────────────────────────────────────


def test_an_order_without_a_point_of_sale_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        sales_order_service._point_sale(_current(point_sale_id=None), None)

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'user.no_point_of_sale'
    assert refusal.value.params == {}


def test_an_order_without_a_facility_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        sales_order_service._facility(_current(facility_id=None))

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'user.no_facility'


# ── Quotes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_quote_without_a_facility_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        await sales_quote_service.create_quote(
            None, SalesQuoteCreate(), current=_current(facility_id=None)
        )

    assert refusal.value.code == 'user.no_facility'


@pytest.mark.asyncio
async def test_converting_without_a_point_of_sale_is_refused() -> None:
    quote = SimpleNamespace(completed=True, cancelled=False, due_date=datetime(2099, 1, 1))
    with pytest.raises(DomainError) as refusal:
        await sales_quote_service.convert_to_order(
            None, quote, current=_current(point_sale_id=None)
        )

    assert refusal.value.status_code == 422
    assert refusal.value.code == 'user.no_point_of_sale'


@pytest.mark.asyncio
async def test_cancelling_a_cancelled_quote_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        await sales_quote_service.cancel_quote(
            None, SimpleNamespace(cancelled=True), current=_current()
        )

    assert refusal.value.status_code == 409
    assert refusal.value.code == 'quote.already_cancelled'


@pytest.mark.asyncio
async def test_emailing_an_unconfirmed_quote_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        await sales_quote_service.email_quote(
            None,
            SimpleNamespace(completed=False, cancelled=False),
            ['a@b.co'],
            mailer=None,
            current=_current(),
        )

    assert refusal.value.status_code == 409
    assert refusal.value.code == 'quote.not_confirmed'
    assert refusal.value.detail == 'Only a confirmed quote can be emailed'


def test_converting_a_cancelled_quote_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        sales_quote_service.assert_convertible(
            SimpleNamespace(completed=True, cancelled=True, due_date=NOW), now=NOW
        )

    assert refusal.value.code == 'quote.cancelled'


def test_converting_an_expired_quote_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        sales_quote_service.assert_convertible(
            SimpleNamespace(completed=True, cancelled=False, due_date=datetime(2020, 1, 1)),
            now=NOW,
        )

    assert refusal.value.code == 'quote.expired'


def test_converting_an_unconfirmed_quote_is_refused() -> None:
    with pytest.raises(DomainError) as refusal:
        sales_quote_service.assert_convertible(
            SimpleNamespace(completed=False, cancelled=False, due_date=NOW), now=NOW
        )

    assert refusal.value.code == 'quote.not_confirmed'


@pytest.mark.asyncio
async def test_a_quote_line_below_the_minimum_names_the_product() -> None:
    line = SimpleNamespace(product=11, quantity=Decimal(5))
    product = SimpleNamespace(product_id=11, min_order_qty=5)
    db = SimpleNamespace(get=AsyncMock(return_value=product))
    with pytest.raises(DomainError) as refusal:
        await sales_quote_service.update_line(
            db,
            SimpleNamespace(completed=False, cancelled=False),
            line,
            SalesQuoteLineUpdate(quantity=Decimal(1)),
            current=_current(),
        )

    assert refusal.value.code == 'sales_order.below_min_quantity'
    assert refusal.value.params == {'product_id': 11, 'min_order_qty': '5'}


# ── Customers ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_default_customer_cannot_be_deleted() -> None:
    with pytest.raises(DomainError) as refusal:
        await customer_service.delete_customer(None, SimpleNamespace(customer_id=1), 1)

    assert refusal.value.status_code == 409
    assert refusal.value.code == 'customer.default_not_deletable'
    assert refusal.value.params == {}
