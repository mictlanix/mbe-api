"""Tests for the /sales-quotes endpoints."""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int | None = 7, point_sale_id: int | None = 3) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
        point_sale_id=point_sale_id,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _quote(**overrides) -> SimpleNamespace:
    base = dict(
        sales_quote_id=1,
        facility=1,
        serial=None,
        salesperson=7,
        customer=2,
        payment_terms=0,
        date=datetime(2026, 7, 25),
        due_date=datetime(2026, 8, 24),
        contact=None,
        ship_to=None,
        currency=0,
        exchange_rate=Decimal('1'),
        comment=None,
        status='draft',
        has_expired=False,
        lines=[],
        subtotal=Decimal('0.00'),
        tax_total=Decimal('0.00'),
        total=Decimal('0.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _order() -> SimpleNamespace:
    return SimpleNamespace(
        sales_order_id=5, facility=1, serial=None, point_sale=3, salesperson=7, customer=2,
        customer_name=None, sales_quote=1, payment_terms=0, date=datetime(2026, 7, 25),
        promise_date=datetime(2026, 8, 1), due_date=datetime(2026, 7, 25), contact=None,
        ship_to=None, recipient=None, recipient_name=None, currency=0,
        exchange_rate=Decimal('1'), priority=1, comment=None, status='draft', lines=[],
        subtotal=Decimal('0.00'), tax_total=Decimal('0.00'), total=Decimal('0.00'),
        balance=Decimal('0.00'),
    )


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/sales-quotes')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_convert_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post('/api/v1/sales-quotes/1/convert')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── CRUD ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_a_draft_with_no_folio() -> None:
    """FR-032 — a quote is numbered at confirmation, so a draft leaves no gap."""
    _auth()
    with patch(
        'app.services.sales_quote_service.create_quote', AsyncMock(return_value=_quote())
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes', json={})

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['serial'] is None
    assert response.json()['status'] == 'draft'


@pytest.mark.asyncio
async def test_get_returns_the_quote() -> None:
    _auth()
    quote = _quote(total=Decimal('116.00'))
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=quote)
    ), patch('app.services.sales_quote_service.attach_derived', AsyncMock(return_value=quote)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-quotes/1')

    assert response.json()['total'] == '116.00'


@pytest.mark.asyncio
async def test_unknown_quote_is_404() -> None:
    _auth()
    with patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-quotes/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_list_returns_paginated_envelope() -> None:
    _auth()
    with patch(
        'app.services.sales_quote_service.list_quotes', AsyncMock(return_value=([_quote()], 1))
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-quotes')

    assert response.json()['total'] == 1


@pytest.mark.asyncio
async def test_editing_a_confirmed_quote_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail='Document is completed'
    )
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch('app.services.sales_quote_service.update_quote', AsyncMock(side_effect=conflict)):
        async with await _client() as client:
            response = await client.put('/api/v1/sales-quotes/1', json={'comment': 'nope'})

    assert response.status_code == status.HTTP_409_CONFLICT


# ── Transitions ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_assigns_the_folio() -> None:
    _auth()
    confirmed = _quote(serial=77, status='completed')
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.confirm_quote', AsyncMock(return_value=confirmed)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/confirm')

    assert response.json()['serial'] == 77
    assert response.json()['status'] == 'completed'


@pytest.mark.asyncio
async def test_cancel_marks_the_quote_cancelled() -> None:
    _auth()
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.cancel_quote',
        AsyncMock(return_value=_quote(status='cancelled')),
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/cancel')

    assert response.json()['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_duplicate_returns_a_new_draft() -> None:
    _auth()
    copy = _quote(sales_quote_id=2)
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch('app.services.sales_quote_service.duplicate_quote', AsyncMock(return_value=copy)):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/duplicate')

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['sales_quote_id'] == 2
    assert response.json()['serial'] is None


# ── Conversion ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_convert_produces_an_order_referencing_the_quote() -> None:
    _auth()
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.convert_to_order', AsyncMock(return_value=_order())
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/convert')

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['sales_quote'] == 1
    assert body['status'] == 'draft'


@pytest.mark.asyncio
async def test_converting_an_expired_quote_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='Quote has expired and cannot be converted; duplicate it to re-quote',
    )
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.convert_to_order', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/convert')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'expired' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_converting_an_unconfirmed_quote_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='Only a confirmed quote can be converted; confirm it first',
    )
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.convert_to_order', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/convert')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'confirm' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_converting_a_cancelled_quote_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail='A cancelled quote cannot be converted'
    )
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch(
        'app.services.sales_quote_service.convert_to_order', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-quotes/1/convert')

    assert response.status_code == status.HTTP_409_CONFLICT


# ── Lines ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_line_accepts_a_price_adjustment() -> None:
    """The repo has `price_adjustment` only — no percentage increment (see Divergences)."""
    _auth()
    add = AsyncMock(return_value=_quote())
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch('app.services.sales_quote_service.add_line', add):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-quotes/1/lines',
                json={'product': 1, 'price_adjustment': '15.00'},
            )

    assert response.status_code == status.HTTP_200_OK
    assert add.await_args.args[2].price_adjustment == Decimal('15.00')


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    with patch(
        'app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())
    ), patch('app.services.sales_quote_service.get_line', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.delete('/api/v1/sales-quotes/1/lines/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_discount_above_one_is_rejected() -> None:
    _auth()
    with patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=_quote())):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-quotes/1/lines',
                json={'product': 1, 'discount_rate': '1.5'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
