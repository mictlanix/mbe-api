"""Tests for the /api/v1/bank-accounts endpoint (#13).

`supplier_bank_account` has linked suppliers to bank accounts all along, but nothing produced a
bank account, so a client was asked for ids it had no way to obtain or create.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=None,
        employee_id=7,
    )

    async def _no_privileges_db():
        yield SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
            )
        )

    app.dependency_overrides[get_db] = _no_privileges_db


def _account(bank_account_id: int = 1, **overrides) -> SimpleNamespace:
    base = dict(
        bank_account_id=bank_account_id,
        bank_name='BBVA',
        account_number='0123456789',
        reference=None,
        routing_number=None,
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_requests_without_a_token_are_refused() -> None:
    async with await _client() as c:
        r = await c.get('/api/v1/bank-accounts')

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_a_user_without_the_privilege_is_forbidden() -> None:
    _auth(administrator=False)
    async with await _client() as c:
        r = await c.get('/api/v1/bank-accounts')

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_returns_the_envelope_and_passes_the_paging_through() -> None:
    _auth()
    listing = AsyncMock(return_value=([_account()], 1))
    with patch('app.services.bank_account_service.list_bank_accounts', listing):
        async with await _client() as c:
            r = await c.get('/api/v1/bank-accounts?search=bbva&skip=5&limit=10')

    assert r.status_code == 200
    assert r.json()['total'] == 1
    assert r.json()['items'][0]['bank_name'] == 'BBVA'
    assert listing.await_args.kwargs == {'search': 'bbva', 'skip': 5, 'limit': 10}


@pytest.mark.asyncio
async def test_create_returns_201() -> None:
    _auth()
    with patch(
        'app.services.bank_account_service.create_bank_account',
        AsyncMock(return_value=_account()),
    ):
        async with await _client() as c:
            r = await c.post(
                '/api/v1/bank-accounts',
                json={'bank_name': 'BBVA', 'account_number': '0123456789'},
            )

    assert r.status_code == 201
    assert r.json()['bank_account_id'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body', [{'account_number': '0123456789'}, {'bank_name': 'BBVA'}], ids=['no_name', 'no_number']
)
async def test_create_requires_a_bank_name_and_an_account_number(body: dict) -> None:
    _auth()
    async with await _client() as c:
        r = await c.post('/api/v1/bank-accounts', json=body)

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_returns_the_account() -> None:
    _auth()
    with patch(
        'app.services.bank_account_service.get_bank_account', AsyncMock(return_value=_account())
    ):
        async with await _client() as c:
            r = await c.get('/api/v1/bank-accounts/1')

    assert r.status_code == 200
    assert r.json()['account_number'] == '0123456789'


@pytest.mark.asyncio
async def test_get_returns_404_for_an_unknown_account() -> None:
    _auth()
    with patch(
        'app.services.bank_account_service.get_bank_account', AsyncMock(return_value=None)
    ):
        async with await _client() as c:
            r = await c.get('/api/v1/bank-accounts/999')

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_returns_the_changed_account() -> None:
    _auth()
    with (
        patch(
            'app.services.bank_account_service.get_bank_account',
            AsyncMock(return_value=_account()),
        ),
        patch(
            'app.services.bank_account_service.update_bank_account',
            AsyncMock(return_value=_account(bank_name='Banorte')),
        ),
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/bank-accounts/1', json={'bank_name': 'Banorte'})

    assert r.status_code == 200
    assert r.json()['bank_name'] == 'Banorte'


@pytest.mark.asyncio
async def test_update_returns_404_for_an_unknown_account() -> None:
    _auth()
    with patch(
        'app.services.bank_account_service.get_bank_account', AsyncMock(return_value=None)
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/bank-accounts/999', json={'bank_name': 'X'})

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_returns_204() -> None:
    _auth()
    with (
        patch(
            'app.services.bank_account_service.get_bank_account',
            AsyncMock(return_value=_account()),
        ),
        patch(
            'app.services.bank_account_service.delete_bank_account',
            AsyncMock(return_value=None),
        ),
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/bank-accounts/1')

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_returns_409_while_the_account_is_linked() -> None:
    _auth()
    conflict = HTTPException(status_code=409, detail='Still referenced by supplier (1)')
    with (
        patch(
            'app.services.bank_account_service.get_bank_account',
            AsyncMock(return_value=_account()),
        ),
        patch(
            'app.services.bank_account_service.delete_bank_account', AsyncMock(side_effect=conflict)
        ),
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/bank-accounts/1')

    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_returns_404_for_an_unknown_account() -> None:
    _auth()
    with patch(
        'app.services.bank_account_service.get_bank_account', AsyncMock(return_value=None)
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/bank-accounts/999')

    assert r.status_code == 404
