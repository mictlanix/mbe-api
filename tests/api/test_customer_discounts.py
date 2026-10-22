"""Tests for `/customers/{customer_id}/discounts` (#15)."""

from collections.abc import Generator
from decimal import Decimal
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


def _auth() -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None, employee_id=7
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _discount(**overrides) -> SimpleNamespace:
    base = dict(
        customer_discount_id=5,
        customer=1,
        product=SimpleNamespace(product_id=9, code='P9', name='Producto Nueve'),
        discount=Decimal('0.10000000'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


_CUSTOMER = 'app.services.customer_service.'
_DISCOUNT = 'app.services.customer_discount_service.'


def _customer(found: bool = True) -> patch:
    return patch(_CUSTOMER + 'get_customer', AsyncMock(return_value=object() if found else None))


# ── List ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_a_plain_list_with_the_product_summary() -> None:
    _auth()
    with (
        _customer(),
        patch(_DISCOUNT + 'list_discounts', AsyncMock(return_value=[_discount()])),
    ):
        async with await _client() as c:
            r = await c.get('/api/v1/customers/1/discounts')

    assert r.status_code == 200
    assert r.json()[0]['customer_discount_id'] == 5
    assert r.json()[0]['product'] == {'product_id': 9, 'code': 'P9', 'name': 'Producto Nueve'}
    assert Decimal(str(r.json()[0]['discount'])) == Decimal('0.1')


@pytest.mark.asyncio
async def test_list_returns_404_for_an_unknown_customer() -> None:
    _auth()
    with _customer(False):
        async with await _client() as c:
            r = await c.get('/api/v1/customers/999/discounts')

    assert r.status_code == 404


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_201_for_the_customer_in_the_path() -> None:
    _auth()
    creating = AsyncMock(return_value=_discount())
    with _customer(), patch(_DISCOUNT + 'create_discount', creating):
        async with await _client() as c:
            r = await c.post('/api/v1/customers/1/discounts', json={'product': 9, 'discount': 0.1})

    assert r.status_code == 201
    assert creating.await_args.args[1] == 1
    assert r.json()['product']['code'] == 'P9'


@pytest.mark.asyncio
async def test_create_returns_404_for_an_unknown_customer() -> None:
    _auth()
    with _customer(False):
        async with await _client() as c:
            r = await c.post(
                '/api/v1/customers/999/discounts', json={'product': 9, 'discount': 0.1}
            )

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_create_returns_409_for_a_duplicate() -> None:
    _auth()
    duplicate = AsyncMock(side_effect=HTTPException(status_code=409, detail='dup'))
    with _customer(), patch(_DISCOUNT + 'create_discount', duplicate):
        async with await _client() as c:
            r = await c.post('/api/v1/customers/1/discounts', json={'product': 9, 'discount': 0.1})

    assert r.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'product': 9, 'discount': '-0.01'},
        {'product': 9, 'discount': '1.01'},
        {'product': 9, 'discount': '0.123456789'},
        {'discount': '0.1'},
    ],
)
async def test_create_refuses_an_invalid_body(body: dict) -> None:
    _auth()
    with _customer():
        async with await _client() as c:
            r = await c.post('/api/v1/customers/1/discounts', json=body)

    assert r.status_code == 422


# ── Update ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_returns_the_changed_override() -> None:
    _auth()
    with (
        _customer(),
        patch(_DISCOUNT + 'get_discount', AsyncMock(return_value=_discount())),
        patch(
            _DISCOUNT + 'update_discount',
            AsyncMock(return_value=_discount(discount=Decimal('0.5'))),
        ),
    ):
        async with await _client() as c:
            r = await c.put('/api/v1/customers/1/discounts/5', json={'discount': '0.5'})

    assert r.status_code == 200
    assert Decimal(str(r.json()['discount'])) == Decimal('0.5')


@pytest.mark.asyncio
async def test_update_ignores_a_product_in_the_body() -> None:
    _auth()
    updating = AsyncMock(return_value=_discount())
    with (
        _customer(),
        patch(_DISCOUNT + 'get_discount', AsyncMock(return_value=_discount())),
        patch(_DISCOUNT + 'update_discount', updating),
    ):
        async with await _client() as c:
            r = await c.put(
                '/api/v1/customers/1/discounts/5', json={'discount': '0.5', 'product': 77}
            )

    assert r.status_code == 200
    data = updating.await_args.args[2]
    assert not hasattr(data, 'product')
    assert data.discount == Decimal('0.5')


@pytest.mark.asyncio
async def test_update_returns_404_for_an_unknown_or_foreign_override() -> None:
    _auth()
    with _customer(), patch(_DISCOUNT + 'get_discount', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.put('/api/v1/customers/1/discounts/999', json={'discount': '0.5'})

    assert r.status_code == 404


@pytest.mark.asyncio
async def test_update_returns_404_for_an_unknown_customer() -> None:
    _auth()
    with _customer(False):
        async with await _client() as c:
            r = await c.put('/api/v1/customers/999/discounts/5', json={'discount': '0.5'})

    assert r.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_returns_204() -> None:
    _auth()
    deleting = AsyncMock()
    with (
        _customer(),
        patch(_DISCOUNT + 'get_discount', AsyncMock(return_value=_discount())),
        patch(_DISCOUNT + 'delete_discount', deleting),
    ):
        async with await _client() as c:
            r = await c.delete('/api/v1/customers/1/discounts/5')

    assert r.status_code == 204
    assert deleting.await_count == 1


@pytest.mark.asyncio
async def test_delete_returns_404_for_an_unknown_or_foreign_override() -> None:
    _auth()
    with _customer(), patch(_DISCOUNT + 'get_discount', AsyncMock(return_value=None)):
        async with await _client() as c:
            r = await c.delete('/api/v1/customers/1/discounts/999')

    assert r.status_code == 404


# ── Auth ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path'),
    [
        ('get', '/api/v1/customers/1/discounts'),
        ('post', '/api/v1/customers/1/discounts'),
        ('put', '/api/v1/customers/1/discounts/5'),
        ('delete', '/api/v1/customers/1/discounts/5'),
    ],
)
async def test_the_discount_routes_require_a_token(method: str, path: str) -> None:
    async with await _client() as c:
        r = await getattr(c, method)(path)

    assert r.status_code == 401
