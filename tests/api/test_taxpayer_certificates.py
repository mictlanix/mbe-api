"""Tests for /taxpayer-certificates endpoints."""

from collections.abc import Generator
from datetime import datetime
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


def _certificate(certificate_id: str = '00001000000500003416') -> SimpleNamespace:
    """Carries the secret columns so the response schema is shown to drop them."""
    return SimpleNamespace(
        taxpayer_certificate_id=certificate_id,
        taxpayer='RFC123456789A',
        certificate_data=b'\x30\x82CERT',
        key_data=b'\x30\x82KEY',
        key_password=b'hunter2',
        valid_from=datetime(2024, 1, 1),
        valid_to=datetime(2028, 1, 1),
        status=0,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_taxpayer_certificates_returns_200() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_certificate_service.list_taxpayer_certificates',
        new=AsyncMock(return_value=([_certificate()], 1)),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-certificates')
    assert r.status_code == 200
    assert r.json()['items'][0]['taxpayer_certificate_id'] == '00001000000500003416'


@pytest.mark.asyncio
async def test_list_taxpayer_certificates_filters_passed_through() -> None:
    _auth()
    mock = AsyncMock(return_value=([_certificate()], 1))
    with patch('app.services.taxpayer_certificate_service.list_taxpayer_certificates', new=mock):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get(
                '/api/v1/taxpayer-certificates', params={'taxpayer': 'RFC123456789A', 'status': 0}
            )
    assert r.status_code == 200
    assert mock.call_args.kwargs['taxpayer'] == 'RFC123456789A'
    assert mock.call_args.kwargs['status'] == 0


@pytest.mark.asyncio
async def test_get_taxpayer_certificate_returns_200() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_certificate_service.get_taxpayer_certificate',
        new=AsyncMock(return_value=_certificate()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-certificates/00001000000500003416')
    assert r.status_code == 200
    assert r.json()['valid_to'].startswith('2028-01-01')


@pytest.mark.asyncio
async def test_certificate_response_never_exposes_the_key_material() -> None:
    """The whole point of the resource: metadata out, CSD binaries and password never."""
    _auth()
    with patch(
        'app.services.taxpayer_certificate_service.get_taxpayer_certificate',
        new=AsyncMock(return_value=_certificate()),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-certificates/00001000000500003416')
    body = r.json()
    assert set(body) == {
        'taxpayer_certificate_id',
        'taxpayer',
        'valid_from',
        'valid_to',
        'status',
    }
    assert 'hunter2' not in r.text


@pytest.mark.asyncio
async def test_get_taxpayer_certificate_returns_404() -> None:
    _auth()
    with patch(
        'app.services.taxpayer_certificate_service.get_taxpayer_certificate',
        new=AsyncMock(return_value=None),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
            r = await c.get('/api/v1/taxpayer-certificates/UNKNOWN')
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_taxpayer_certificates_requires_auth() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as c:
        r = await c.get('/api/v1/taxpayer-certificates')
    assert r.status_code == 401
