"""The commission period routes (spec 025): the HTTP contract, with the service patched."""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services import commission_service

BASE = '/api/v1/commissions/periods'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _deny_privileges() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


_LINE = SimpleNamespace(
    scheme=2,
    movement=0,
    sales_order=336924,
    sales_order_detail=1,
    customer_refund_detail=None,
    order_date=datetime(2026, 9, 2),
    paid_date=datetime(2026, 9, 10),
    customer=9149,
    customer_name='Constructora',
    product=12,
    product_name='Concreto',
    label=1,
    label_name='CONCRETOS',
    label_rate=Decimal('0.025'),
    salesperson=17,
    order_salesperson=17,
    customer_salesperson=17,
    participation=1,
    participation_name='CLIENTE VITALICIO',
    participation_rate=Decimal('1'),
    rate=Decimal('0.025'),
    price=Decimal('2143.10'),
    quantity=Decimal('7'),
    total=Decimal('15001.70'),
    full_commission=Decimal('375.04'),
    share=Decimal('0.4'),
    commission=Decimal('150.02'),
)
_VIEW = commission_service.PeriodView(
    year=2026,
    month=9,
    status='closed',
    final=True,
    closed_by=7,
    closed_at=datetime(2026, 10, 2, 10, 0),
    reopened_by=None,
    reopened_at=None,
    paid_by=None,
    paid_at=None,
    lines=[_LINE],
    totals=[commission_service.Total(17, 1, 'CONCRETOS', 1, Decimal('150.02'))],
)


@pytest.mark.asyncio
async def test_a_month_is_read_with_its_lines_and_totals() -> None:
    _auth()
    with patch.object(commission_service, 'read_period', AsyncMock(return_value=_VIEW)) as read:
        async with _client() as client:
            response = await client.get(f'{BASE}/2026/9', params={'salesperson': 17})

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['status'] == 'closed' and body['final'] is True
    assert body['lines'][0]['movement'] == 'sale'
    assert body['lines'][0]['commission'] == '150.02'
    assert body['totals'] == [
        {
            'salesperson': 17,
            'label': 1,
            'label_name': 'CONCRETOS',
            'lines': 1,
            'commission': '150.02',
        }
    ]
    assert read.await_args.kwargs['salesperson'] == 17


@pytest.mark.asyncio
async def test_a_month_out_of_range_is_422() -> None:
    _auth()
    async with _client() as client:
        response = await client.get(f'{BASE}/2026/13')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['close', 'reopen', 'pay'])
async def test_each_action_returns_the_period(action: str) -> None:
    _auth()
    period = SimpleNamespace(
        year=2026,
        month=9,
        status=1 if action == 'close' else (0 if action == 'reopen' else 2),
        closed_by=7,
        closed_at=datetime(2026, 10, 2),
        reopened_by=None,
        reopened_at=None,
        paid_by=None,
        paid_at=None,
    )
    with patch.object(commission_service, action, AsyncMock(return_value=period)):
        async with _client() as client:
            response = await client.post(f'{BASE}/2026/9/{action}')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['year'] == 2026


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'detail'),
    [
        ('close', 'Period has not ended'),
        ('close', 'The previous period is not closed'),
        ('reopen', 'A later period is closed'),
        ('pay', 'Close the period first'),
    ],
)
async def test_refusals_pass_through_as_409(action: str, detail: str) -> None:
    _auth()
    refusal = AsyncMock(side_effect=HTTPException(status_code=409, detail=detail))
    with patch.object(commission_service, action, refusal):
        async with _client() as client:
            response = await client.post(f'{BASE}/2026/9/{action}')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': detail}


@pytest.mark.asyncio
async def test_without_a_token_it_is_401() -> None:
    async with _client() as client:
        response = await client.get(f'{BASE}/2026/9')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('get', '/2026/9'),
        ('post', '/2026/9/close'),
        ('post', '/2026/9/reopen'),
        ('post', '/2026/9/pay'),
    ],
)
async def test_without_the_commissions_privilege_it_is_403(method: str, path: str) -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with _client() as client:
        response = await getattr(client, method)(f'{BASE}{path}')

    assert response.status_code == status.HTTP_403_FORBIDDEN
