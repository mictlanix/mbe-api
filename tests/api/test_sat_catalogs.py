"""Tests for /sat/* read-only catalog endpoints."""

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


def _uom(code: str = 'H87', name: str = 'Pieza') -> SimpleNamespace:
    return SimpleNamespace(sat_unit_of_measurement_id=code, name=name)


# ── List endpoint ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_sat_units_of_measurement_returns_200() -> None:
    _auth()
    with patch(
        'app.services.sat_catalog_service.list_sat',
        new=AsyncMock(return_value=([_uom()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/sat/units-of-measurement')
    assert r.status_code == 200
    body = r.json()
    assert body['total'] == 1
    assert body['items'][0]['id'] == 'H87'
    assert body['items'][0]['description'] == 'Pieza'


@pytest.mark.asyncio
async def test_list_sat_catalog_passes_search_to_service() -> None:
    _auth()
    mock_list = AsyncMock(return_value=([_uom()], 1))
    with patch('app.services.sat_catalog_service.list_sat', new=mock_list):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/sat/units-of-measurement', params={'search': 'pieza'})
    assert r.status_code == 200
    assert mock_list.call_args.kwargs['search'] == 'pieza'


@pytest.mark.asyncio
async def test_list_sat_catalog_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/sat/units-of-measurement')
    assert r.status_code == 401


# ── Get-by-id endpoint ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_sat_unit_of_measurement_returns_200() -> None:
    _auth()
    with patch(
        'app.services.sat_catalog_service.get_sat',
        new=AsyncMock(return_value=_uom('H87')),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/sat/units-of-measurement/H87')
    assert r.status_code == 200
    body = r.json()
    assert body['id'] == 'H87'
    assert body['description'] == 'Pieza'


@pytest.mark.asyncio
async def test_get_sat_unit_of_measurement_returns_404() -> None:
    _auth()
    with patch(
        'app.services.sat_catalog_service.get_sat',
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/sat/units-of-measurement/XXX')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_sat_catalog_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/sat/units-of-measurement/H87')
    assert r.status_code == 401


# ── Write operations return 405 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_sat_catalog_returns_405() -> None:
    _auth()
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.post('/api/v1/sat/units-of-measurement', json={'id': 'ZZZ'})
    assert r.status_code == 405
