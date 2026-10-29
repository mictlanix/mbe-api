"""The /inventory-issues routes (spec 029, US2): the HTTP contract, with the services patched."""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight
from app.main import app

BASE = '/api/v1/inventory-issues'
SHARED = 'app.services.inventory_documents'
WHEN = datetime(2026, 10, 1, 10, 0)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _no_database_for_lines() -> Generator[None, None, None]:
    """`attach_issue` reads the lines; the patched services already return them."""

    async def passthrough(db, issue):  # noqa: ANN001, ANN202
        return issue

    with patch(f'{SHARED}.attach_issue', passthrough):
        yield


def _auth(*, administrator: bool = True, rights: int | None = None) -> None:
    """`rights` is the privilege mask a non-administrator holds on every object."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = (
        None if rights is None else SimpleNamespace(privileges=rights)
    )
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _line(**overrides) -> SimpleNamespace:
    base = dict(
        issue_detail_id=1,
        product=3,
        product_code='W-1',
        product_name='Widget',
        quantity=Decimal('2'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _issue(**overrides) -> SimpleNamespace:
    base = dict(
        inventory_issue_id=1,
        facility=1,
        warehouse=1,
        supplier_return=None,
        serial=None,
        status='draft',
        completed=False,
        cancelled=False,
        comment=None,
        creator=7,
        updater=7,
        creation_time=WHEN,
        modification_time=WHEN,
        lines=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication and rights ─────────────────────────────────────────────────

ROUTES = [
    ('get', '', AccessRight.READ),
    ('post', '', AccessRight.CREATE),
    ('get', '/1', AccessRight.READ),
    ('put', '/1', AccessRight.UPDATE),
    ('post', '/1/lines', AccessRight.UPDATE),
    ('put', '/1/lines/1', AccessRight.UPDATE),
    ('delete', '/1/lines/1', AccessRight.UPDATE),
    ('post', '/1/confirm', AccessRight.UPDATE),
    ('post', '/1/cancel', AccessRight.DELETE),
]
BODIES = {
    ('post', ''): {'warehouse': 1},
    ('put', '/1'): {'comment': 'x'},
    ('post', '/1/lines'): {'product': 3, 'quantity': '1'},
    ('put', '/1/lines/1'): {'quantity': '1'},
}


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_a_token_it_is_401(method: str, path: str, right: AccessRight) -> None:
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_the_right_it_is_403(method: str, path: str, right: AccessRight) -> None:
    """Every other right on object 16 is held, and only the one the route needs is missing."""
    _auth(administrator=False, rights=0b1111 & ~int(right))
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def _body(method: str, path: str) -> dict:
    body = BODIES.get((method, path))
    return {} if body is None else {'json': body}


# ── Reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_the_issue_with_its_lines_and_supplier_return() -> None:
    _auth()
    issue = _issue(lines=[_line()], supplier_return=12)
    with patch(f'{SHARED}.get_document', AsyncMock(return_value=issue)):
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['inventory_issue_id'], body['status']) == (1, 'draft')
    assert body['lines'][0]['issue_detail_id'] == 1
    assert body['supplier_return'] == 12
    assert 'warnings' not in body


@pytest.mark.asyncio
async def test_unknown_issue_is_404() -> None:
    _auth()
    with patch(f'{SHARED}.get_document', AsyncMock(return_value=None)):
        async with _client() as client:
            responses = [
                await client.get(f'{BASE}/9'),
                await client.put(f'{BASE}/9', json={'comment': 'x'}),
                await client.post(f'{BASE}/9/confirm'),
                await client.post(f'{BASE}/9/cancel'),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 4


@pytest.mark.asyncio
async def test_list_returns_summaries_with_their_status() -> None:
    _auth()
    items = [_issue(), _issue(inventory_issue_id=2, completed=True, serial=1)]
    with patch(f'{SHARED}.list_documents', AsyncMock(return_value=(items, 2))) as listing:
        async with _client() as client:
            response = await client.get(BASE, params={'status': 'completed', 'warehouse': 4})

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['total'] == 2
    assert [item['status'] for item in body['items']] == ['draft', 'completed']
    assert listing.await_args.kwargs['status'] == 'completed'
    assert listing.await_args.kwargs['warehouse'] == 4


@pytest.mark.asyncio
async def test_list_with_a_bad_status_is_422() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='status must be one of draft, completed')
    with patch(f'{SHARED}.list_documents', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.get(BASE, params={'status': 'paid'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Writes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_201_with_the_draft() -> None:
    _auth()
    with patch(f'{SHARED}.create_draft', AsyncMock(return_value=_issue())) as create:
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 1, 'comment': 'c'})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['status'] == 'draft'
    assert create.await_args.kwargs['warehouse'] == 1


@pytest.mark.asyncio
async def test_create_on_an_unknown_warehouse_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Warehouse not found')
    with patch(f'{SHARED}.create_draft', AsyncMock(side_effect=error)):
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 99})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_line_routes_return_the_whole_issue() -> None:
    _auth()
    issue = _issue(lines=[_line(quantity=Decimal('5'))])
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=issue)),
        patch(f'{SHARED}.add_line', AsyncMock(return_value=issue)),
        patch(f'{SHARED}.update_line', AsyncMock(return_value=issue)),
        patch(f'{SHARED}.remove_line', AsyncMock(return_value=issue)),
    ):
        async with _client() as client:
            added = await client.post(f'{BASE}/1/lines', json={'product': 3, 'quantity': '5'})
            updated = await client.put(f'{BASE}/1/lines/1', json={'quantity': '5'})
            removed = await client.delete(f'{BASE}/1/lines/1')

    for response in (added, updated, removed):
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()['lines'][0]['quantity'] == '5'


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Line not found')
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_issue())),
        patch(f'{SHARED}.update_line', AsyncMock(side_effect=error)),
        patch(f'{SHARED}.remove_line', AsyncMock(side_effect=error)),
    ):
        async with _client() as client:
            updated = await client.put(f'{BASE}/1/lines/9', json={'quantity': '5'})
            removed = await client.delete(f'{BASE}/1/lines/9')

    assert updated.status_code == removed.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('path', 'body'),
    [
        ('/lines', {'product': 3, 'quantity': '0'}),
        ('/lines', {'product': 3, 'quantity': '-1'}),
        ('/lines/1', {'quantity': '0'}),
    ],
)
async def test_quantity_zero_or_less_is_422(path: str, body: dict) -> None:
    _auth()
    method = 'post' if path == '/lines' else 'put'
    with patch(f'{SHARED}.get_document', AsyncMock(return_value=_issue())):
        async with _client() as client:
            response = await getattr(client, method)(f'{BASE}/1{path}', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_confirm_and_cancel_return_the_issue() -> None:
    _auth()
    done = _issue(status='completed', completed=True, serial=5)
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=done)),
        patch(f'{SHARED}.confirm_simple', AsyncMock(return_value=done)),
        patch(f'{SHARED}.cancel', AsyncMock(return_value=_issue(status='cancelled'))),
    ):
        async with _client() as client:
            confirmed = await client.post(f'{BASE}/1/confirm')
            cancelled = await client.post(f'{BASE}/1/cancel')

    assert (confirmed.status_code, confirmed.json()['serial']) == (200, 5)
    assert (cancelled.status_code, cancelled.json()['status']) == (200, 'cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'code'), [('confirm', 409), ('confirm', 422), ('cancel', 409), ('cancel', 403)]
)
async def test_service_refusals_pass_through(action: str, code: int) -> None:
    _auth()
    refusal = HTTPException(status_code=code, detail='refused')
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_issue())),
        patch(
            f'{SHARED}.{"confirm_simple" if action == "confirm" else "cancel"}',
            AsyncMock(side_effect=refusal),
        ),
    ):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/{action}')

    assert response.status_code == code
    assert response.json()['detail'] == 'refused'
