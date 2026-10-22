"""Access privileges on the 11 master data routers (spec 022).

The route table pins, for every route, the grants its guard accepts — the module's own grant
first, then the hosts whose screens make the lookup (FR-005). It is the executable copy of
`specs/022-master-data-privileges/contracts/route-privileges.md`. The sweep proves that a
non-admin user with no privilege rows is refused everywhere; the behavior tests run each kind
of rule end to end.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app

R, C, U, D = AccessRight.READ, AccessRight.CREATE, AccessRight.UPDATE, AccessRight.DELETE
SO = SystemObject

PREFIXES = (
    '/api/v1/customers',
    '/api/v1/employees',
    '/api/v1/exchange-rates',
    '/api/v1/expenses',
    '/api/v1/labels',
    '/api/v1/payment-method-options',
    '/api/v1/price-lists',
    '/api/v1/suppliers',
    '/api/v1/taxpayer-recipients',
    '/api/v1/vehicle-operators',
    '/api/v1/vehicles',
)


def _own(obj: SystemObject, right: AccessRight) -> tuple[tuple[SystemObject, AccessRight], ...]:
    return ((obj, right),)


def _lookup(
    obj: SystemObject, *hosts: SystemObject
) -> tuple[tuple[SystemObject, AccessRight], ...]:
    return ((obj, R), *((host, R) for host in hosts))


def _crud(base: str, item: str, obj: SystemObject, *hosts: SystemObject) -> dict:
    read = _lookup(obj, *hosts)
    return {
        ('GET', base): read,
        ('POST', base): _own(obj, C),
        ('GET', f'{base}/{item}'): read,
        ('PUT', f'{base}/{item}'): _own(obj, U),
        ('DELETE', f'{base}/{item}'): _own(obj, D),
    }


_CUSTOMER_LOOKUP = (SO.SALES_ORDERS, SO.SALES_QUOTES, SO.POS)
_EMPLOYEE_LOOKUP = (SO.SALES_ORDERS, SO.SALES_QUOTES, SO.POS, SO.CUSTOMERS, SO.VEHICLE_OPERATORS)
_DISCOUNTS = '/api/v1/customers/{customer_id}/discounts'
_AGREEMENTS = '/api/v1/suppliers/{supplier_id}/agreements'
_PHOTO = '/api/v1/vehicle-operators/{vehicle_operator_id}/license-photo'

# None: the route resolves its privilege in the handler (FR-006), so it carries no guard.
EXPECTED: dict[tuple[str, str], tuple[tuple[SystemObject, AccessRight], ...] | None] = {
    **_crud('/api/v1/customers', '{customer_id}', SO.CUSTOMERS, *_CUSTOMER_LOOKUP),
    ('PUT', '/api/v1/customers/{customer_id}'): None,
    ('GET', _DISCOUNTS): _own(SO.CUSTOMERS, R),
    ('POST', _DISCOUNTS): _own(SO.CUSTOMERS, U),
    ('PUT', f'{_DISCOUNTS}/{{customer_discount_id}}'): _own(SO.CUSTOMERS, U),
    ('DELETE', f'{_DISCOUNTS}/{{customer_discount_id}}'): _own(SO.CUSTOMERS, U),
    **_crud('/api/v1/employees', '{employee_id}', SO.EMPLOYEES, *_EMPLOYEE_LOOKUP),
    **_crud('/api/v1/exchange-rates', '{exchange_rate_id}', SO.EXCHANGE_RATES),
    **_crud('/api/v1/expenses', '{expense_id}', SO.EXPENSES),
    **_crud('/api/v1/labels', '{label_id}', SO.LABELS, SO.PRODUCTS, SO.PRICING),
    **_crud(
        '/api/v1/payment-method-options',
        '{payment_method_option_id}',
        SO.PAYMENT_METHOD_OPTIONS,
        SO.SALES_ORDERS,
        SO.POS,
        SO.CUSTOMER_PAYMENTS,
    ),
    **_crud('/api/v1/price-lists', '{price_list_id}', SO.PRICE_LISTS, SO.CUSTOMERS, SO.PRICING),
    ('GET', '/api/v1/price-lists/{price_list_id}/delete/preview'): _own(SO.PRICE_LISTS, R),
    **_crud('/api/v1/suppliers', '{supplier_id}', SO.SUPPLIERS, SO.PRODUCTS, SO.PRICING),
    ('GET', _AGREEMENTS): _own(SO.SUPPLIERS, R),
    ('POST', _AGREEMENTS): _own(SO.SUPPLIERS, U),
    ('PUT', f'{_AGREEMENTS}/{{supplier_agreement_id}}'): _own(SO.SUPPLIERS, U),
    ('DELETE', f'{_AGREEMENTS}/{{supplier_agreement_id}}'): _own(SO.SUPPLIERS, U),
    **_crud(
        '/api/v1/taxpayer-recipients',
        '{rfc}',
        SO.TAXPAYER_RECIPIENTS,
        SO.SALES_ORDERS,
        SO.CUSTOMERS,
    ),
    **_crud('/api/v1/vehicle-operators', '{vehicle_operator_id}', SO.VEHICLE_OPERATORS),
    ('POST', _PHOTO): _own(SO.VEHICLE_OPERATORS, U),
    ('GET', _PHOTO): _own(SO.VEHICLE_OPERATORS, R),
    ('DELETE', _PHOTO): _own(SO.VEHICLE_OPERATORS, U),
    **_crud('/api/v1/vehicles', '{vehicle_id}', SO.VEHICLE),
}


def _routes() -> dict[tuple[str, str], APIRoute]:
    found = {}
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not any(route.path == p or route.path.startswith(p + '/') for p in PREFIXES):
            continue
        for method in route.methods:
            found[(method, route.path)] = route
    return found


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = False) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=None,
        employee_id=7,
    )


def _bound_objects(stmt) -> set[int]:  # noqa: ANN001
    """The SystemObject values a privilege query asks for, flattening an `IN (...)` list."""
    values = []
    for value in stmt.compile().params.values():
        values.extend(value if isinstance(value, list | tuple) else [value])
    known = {int(obj) for obj in SystemObject}
    return {int(v) for v in values if isinstance(v, int) and int(v) in known}


def _privileges(**masks: int) -> None:
    """Answer privilege queries from `masks`, keyed by SystemObject name (e.g. `LABELS=2`)."""
    held = {int(SystemObject[name]): mask for name, mask in masks.items()}

    async def _execute(stmt):  # noqa: ANN001, ANN202
        asked = _bound_objects(stmt)
        rows = [(obj, mask) for obj, mask in held.items() if obj in asked]
        result = MagicMock()
        result.all.return_value = rows
        result.scalar_one_or_none.return_value = (
            SimpleNamespace(privileges=rows[0][1]) if len(asked) == 1 and rows else None
        )
        return result

    session = SimpleNamespace(execute=_execute)

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Route table ───────────────────────────────────────────────────────────────


def test_the_route_table_covers_every_route_of_the_11_routers() -> None:
    assert set(_routes()) == set(EXPECTED)


@pytest.mark.parametrize('key', list(EXPECTED), ids=lambda k: f'{k[0]} {k[1]}')
def test_each_route_requires_the_contracted_grants(key: tuple[str, str]) -> None:
    route = _routes()[key]
    guards = [d.call.grants for d in route.dependant.dependencies if hasattr(d.call, 'grants')]

    if EXPECTED[key] is None:
        assert guards == []
    else:
        assert guards == [EXPECTED[key]]


# ── No-privilege sweep ────────────────────────────────────────────────────────


def _fill(path: str) -> str:
    out = path.replace('{rfc}', 'XAXX010101000')
    while '{' in out:
        start = out.index('{')
        out = out[:start] + '1' + out[out.index('}', start) + 1 :]
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(EXPECTED), ids=lambda k: f'{k[0]} {k[1]}')
async def test_a_user_without_privileges_is_refused(key: tuple[str, str]) -> None:
    method, path = key
    _auth()
    _privileges()
    sends_json = method in ('POST', 'PUT') and not path.endswith('license-photo')
    kwargs = {'json': {}} if sends_json else {}
    async with _client() as client:
        response = await client.request(method, _fill(path), **kwargs)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()['detail'] == 'Insufficient privileges'


# ── Own privilege ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_only_cannot_delete() -> None:
    _auth()
    _privileges(LABELS=2)
    async with _client() as client:
        response = await client.delete('/api/v1/labels/1')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_a_zero_mask_is_no_access() -> None:
    _auth()
    _privileges(VEHICLE=0)
    async with _client() as client:
        response = await client.get('/api/v1/vehicles')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_the_own_privilege_opens_the_route() -> None:
    _auth()
    _privileges(VEHICLE=2)
    with patch('app.services.vehicle_service.list_vehicles', new=AsyncMock(return_value=([], 0))):
        async with _client() as client:
            response = await client.get('/api/v1/vehicles')

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_an_administrator_needs_no_rows() -> None:
    _auth(administrator=True)
    _privileges()
    with patch('app.services.vehicle_service.list_vehicles', new=AsyncMock(return_value=([], 0))):
        async with _client() as client:
            response = await client.get('/api/v1/vehicles')

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_no_token_is_401() -> None:
    async with _client() as client:
        response = await client.get('/api/v1/vehicles')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
