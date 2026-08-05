"""Tests for the /cash-sessions endpoints."""

from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import EntityStatus
from app.main import app
from app.schemas.cash_session import SessionState


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int = 7, cash_drawer_id: int | None = 5) -> None:
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


def _drawer(cash_drawer_id: int = 5) -> SimpleNamespace:
    return SimpleNamespace(
        cash_drawer_id=cash_drawer_id,
        facility=1,
        code='CAJA1',
        name='Caja 1',
        comment=None,
        status=EntityStatus.ACTIVE,
    )


def _employee(employee_id: int = 7, first_name: str = 'Ana') -> SimpleNamespace:
    return SimpleNamespace(
        employee_id=employee_id,
        first_name=first_name,
        last_name='Ruiz',
        nickname='ana',
        gender=2,
        birthday=date(1990, 4, 3),
        taxpayer_id=None,
        sales_person=False,
        status=EntityStatus.ACTIVE,
        personal_id=None,
        start_job_date=date(2020, 1, 15),
        enroll_number=None,
        comment=None,
    )


def _session(**overrides) -> SimpleNamespace:
    """A session as the service hands it over — FK details already expanded (#141)."""
    base = dict(
        cash_session_id=1,
        cash_drawer=5,
        cash_drawer_detail=_drawer(),
        cashier=7,
        cashier_detail=_employee(),
        start=datetime(2026, 7, 25, 9),
        end=None,
        cash_supervisor=None,
        cash_supervisor_detail=None,
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
async def test_closing_expands_the_supervisor_who_closed_it() -> None:
    """#141 — the detail view shows a supervisor name, not an id to resolve."""
    _auth()
    closed = _session(
        end=datetime(2026, 7, 25, 18),
        cash_supervisor=12,
        cash_supervisor_detail=_employee(employee_id=12, first_name='Luis'),
    )
    with patch(
        'app.services.cash_session_service.get_session', AsyncMock(return_value=_session())
    ), patch(
        'app.services.cash_session_service.close_session', AsyncMock(return_value=closed)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/cash-sessions/1/close', json={'counts': []})

    supervisor = response.json()['cash_supervisor']
    assert supervisor['employee_id'] == 12
    assert supervisor['first_name'] == 'Luis'


# ── Expanded foreign keys (#141) ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_expands_the_drawer_and_the_cashier() -> None:
    _auth()
    with patch(
        'app.services.cash_session_service.get_session', AsyncMock(return_value=_session())
    ), patch('app.services.cash_session_service.attach_derived', AsyncMock()):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/1')

    body = response.json()
    assert body['cash_drawer'] == {
        'cash_drawer_id': 5,
        'facility': 1,
        'code': 'CAJA1',
        'name': 'Caja 1',
        'comment': None,
        'status': 0,
    }
    assert body['cashier']['employee_id'] == 7
    assert body['cashier']['last_name'] == 'Ruiz'
    assert body['cash_supervisor'] is None


@pytest.mark.asyncio
async def test_list_expands_every_row() -> None:
    _auth()
    listing = AsyncMock(return_value=([_session(), _session(cash_session_id=2)], 2))
    with patch('app.services.cash_session_service.list_sessions', listing):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions')

    assert [row['cash_drawer']['name'] for row in response.json()['items']] == [
        'Caja 1',
        'Caja 1',
    ]


@pytest.mark.asyncio
async def test_get_unknown_session_is_404() -> None:
    _auth()
    with patch('app.services.cash_session_service.get_session', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND
