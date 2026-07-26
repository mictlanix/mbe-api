"""Tests for the /credit-notes endpoints.

Note what is absent: there is no redemption route. Redeeming a credit note is an ordinary payment
application against its backing payment (FR-070a), and `test_no_redemption_route_exists` guards
that decision so nobody adds a parallel path later.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=1, employee_id=7
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _note(**overrides) -> SimpleNamespace:
    base = dict(
        credit_note_id=1,
        customer=2,
        sales_order=5,
        customer_refund=3,
        customer_payment=9,
        refunded=Decimal('116.00'),
        remaining=Decimal('116.00'),
        cash_session=4,
        date=datetime(2026, 7, 25),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/credit-notes')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_list_reports_issued_and_remaining_and_its_origin() -> None:
    """FR-070 — each note names the refund and order that produced it."""
    _auth()
    with patch(
        'app.services.credit_note_service.list_credit_notes',
        AsyncMock(return_value=([_note()], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes?customer=2')

    assert response.status_code == status.HTTP_200_OK
    row = response.json()['items'][0]
    assert row['refunded'] == '116.00'
    assert row['remaining'] == '116.00'
    assert row['customer_refund'] == 3
    assert row['sales_order'] == 5


@pytest.mark.asyncio
async def test_partially_redeemed_note_shows_the_reduced_remainder() -> None:
    _auth()
    partly = _note(remaining=Decimal('16.00'))
    with patch(
        'app.services.credit_note_service.list_credit_notes',
        AsyncMock(return_value=([partly], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes')

    row = response.json()['items'][0]
    assert row['refunded'] == '116.00'
    assert row['remaining'] == '16.00'


@pytest.mark.asyncio
async def test_open_only_filter_is_passed_through() -> None:
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.credit_note_service.list_credit_notes', listing):
        async with await _client() as client:
            await client.get('/api/v1/credit-notes?open_only=true')

    assert listing.await_args.kwargs['open_only'] is True


@pytest.mark.asyncio
async def test_get_a_single_note() -> None:
    _auth()
    with patch(
        'app.services.credit_note_service.get_credit_note', AsyncMock(return_value=_note())
    ), patch(
        'app.services.credit_note_service.attach_remaining', AsyncMock(return_value=_note())
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/1')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['credit_note_id'] == 1


@pytest.mark.asyncio
async def test_unknown_note_is_404() -> None:
    _auth()
    with patch(
        'app.services.credit_note_service.get_credit_note', AsyncMock(return_value=None)
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/credit-notes/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_no_redemption_route_exists() -> None:
    """FR-070a — redemption is a payment application, not a credit-note endpoint.

    Guards against a well-meaning addition that would bypass the unapplied-amount bound and the
    reversal path the payment application already provides.
    """
    credit_note_paths = {
        route.path for route in app.routes if '/credit-notes' in str(getattr(route, 'path', ''))
    }

    assert credit_note_paths == {'/api/v1/credit-notes', '/api/v1/credit-notes/{credit_note_id}'}
