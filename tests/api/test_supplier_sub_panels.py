"""Tests for the supplier sub-panels: links, bank accounts and agreements (#13)."""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
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


def _supplier(**overrides) -> SimpleNamespace:
    base = dict(
        supplier_id=1,
        code='SUP1',
        name='Supplier Co',
        zone=None,
        credit_limit=Decimal('0'),
        credit_days=0,
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _agreement(**overrides) -> SimpleNamespace:
    base = dict(
        supplier_agreement_id=5,
        supplier=1,
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _detail() -> SimpleNamespace:
    return _supplier(
        addresses=[],
        contacts=[
            SimpleNamespace(
                contact_id=3,
                name='Juan Pérez',
                job_title=None,
                phone=None,
                phone_ext=None,
                mobile='',
                fax=None,
                website=None,
                email=None,
                im=None,
                sip=None,
                birthday=None,
                comment=None,
            )
        ],
        bank_accounts=[
            SimpleNamespace(
                bank_account_id=2,
                bank_name='BBVA',
                account_number='0123456789',
                reference=None,
                routing_number=None,
                comment=None,
            )
        ],
        agreements=[_agreement()],
    )


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


_SUPPLIER = 'app.services.supplier_service.'
_AGREEMENT = 'app.services.supplier_agreement_service.'

# ── Detail and list ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_detail_carries_the_four_sub_panels() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier_detail', AsyncMock(return_value=_detail())):
        async with await _client() as c:
            r = await c.get('/api/v1/suppliers/1')

    body = r.json()
    assert r.status_code == 200
    assert body['addresses'] == []
    assert body['contacts'][0]['contact_id'] == 3
    assert body['bank_accounts'][0]['bank_name'] == 'BBVA'
    assert body['agreements'][0]['supplier_agreement_id'] == 5


@pytest.mark.asyncio
async def test_the_detail_returns_404_for_an_unknown_supplier() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier_detail', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.get('/api/v1/suppliers/999')

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_a_list_row_has_none_of_the_sub_panels() -> None:
    _auth()
    with patch(_SUPPLIER + 'list_suppliers', AsyncMock(return_value=([_supplier()], 1))):
        async with await _client() as c:
            r = await c.get('/api/v1/suppliers')

    row = r.json()['items'][0]
    for key in ('addresses', 'contacts', 'bank_accounts', 'agreements'):
        assert key not in row


@pytest.mark.asyncio
async def test_post_and_put_return_the_detail_shape() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'create_supplier', AsyncMock(return_value=_detail())),
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_SUPPLIER + 'update_supplier', AsyncMock(return_value=_detail())),
    ):
        async with await _client() as c:
            created = await c.post('/api/v1/suppliers', json={'code': 'S', 'name': 'S'})
            updated = await c.put('/api/v1/suppliers/1', json={'name': 'S'})

    for r in (created, updated):
        assert r.json()['bank_accounts'][0]['bank_account_id'] == 2
        assert r.json()['agreements'][0]['supplier'] == 1


# ── Links ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_omitted_links_reach_the_service_as_none() -> None:
    _auth()
    creating = AsyncMock(return_value=_detail())
    with patch(_SUPPLIER + 'create_supplier', creating):
        async with await _client() as c:
            await c.post('/api/v1/suppliers', json={'code': 'S', 'name': 'S'})

    data = creating.await_args.args[1]
    assert (data.addresses, data.contacts, data.bank_accounts) == (None, None, None)


@pytest.mark.asyncio
async def test_link_lists_are_passed_through_on_post() -> None:
    _auth()
    creating = AsyncMock(return_value=_detail())
    with patch(_SUPPLIER + 'create_supplier', creating):
        async with await _client() as c:
            await c.post(
                '/api/v1/suppliers',
                json={
                    'code': 'S',
                    'name': 'S',
                    'addresses': [1, 2],
                    'contacts': [],
                    'bank_accounts': [4],
                },
            )

    data = creating.await_args.args[1]
    assert (data.addresses, data.contacts, data.bank_accounts) == ([1, 2], [], [4])


@pytest.mark.asyncio
async def test_link_lists_are_passed_through_on_put() -> None:
    _auth()
    updating = AsyncMock(return_value=_detail())
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_SUPPLIER + 'update_supplier', updating),
    ):
        async with await _client() as c:
            await c.put('/api/v1/suppliers/1', json={'contacts': [], 'addresses': [9]})

    data = updating.await_args.args[2]
    assert (data.addresses, data.contacts, data.bank_accounts) == ([9], [], None)


# ── Caller ────────────────────────────────────────────────────────────────────
# Which panels the caller reads is decided in the service, and tested against real rows in
# tests/integration/test_supplier_panels_flow.py; here only that the caller is handed over.


@pytest.mark.asyncio
async def test_the_caller_reaches_the_service_on_get_post_and_put() -> None:
    _auth()
    reading = AsyncMock(return_value=_detail())
    creating = AsyncMock(return_value=_detail())
    updating = AsyncMock(return_value=_detail())
    with (
        patch(_SUPPLIER + 'get_supplier_detail', reading),
        patch(_SUPPLIER + 'create_supplier', creating),
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_SUPPLIER + 'update_supplier', updating),
    ):
        async with await _client() as c:
            await c.get('/api/v1/suppliers/1')
            await c.post('/api/v1/suppliers', json={'code': 'S', 'name': 'S'})
            await c.put('/api/v1/suppliers/1', json={'name': 'S'})

    for call in (reading, creating, updating):
        assert call.await_args.args[-1].user_id == 'tester'


@pytest.mark.asyncio
async def test_a_user_without_suppliers_read_still_cannot_use_the_agreements_route() -> None:
    """The panel rule narrows the detail; the nested route keeps its own gate."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=False, facility_id=None, employee_id=7
    )

    async def _db_without_privileges():
        yield SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))
        )

    app.dependency_overrides[get_db] = _db_without_privileges
    async with await _client() as c:
        r = await c.get('/api/v1/suppliers/1/agreements')

    assert r.status_code == 403


# ── Agreements ────────────────────────────────────────────────────────────────

_PERIOD = {'start': '2026-01-01', 'end': '2026-12-31'}


@pytest.mark.asyncio
async def test_list_agreements_returns_a_plain_list() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'list_agreements', AsyncMock(return_value=[_agreement()])),
    ):
        async with await _client() as c:
            r = await c.get('/api/v1/suppliers/1/agreements')

    assert r.status_code == 200
    assert r.json()[0]['supplier_agreement_id'] == 5


@pytest.mark.asyncio
async def test_list_agreements_returns_404_for_an_unknown_supplier() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.get('/api/v1/suppliers/999/agreements')

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_create_agreement_returns_201_for_the_supplier_in_the_path() -> None:
    _auth()
    creating = AsyncMock(return_value=_agreement())
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'create_agreement', creating),
    ):
        async with await _client() as c:
            r = await c.post('/api/v1/suppliers/1/agreements', json=_PERIOD)

    assert r.status_code == 201
    assert creating.await_args.args[1] == 1


@pytest.mark.asyncio
async def test_create_agreement_refuses_an_end_before_the_start() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())):
        async with await _client() as c:
            r = await c.post(
                '/api/v1/suppliers/1/agreements', json={'start': '2026-02-01', 'end': '2026-01-01'}
            )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_create_agreement_returns_404_for_an_unknown_supplier() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.post('/api/v1/suppliers/999/agreements', json=_PERIOD)

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_update_agreement_returns_the_changed_agreement() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'get_agreement', AsyncMock(return_value=_agreement())),
        patch(
            _AGREEMENT + 'update_agreement',
            AsyncMock(return_value=_agreement(comment='renewed')),
        ),
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/suppliers/1/agreements/5', json={'comment': 'renewed'})

    assert r.status_code == 200
    assert r.json()['comment'] == 'renewed'


@pytest.mark.asyncio
async def test_update_agreement_returns_404_for_an_unknown_or_foreign_agreement() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'get_agreement', AsyncMock(return_value=None)),
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/suppliers/1/agreements/999', json={'comment': 'x'})

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_update_agreement_returns_404_for_an_unknown_supplier() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.put('/api/v1/suppliers/999/agreements/5', json={'comment': 'x'})

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_delete_agreement_returns_204() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'get_agreement', AsyncMock(return_value=_agreement())),
        patch(_AGREEMENT + 'delete_agreement', AsyncMock(return_value=None)),
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/suppliers/1/agreements/5')

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_agreement_returns_404_for_an_unknown_or_foreign_agreement() -> None:
    _auth()
    with (
        patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=_supplier())),
        patch(_AGREEMENT + 'get_agreement', AsyncMock(return_value=None)),
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/suppliers/1/agreements/999')

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


@pytest.mark.asyncio
async def test_delete_agreement_returns_404_for_an_unknown_supplier() -> None:
    _auth()
    with patch(_SUPPLIER + 'get_supplier', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.delete('/api/v1/suppliers/999/agreements/5')

    assert r.status_code == 404
    assert r.json()['code'] == 'resource.not_found'


# ── Auth ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('get', '/api/v1/suppliers/1/agreements'),
        ('post', '/api/v1/suppliers/1/agreements'),
        ('put', '/api/v1/suppliers/1/agreements/5'),
        ('delete', '/api/v1/suppliers/1/agreements/5'),
    ],
)
async def test_the_agreement_routes_require_a_token(method: str, path: str) -> None:
    async with await _client() as c:
        r = await getattr(c, method)(path)

    assert r.status_code == 401
