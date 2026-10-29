"""Tests for the taxpayer batch (series) routes (#44, spec 027).

The HTTP contract only: the issuer and batch services are patched. The rules (duplicate series,
in-use series) are covered in the service's own tests.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from app.main import app
from app.schemas.fiscal import BatchTemplate, TaxpayerBatchCreate, TaxpayerBatchResponse
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges

_BATCHES = 'app.services.taxpayer_batch_service'
_ISSUERS = 'app.services.taxpayer_issuer_service'
_BASE = '/api/v1/taxpayer-issuers/CAG190523ES5/batches'
_BODY = {'batch': 'GR', 'type': 0, 'template': {'name': 'Factura'}}

# key: (method, path, body, success status)
_ROUTES = {
    'list': ('GET', _BASE, None, status.HTTP_200_OK),
    'create': ('POST', _BASE, _BODY, status.HTTP_201_CREATED),
    'update': ('PUT', f'{_BASE}/3', _BODY, status.HTTP_200_OK),
    'delete': ('DELETE', f'{_BASE}/3', None, status.HTTP_204_NO_CONTENT),
}


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _batch(**overrides) -> TaxpayerBatchResponse:
    base = dict(
        taxpayer_batch_id=3,
        taxpayer='CAG190523ES5',
        batch='GR',
        type=0,
        template=BatchTemplate(name='Factura'),
        in_use=False,
    )
    base.update(overrides)
    return TaxpayerBatchResponse(**base)


async def _call(client: AsyncClient, key: str):
    method, path, body, _ = _ROUTES[key]
    return await client.request(method, path, json=body)


# ── Success ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_the_issuers_batches() -> None:
    _auth()
    batches = [_batch(), _batch(taxpayer_batch_id=4, batch='NC', type=100, in_use=True)]
    with (
        patch(f'{_ISSUERS}.get_taxpayer_issuer', AsyncMock(return_value=SimpleNamespace())),
        patch(f'{_BATCHES}.list_batches', AsyncMock(return_value=batches)) as listed,
    ):
        async with await _client() as client:
            response = await client.get(_BASE)

    assert response.status_code == status.HTTP_200_OK
    assert [(b['taxpayer_batch_id'], b['batch'], b['in_use']) for b in response.json()] == [
        (3, 'GR', False),
        (4, 'NC', True),
    ]
    assert listed.await_args.args[1] == 'CAG190523ES5'


@pytest.mark.asyncio
async def test_create_returns_the_new_batch() -> None:
    _auth()
    with patch(f'{_BATCHES}.create_batch', AsyncMock(return_value=_batch())) as create:
        async with await _client() as client:
            response = await client.post(_BASE, json=_BODY)

    assert response.status_code == status.HTTP_201_CREATED
    assert response.json()['taxpayer_batch_id'] == 3
    assert response.json()['template']['name'] == 'Factura'
    args = create.await_args.args
    assert args[1] == 'CAG190523ES5'
    assert args[2] == TaxpayerBatchCreate(
        batch='GR', type=0, template=BatchTemplate(name='Factura')
    )


@pytest.mark.asyncio
async def test_update_returns_the_changed_batch() -> None:
    _auth()
    existing = SimpleNamespace(taxpayer_batch_id=3)
    with (
        patch(f'{_BATCHES}.get_batch', AsyncMock(return_value=existing)) as load,
        patch(
            f'{_BATCHES}.update_batch', AsyncMock(return_value=_batch(batch='GX', in_use=True))
        ) as update,
    ):
        async with await _client() as client:
            response = await client.put(f'{_BASE}/3', json={**_BODY, 'batch': 'GX'})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['batch'] == 'GX'
    assert load.await_args.args[1:] == ('CAG190523ES5', 3)
    args = update.await_args.args
    assert args[1] is existing
    assert args[2].batch == 'GX'


@pytest.mark.asyncio
async def test_delete_returns_no_content() -> None:
    _auth()
    existing = SimpleNamespace(taxpayer_batch_id=3)
    with (
        patch(f'{_BATCHES}.get_batch', AsyncMock(return_value=existing)),
        patch(f'{_BATCHES}.delete_batch', AsyncMock()) as delete,
    ):
        async with await _client() as client:
            response = await client.delete(f'{_BASE}/3')

    assert response.status_code == status.HTTP_204_NO_CONTENT
    assert response.content == b''
    assert delete.await_args.args[1] is existing


# ── Every route ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_every_route_requires_authentication(key: str) -> None:
    async with await _client() as client:
        response = await _call(client, key)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_every_route_requires_the_taxpayers_privilege(key: str) -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        response = await _call(client, key)

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── 404 ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_of_an_unknown_issuer_is_404() -> None:
    _auth()
    with (
        patch(f'{_ISSUERS}.get_taxpayer_issuer', AsyncMock(return_value=None)),
        patch(f'{_BATCHES}.list_batches', AsyncMock()) as listed,
    ):
        async with await _client() as client:
            response = await client.get(_BASE)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Taxpayer issuer not found'
    listed.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['update', 'delete'])
async def test_an_unknown_batch_is_404(key: str) -> None:
    _auth()
    with (
        patch(f'{_BATCHES}.get_batch', AsyncMock(return_value=None)),
        patch(f'{_BATCHES}.update_batch', AsyncMock()) as update,
        patch(f'{_BATCHES}.delete_batch', AsyncMock()) as delete,
    ):
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Batch not found'
    update.assert_not_awaited()
    delete.assert_not_awaited()


# ── 409 from the service ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('key', 'service', 'detail'),
    [
        ('create', 'create_batch', 'The issuer already has this series'),
        ('update', 'update_batch', 'The series is in use: only its template can change'),
        ('delete', 'delete_batch', 'The series is in use and cannot be deleted'),
    ],
)
async def test_a_service_conflict_is_returned_as_is(key: str, service: str, detail: str) -> None:
    _auth()
    refusal = HTTPException(status.HTTP_409_CONFLICT, detail)
    with (
        patch(f'{_BATCHES}.get_batch', AsyncMock(return_value=SimpleNamespace())),
        patch(f'{_BATCHES}.{service}', AsyncMock(side_effect=refusal)),
    ):
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == detail


# ── 422 from the body ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {**_BODY, 'batch': 'ABCDEFGHIJK'},
        {**_BODY, 'type': 4},
        {**_BODY, 'template': {}},
    ],
    ids=['series-too-long', 'unknown-type', 'template-without-name'],
)
@pytest.mark.parametrize('key', ['create', 'update'])
async def test_an_invalid_body_is_422(key: str, body: dict) -> None:
    _auth()
    method, path, _, _ = _ROUTES[key]
    with (
        patch(f'{_BATCHES}.get_batch', AsyncMock(return_value=SimpleNamespace())),
        patch(f'{_BATCHES}.create_batch', AsyncMock()) as create,
        patch(f'{_BATCHES}.update_batch', AsyncMock()) as update,
    ):
        async with await _client() as client:
            response = await client.request(method, path, json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    create.assert_not_awaited()
    update.assert_not_awaited()
