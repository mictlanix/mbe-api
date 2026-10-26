"""The /inventory-transfers routes (spec 029, contracts/inventory-documents.md, T018).

The HTTP contract with the services patched: the 13 routes and the right each one needs on
InventoryTransfers (17), 401 and 403 on every route, and the 404 and 422 answers. The flows
themselves run against real tables in `tests/integration/test_inventory_transfer_flow.py`.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.schemas.inventory import InTransitResponse
from app.services import inventory_documents as inv
from app.services import inventory_transfer_service as svc

BASE = '/api/v1/inventory-transfers'
ITEM = f'{BASE}/{{inventory_transfer_id}}'
LINE = f'{ITEM}/lines/{{line_id}}'
R, C, U, D = AccessRight.READ, AccessRight.CREATE, AccessRight.UPDATE, AccessRight.DELETE

#: (method, path template) → the right it needs on InventoryTransfers.
ROUTES: dict[tuple[str, str], AccessRight] = {
    ('GET', BASE): R,
    ('GET', f'{BASE}/in-transit'): R,
    ('POST', BASE): C,
    ('GET', ITEM): R,
    ('PUT', ITEM): U,
    ('POST', f'{ITEM}/lines'): U,
    ('PUT', LINE): U,
    ('DELETE', LINE): U,
    ('POST', f'{ITEM}/sales-order-items'): U,
    ('POST', f'{ITEM}/dispatch'): U,
    ('POST', f'{ITEM}/receive'): U,
    ('POST', f'{ITEM}/close'): D,
    ('POST', f'{ITEM}/cancel'): D,
}

#: A valid body per route, so a refusal is never a validation error.
BODIES: dict[tuple[str, str], dict] = {
    ('POST', BASE): {'warehouse': 1, 'warehouse_to': 3},
    ('PUT', ITEM): {'comment': 'x'},
    ('POST', f'{ITEM}/lines'): {'product': 1, 'quantity': '2'},
    ('PUT', LINE): {'quantity': '2'},
    ('POST', f'{ITEM}/sales-order-items'): {'sales_order': 5},
}


def _url(path: str) -> str:
    return path.replace('{inventory_transfer_id}', '1').replace('{line_id}', '11')


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


def _privileges(mask: int) -> None:
    """A non-administrator holding `mask` on every object asked for."""
    _auth(administrator=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(privileges=mask)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


async def _call(client: AsyncClient, key: tuple[str, str], **kwargs):  # noqa: ANN202
    method, path = key
    if key in BODIES and 'json' not in kwargs:
        kwargs['json'] = BODIES[key]
    return await client.request(method, _url(path), **kwargs)


_WHEN = datetime(2026, 10, 1, 10, 0)


def _transfer(**overrides) -> SimpleNamespace:
    base = dict(
        inventory_transfer_id=1,
        facility=1,
        warehouse=1,
        warehouse_to=3,
        serial=None,
        status='draft',
        comment=None,
        creator=7,
        updater=7,
        creation_time=_WHEN,
        modification_time=_WHEN,
        lines=[
            SimpleNamespace(
                transfer_detail_id=11,
                product=1,
                product_code='P1',
                product_name='Producto',
                quantity=Decimal('10'),
                quantity_received=Decimal('8'),
                quantity_outstanding=Decimal('2'),
            )
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _attach() -> AsyncMock:
    return AsyncMock(side_effect=lambda db, transfer: transfer)


# ── Route table ───────────────────────────────────────────────────────────────


def _routes() -> dict[tuple[str, str], APIRoute]:
    found = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and (
            route.path == BASE or route.path.startswith(BASE + '/')
        ):
            for method in route.methods:
                found[(method, route.path)] = route
    return found


def test_the_13_routes_of_the_contract():
    assert set(_routes()) == set(ROUTES)


@pytest.mark.parametrize('key', list(ROUTES), ids=lambda k: f'{k[0]} {k[1]}')
def test_each_route_requires_its_right_on_inventory_transfers(key):
    route = _routes()[key]
    guards = [d.call.grants for d in route.dependant.dependencies if hasattr(d.call, 'grants')]
    assert guards == [((SystemObject.INVENTORY_TRANSFERS, ROUTES[key]),)]


def test_in_transit_is_declared_before_the_item_route():
    paths = [route.path for route in app.routes if isinstance(route, APIRoute)]
    assert paths.index(f'{BASE}/in-transit') < paths.index(ITEM)


# ── 401 and 403 ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize('key', list(ROUTES), ids=lambda k: f'{k[0]} {k[1]}')
async def test_every_route_requires_a_token(key):
    async with _client() as client:
        response = await _call(client, key)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize('key', list(ROUTES), ids=lambda k: f'{k[0]} {k[1]}')
async def test_every_route_is_403_without_its_right(key):
    _privileges(int(R | C | U | D) & ~int(ROUTES[key]))
    async with _client() as client:
        response = await _call(client, key)
    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Reads ─────────────────────────────────────────────────────────────────────


async def test_list_passes_the_filters_and_the_transfer_status_clause():
    _auth()
    listed = AsyncMock(
        return_value=([_transfer(status=None, completed=True, cancelled=False, in_transit=True)], 1)
    )
    with patch.object(inv, 'list_documents', listed):
        async with _client() as client:
            response = await client.get(
                BASE, params={'status': 'in_transit', 'warehouse': 3, 'skip': 5, 'limit': 10}
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['status'] == 'in_transit'
    kwargs = listed.await_args.kwargs
    assert (kwargs['status'], kwargs['warehouse'], kwargs['skip'], kwargs['limit']) == (
        'in_transit',
        3,
        5,
        10,
    )
    assert kwargs['status_clause'] is svc.transfer_status_clause


async def test_list_refuses_a_status_transfers_do_not_have():
    _auth()
    async with _client() as client:
        response = await client.get(BASE, params={'status': 'completed'})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_in_transit_defaults_to_the_callers_facility():
    _auth()
    view = AsyncMock(return_value=InTransitResponse(facility=1))
    with patch.object(svc, 'in_transit_view', view):
        async with _client() as client:
            mine = await client.get(f'{BASE}/in-transit')
            other = await client.get(f'{BASE}/in-transit', params={'facility': 4})

    assert mine.status_code == status.HTTP_200_OK, mine.text
    assert mine.json() == {'facility': 1, 'products': [], 'lines': []}
    assert other.status_code == status.HTTP_200_OK
    assert [c.args[1] for c in view.await_args_list] == [1, 4]


async def test_get_returns_the_lines_with_received_and_outstanding():
    _auth()
    with (
        patch.object(inv, 'get_document', AsyncMock(return_value=_transfer())),
        patch.object(svc, 'attach_derived', _attach()),
    ):
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    line = response.json()['lines'][0]
    assert (line['quantity_received'], line['quantity_outstanding']) == ('8', '2')


async def test_get_an_unknown_transfer_is_404():
    _auth()
    with patch.object(inv, 'get_document', AsyncMock(return_value=None)):
        async with _client() as client:
            response = await client.get(f'{BASE}/99')
    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Drafts and lines ──────────────────────────────────────────────────────────


async def test_create_is_201():
    _auth()
    created = AsyncMock(return_value=_transfer())
    with patch.object(inv, 'create_draft', created), patch.object(svc, 'attach_derived', _attach()):
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 1, 'warehouse_to': 3})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    kwargs = created.await_args.kwargs
    assert (kwargs['warehouse'], kwargs['warehouse_to']) == (1, 3)


async def test_create_without_a_destination_is_422():
    _auth()
    async with _client() as client:
        response = await client.post(BASE, json={'warehouse': 1})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.parametrize(
    'key, service',
    [
        (('PUT', ITEM), 'update_header'),
        (('POST', f'{ITEM}/lines'), 'add_line'),
        (('PUT', LINE), 'update_line'),
        (('DELETE', LINE), 'remove_line'),
    ],
    ids=['header', 'add line', 'update line', 'remove line'],
)
async def test_draft_edits_answer_200(key, service):
    _auth()
    with (
        patch.object(inv, service, AsyncMock(return_value=_transfer())) as edit,
        patch.object(svc, 'attach_derived', _attach()),
    ):
        async with _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_200_OK, response.text
    assert edit.await_args.args[1] is inv.TRANSFER


@pytest.mark.parametrize('key', [('POST', f'{ITEM}/lines'), ('PUT', LINE)], ids=['add', 'update'])
async def test_a_line_quantity_of_zero_is_422(key):
    _auth()
    async with _client() as client:
        response = await _call(client, key, json={'product': 1, 'quantity': '0'})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_an_unknown_line_is_404():
    _auth()
    missing = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    with patch.object(inv, 'update_line', AsyncMock(side_effect=missing)):
        async with _client() as client:
            response = await _call(client, ('PUT', LINE))
    assert response.status_code == status.HTTP_404_NOT_FOUND


async def test_sales_order_items_pass_the_order():
    _auth()
    added = AsyncMock(return_value=_transfer())
    with (
        patch.object(svc, 'add_sales_order_items', added),
        patch.object(svc, 'attach_derived', _attach()),
    ):
        async with _client() as client:
            response = await _call(client, ('POST', f'{ITEM}/sales-order-items'))

    assert response.status_code == status.HTTP_200_OK, response.text
    assert added.await_args.args[1:] == (1, 5)


# ── Steps ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'step, service',
    [('dispatch', 'dispatch'), ('close', 'close_remainder'), ('cancel', 'cancel')],
)
async def test_steps_answer_200(step, service):
    _auth()
    with (
        patch.object(svc, service, AsyncMock(return_value=_transfer(status='in_transit'))) as call,
        patch.object(svc, 'attach_derived', _attach()),
    ):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/{step}')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['status'] == 'in_transit'
    assert call.await_args.args[1] == 1


@pytest.mark.parametrize('step, service', [('dispatch', 'dispatch'), ('cancel', 'cancel')])
async def test_a_step_on_an_unknown_transfer_is_404(step, service):
    _auth()
    missing = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Transfer not found')
    with patch.object(svc, service, AsyncMock(side_effect=missing)):
        async with _client() as client:
            response = await client.post(f'{BASE}/99/{step}')
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.parametrize(
    'kwargs, expected',
    [
        ({}, None),
        ({'json': {}}, None),
        ({'json': {'lines': [{'line_id': 11, 'quantity': '2'}]}}, [(11, Decimal('2'))]),
    ],
    ids=['no body', 'no lines', 'per line'],
)
async def test_receive_without_lines_receives_everything(kwargs, expected):
    _auth()
    received = AsyncMock(return_value=_transfer())
    with patch.object(svc, 'receive', received), patch.object(svc, 'attach_derived', _attach()):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/receive', **kwargs)

    assert response.status_code == status.HTTP_200_OK, response.text
    assert received.await_args.args[1:] == (1, expected)


@pytest.mark.parametrize(
    'lines',
    [
        [],
        [{'line_id': 11, 'quantity': '1'}, {'line_id': 11, 'quantity': '1'}],
        [{'line_id': 11, 'quantity': '0'}],
    ],
    ids=['empty', 'same line twice', 'zero'],
)
async def test_receive_refuses_a_malformed_list(lines):
    _auth()
    async with _client() as client:
        response = await client.post(f'{BASE}/1/receive', json={'lines': lines})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.parametrize('code', [404, 409])
async def test_receive_passes_the_services_refusal(code):
    _auth()
    refusal = HTTPException(status_code=code, detail='refused')
    with patch.object(svc, 'receive', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.post(
                f'{BASE}/1/receive', json={'lines': [{'line_id': 99, 'quantity': '1'}]}
            )
    assert response.status_code == code
