"""Tests for /taxpayer-issuers endpoints."""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _issuer(rfc: str = 'RFC123456789A') -> SimpleNamespace:
    return SimpleNamespace(
        taxpayer_issuer_id=rfc,
        name='Acme SA de CV',
        regime={'id': '601', 'description': 'General de Ley'},
        postal_code={'id': '06000'},
        comment=None,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_taxpayer_issuers_returns_200() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_issuer_service.list_taxpayer_issuers',
        new=AsyncMock(return_value=([_issuer()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-issuers')
    assert r.status_code == 200
    assert r.json()['items'][0]['name'] == 'Acme SA de CV'


@pytest.mark.asyncio
async def test_list_taxpayer_issuers_search_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_issuer()], 1))
    with patch('app.services.taxpayer_issuer_service.list_taxpayer_issuers', new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-issuers', params={'search': 'acme'})
    assert r.status_code == 200
    assert mock.call_args.kwargs['search'] == 'acme'


@pytest.mark.asyncio
async def test_get_taxpayer_issuer_returns_200() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_issuer_service.get_taxpayer_issuer',
        new=AsyncMock(return_value=_issuer()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-issuers/RFC123456789A')
    assert r.status_code == 200
    assert r.json()['taxpayer_issuer_id'] == 'RFC123456789A'
    assert r.json()['regime']['description'] == 'General de Ley'


@pytest.mark.asyncio
async def test_get_taxpayer_issuer_returns_404() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_issuer_service.get_taxpayer_issuer',
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-issuers/RFCNOTFOUND')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_taxpayer_issuers_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/taxpayer-issuers')
    assert r.status_code == 401
