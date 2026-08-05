"""Tests for the /api/v1/contacts endpoint (#133).

`sales_order.contact` and `delivery_order.contact` have accepted ids from this table all along;
until now nothing produced one, so a client was asked for an id it could not obtain or create.
"""

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
        user_id='tester', session_version=1, administrator=True, facility_id=None, employee_id=7
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _contact(contact_id: int = 1, **overrides) -> SimpleNamespace:
    base = dict(
        contact_id=contact_id,
        name='Juan Pérez',
        job_title='Almacén',
        phone='5551234567',
        phone_ext=None,
        mobile='5559876543',
        fax=None,
        website=None,
        email='juan@example.com',
        im=None,
        sip=None,
        birthday=None,
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Contact tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as c:
        r = await c.get('/api/v1/contacts')

    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_returns_paginated_envelope() -> None:
    _auth()
    with patch(
        'app.services.contact_service.list_contacts',
        AsyncMock(return_value=([_contact()], 1)),
    ):
        async with await _client() as c:
            r = await c.get('/api/v1/contacts')

    assert r.status_code == 200
    assert r.json()['total'] == 1
    assert r.json()['items'][0]['name'] == 'Juan Pérez'


@pytest.mark.asyncio
async def test_list_passes_the_search_term_through() -> None:
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.contact_service.list_contacts', listing):
        async with await _client() as c:
            await c.get('/api/v1/contacts?search=perez')

    assert listing.await_args.kwargs['search'] == 'perez'


@pytest.mark.asyncio
async def test_create_returns_201() -> None:
    _auth()
    with patch(
        'app.services.contact_service.create_contact', AsyncMock(return_value=_contact())
    ):
        async with await _client() as c:
            r = await c.post('/api/v1/contacts', json={'name': 'Juan Pérez'})

    assert r.status_code == 201
    assert r.json()['contact_id'] == 1


@pytest.mark.asyncio
async def test_create_defaults_mobile_to_empty_rather_than_null() -> None:
    """`contact.mobile` is NOT NULL DEFAULT '' — an omitted mobile must not become NULL."""
    _auth()
    creating = AsyncMock(return_value=_contact(mobile=''))
    with patch('app.services.contact_service.create_contact', creating):
        async with await _client() as c:
            r = await c.post('/api/v1/contacts', json={'name': 'Juan Pérez'})

    assert r.status_code == 201
    assert creating.await_args.args[1].mobile == ''


@pytest.mark.asyncio
async def test_create_rejects_a_blank_name() -> None:
    _auth()
    async with await _client() as c:
        r = await c.post('/api/v1/contacts', json={'name': '   '})

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_get_returns_the_contact() -> None:
    _auth()
    with patch('app.services.contact_service.get_contact', AsyncMock(return_value=_contact())):
        async with await _client() as c:
            r = await c.get('/api/v1/contacts/1')

    assert r.status_code == 200
    assert r.json()['email'] == 'juan@example.com'


@pytest.mark.asyncio
async def test_get_returns_404_for_an_unknown_contact() -> None:
    _auth()
    with patch('app.services.contact_service.get_contact', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.get('/api/v1/contacts/999')

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_returns_the_changed_contact() -> None:
    _auth()
    with patch(
        'app.services.contact_service.get_contact', AsyncMock(return_value=_contact())
    ), patch(
        'app.services.contact_service.update_contact',
        AsyncMock(return_value=_contact(name='Juana Pérez')),
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/contacts/1', json={'name': 'Juana Pérez'})

    assert r.status_code == 200
    assert r.json()['name'] == 'Juana Pérez'


@pytest.mark.asyncio
async def test_update_returns_404_for_an_unknown_contact() -> None:
    _auth()
    with patch('app.services.contact_service.get_contact', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.put('/api/v1/contacts/999', json={'name': 'X'})

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_returns_204() -> None:
    _auth()
    with patch(
        'app.services.contact_service.get_contact', AsyncMock(return_value=_contact())
    ), patch('app.services.contact_service.delete_contact', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.delete('/api/v1/contacts/1')

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_returns_404_for_an_unknown_contact() -> None:
    _auth()
    with patch('app.services.contact_service.get_contact', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.delete('/api/v1/contacts/999')

    assert r.status_code == 404
