"""Tests for the /cash-sessions endpoints."""

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
from app.schemas.cash_session import SessionState


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int | None = 7, cash_drawer_id: int | None = 5) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
        cash_drawer_id=cash_drawer_id,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _session(**overrides) -> SimpleNamespace:
    base = dict(
        cash_session_id=1,
        cash_drawer=5,
        cashier=7,
        start=datetime(2026, 7, 25, 9),
        end=None,
        cash_supervisor=None,
        opening_amount=Decimal('500.00'),
        payments_by_method=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_current_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/cash-sessions/current')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_open_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post('/api/v1/cash-sessions', json={})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Opening ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_a_session() -> None:
    _auth()
    with patch(
        'app.services.cash_session_service.open_session', AsyncMock(return_value=_session())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/cash-sessions', json={'opening_amount': '500.00'}
            )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['opening_amount'] == '500.00'
    assert body['end'] is None


@pytest.mark.asyncio
async def test_second_session_on_the_same_drawer_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='That cash drawer already has an open session',
    )
    with patch(
        'app.services.cash_session_service.open_session', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/cash-sessions', json={})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'drawer' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_second_session_for_the_same_cashier_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='You already have an open session; close it before opening another',
    )
    with patch(
        'app.services.cash_session_service.open_session', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/cash-sessions', json={})

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_negative_opening_amount_is_rejected() -> None:
    _auth()
    async with await _client() as client:
        response = await client.post('/api/v1/cash-sessions', json={'opening_amount': '-1'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Current session, three states ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_current_reports_no_session() -> None:
    _auth()
    with patch(
        'app.services.cash_session_service.current_session',
        AsyncMock(return_value=(SessionState.NONE, None)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/current')

    assert response.json()['state'] == 'none'
    assert response.json()['session'] is None


@pytest.mark.asyncio
async def test_current_reports_an_open_session_with_its_payments() -> None:
    _auth()
    session = _session(payments_by_method=[{'method': 1, 'total': Decimal('1200.00')}])
    with patch(
        'app.services.cash_session_service.current_session',
        AsyncMock(return_value=(SessionState.OPEN, session)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/current')

    body = response.json()
    assert body['state'] == 'open'
    assert body['session']['payments_by_method'][0]['total'] == '1200.00'


@pytest.mark.asyncio
async def test_current_reports_a_stale_session_distinguishably() -> None:
    """FR-053 — 'left open from yesterday' is not the same as 'none'."""
    _auth()
    session = _session(start=datetime(2026, 7, 24, 22))
    with patch(
        'app.services.cash_session_service.current_session',
        AsyncMock(return_value=(SessionState.STALE, session)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/current')

    body = response.json()
    assert body['state'] == 'stale'
    assert body['session'] is not None


# ── Closing ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_stores_counts_and_ends_the_session() -> None:
    _auth()
    closed = _session(end=datetime(2026, 7, 25, 18), cash_supervisor=7)
    with patch(
        'app.services.cash_session_service.get_session', AsyncMock(return_value=_session())
    ), patch(
        'app.services.cash_session_service.close_session', AsyncMock(return_value=closed)
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/cash-sessions/1/close',
                json={'counts': [{'denomination': '500', 'quantity': 3}]},
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['end'] is not None


@pytest.mark.asyncio
async def test_closing_an_already_closed_session_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail='Session is already closed'
    )
    with patch(
        'app.services.cash_session_service.get_session', AsyncMock(return_value=_session())
    ), patch(
        'app.services.cash_session_service.close_session', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/cash-sessions/1/close', json={'counts': []})

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_closing_an_unknown_session_is_404() -> None:
    _auth()
    with patch('app.services.cash_session_service.get_session', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.post('/api/v1/cash-sessions/999/close', json={'counts': []})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_unknown_session_is_404() -> None:
    _auth()
    with patch('app.services.cash_session_service.get_session', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND
