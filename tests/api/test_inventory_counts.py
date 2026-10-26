"""The /inventory-counts routes (spec 030, US1): the HTTP contract, with the services patched."""

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
from app.enums import AccessRight, SystemObject
from app.main import app

BASE = '/api/v1/inventory-counts'
SERVICE = 'app.services.inventory_count_service'
SHARED = 'app.services.inventory_documents'
WHEN = datetime(2026, 10, 1, 10, 0)


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


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
        count_detail_id=1,
        product=3,
        product_code='W-1',
        product_name='Widget',
        quantity=Decimal('2'),
        quantity_on_hand=None,
        difference=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _count(**overrides) -> SimpleNamespace:
    base = dict(
        inventory_count_id=1,
        facility=1,
        warehouse=1,
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
        negative_available=[],
        corrected_by_other_counts=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication and rights (FR-008) ────────────────────────────────────────

ROUTES = [
    ('get', '', AccessRight.READ),
    ('post', '', AccessRight.CREATE),
    ('get', '/1', AccessRight.READ),
    ('put', '/1', AccessRight.UPDATE),
    ('put', '/1/lines', AccessRight.UPDATE),
    ('put', '/1/lines/1', AccessRight.UPDATE),
    ('delete', '/1/lines/1', AccessRight.UPDATE),
    ('post', '/1/confirm', AccessRight.UPDATE),
    ('post', '/1/cancel', AccessRight.DELETE),
]
BODIES = {
    ('post', ''): {'warehouse': 1},
    ('put', '/1'): {'comment': 'x'},
    ('put', '/1/lines'): {'lines': [{'product': 3, 'quantity': '1'}]},
    ('put', '/1/lines/1'): {'quantity': '1'},
    ('post', '/1/confirm'): {'include_uncounted': False},
}


def _body(method: str, path: str) -> dict:
    body = BODIES.get((method, path))
    return {} if body is None else {'json': body}


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_a_token_it_is_401(method: str, path: str, right: AccessRight) -> None:
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_the_right_it_is_403(method: str, path: str, right: AccessRight) -> None:
    """Every other right on object 74 is held, and only the one the route needs is missing."""
    _auth(administrator=False, rights=0b1111 & ~int(right))
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_every_route_checks_its_right_on_object_74() -> None:
    """The grant each route declares: PhysicalCountAdjustment (74), with the contract's right."""
    expected = {(m.upper(), BASE + p): right for m, p, right in ROUTES}
    found = {}
    for route in app.routes:
        if not getattr(route, 'path', '').startswith(BASE):
            continue
        grants = [
            grant
            for dependency in route.dependant.dependencies
            for grant in getattr(dependency.call, 'grants', ())
        ]
        for method in route.methods:
            found[(method, route.path.replace('{count_id}', '1').replace('{line_id}', '1'))] = (
                grants
            )

    assert set(found) == set(expected)
    for key, right in expected.items():
        assert found[key] == [(SystemObject.PHYSICAL_COUNT_ADJUSTMENT, right)], key


# ── Reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_the_count_with_its_lines() -> None:
    _auth()
    count = _count(lines=[_line()])
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=count)),
        patch(f'{SERVICE}.attach_derived', AsyncMock(return_value=count)) as attach,
    ):
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['inventory_count_id'], body['status']) == (1, 'draft')
    assert body['lines'][0]['count_detail_id'] == 1
    assert body['lines'][0]['quantity_on_hand'] is None
    assert body['lines'][0]['difference'] is None
    attach.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_count_is_404_on_every_route_that_names_one() -> None:
    _auth()
    with patch(f'{SHARED}.get_document', AsyncMock(return_value=None)):
        async with _client() as client:
            responses = [
                await client.get(f'{BASE}/9'),
                await client.put(f'{BASE}/9', json={'comment': 'x'}),
                await client.put(
                    f'{BASE}/9/lines', json={'lines': [{'product': 3, 'quantity': '1'}]}
                ),
                await client.put(f'{BASE}/9/lines/1', json={'quantity': '1'}),
                await client.delete(f'{BASE}/9/lines/1'),
                await client.post(f'{BASE}/9/confirm'),
                await client.post(f'{BASE}/9/cancel'),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 7
    assert responses[0].json() == {'detail': 'Count not found'}


@pytest.mark.asyncio
async def test_list_returns_summaries_and_passes_the_filters() -> None:
    _auth()
    items = [_count(), _count(inventory_count_id=2, completed=True, serial=1)]
    with patch(f'{SHARED}.list_documents', AsyncMock(return_value=(items, 2))) as listing:
        async with _client() as client:
            response = await client.get(
                BASE, params={'status': 'completed', 'warehouse': 4, 'skip': 1, 'limit': 5}
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['total'] == 2
    assert [item['status'] for item in body['items']] == ['draft', 'completed']
    kwargs = listing.await_args.kwargs
    assert (kwargs['status'], kwargs['warehouse'], kwargs['skip'], kwargs['limit']) == (
        'completed',
        4,
        1,
        5,
    )
    assert listing.await_args.args[1].label == 'Count'


@pytest.mark.asyncio
async def test_list_with_a_bad_status_is_422() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='status must be one of draft, completed')
    with patch(f'{SHARED}.list_documents', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.get(BASE, params={'status': 'in_transit'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Writes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_201_with_the_draft() -> None:
    _auth()
    with patch(f'{SERVICE}.create', AsyncMock(return_value=_count())) as create:
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 1, 'comment': 'c'})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['status'] == 'draft'
    assert create.await_args.args[1].warehouse == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(('code', 'detail'), [(404, 'Warehouse not found'), (422, 'In transit')])
async def test_create_refusals_pass_through(code: int, detail: str) -> None:
    _auth()
    with patch(f'{SERVICE}.create', AsyncMock(side_effect=HTTPException(code, detail))):
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 99})

    assert response.status_code == code


@pytest.mark.asyncio
async def test_create_with_a_long_comment_is_422() -> None:
    _auth()
    async with _client() as client:
        response = await client.post(BASE, json={'warehouse': 1, 'comment': 'x' * 501})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_update_and_line_routes_return_the_whole_count() -> None:
    _auth()
    count = _count(lines=[_line(quantity=Decimal('5'))])
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=count)),
        patch(f'{SERVICE}.update', AsyncMock(return_value=count)),
        patch(f'{SERVICE}.set_lines', AsyncMock(return_value=count)) as set_lines,
        patch(f'{SERVICE}.update_line', AsyncMock(return_value=count)),
        patch(f'{SERVICE}.remove_line', AsyncMock(return_value=count)),
    ):
        async with _client() as client:
            responses = [
                await client.put(f'{BASE}/1', json={'comment': 'x'}),
                await client.put(
                    f'{BASE}/1/lines',
                    json={
                        'lines': [
                            {'product': 3, 'quantity': '5'},
                            {'product_code': 'W-2', 'quantity': '0'},
                        ]
                    },
                ),
                await client.put(f'{BASE}/1/lines/1', json={'quantity': '0'}),
                await client.delete(f'{BASE}/1/lines/1'),
            ]

    for response in responses:
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()['lines'][0]['quantity'] == '5'
    items = set_lines.await_args.args[2]
    assert [(i.product, i.product_code) for i in items] == [(3, None), (None, 'W-2')]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'lines',
    [
        [],
        [{'product': 3, 'quantity': '-1'}],
        [{'product': 3, 'quantity': '1.00001'}],
        [{'product': 3, 'product_code': 'W-1', 'quantity': '1'}],
        [{'quantity': '1'}],
        [{'product': n, 'quantity': '1'} for n in range(2001)],
    ],
    ids=['empty', 'negative', 'five-decimals', 'both', 'neither', 'over-2000'],
)
async def test_a_malformed_batch_is_422(lines: list) -> None:
    _auth()
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.set_lines', AsyncMock()) as set_lines,
    ):
        async with _client() as client:
            response = await client.put(f'{BASE}/1/lines', json={'lines': lines})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    set_lines.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_batch_is_422_with_every_error() -> None:
    _auth()
    errors = [
        {'index': 0, 'product_code': 'NOPE', 'reason': 'unknown product'},
        {'index': 2, 'product': 5, 'reason': 'not stockable'},
    ]
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.set_lines', AsyncMock(side_effect=_batch_refused(errors))),
    ):
        async with _client() as client:
            response = await client.put(
                f'{BASE}/1/lines', json={'lines': [{'product': 3, 'quantity': '1'}]}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    body = response.json()
    assert body['errors'] == errors
    assert isinstance(body['detail'], str)


def _batch_refused(errors: list[dict]) -> Exception:
    from app.services.inventory_count_service import BatchRefused

    return BatchRefused(errors)


@pytest.mark.asyncio
async def test_single_line_quantity_below_zero_is_422() -> None:
    _auth()
    with patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())):
        async with _client() as client:
            response = await client.put(f'{BASE}/1/lines/1', json={'quantity': '-1'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Line not found')
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.update_line', AsyncMock(side_effect=error)),
        patch(f'{SERVICE}.remove_line', AsyncMock(side_effect=error)),
    ):
        async with _client() as client:
            updated = await client.put(f'{BASE}/1/lines/9', json={'quantity': '5'})
            removed = await client.delete(f'{BASE}/1/lines/9')

    assert updated.status_code == removed.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_confirm_returns_the_count_and_its_warnings() -> None:
    _auth()
    done = _count(
        status='completed',
        completed=True,
        serial=5,
        lines=[_line(quantity_on_hand=Decimal('10'), difference=Decimal('-8'))],
        negative_available=[
            {
                'product': 3,
                'product_code': 'W-1',
                'counted': Decimal('2'),
                'reserved': Decimal('3'),
                'available': Decimal('-1'),
            }
        ],
        corrected_by_other_counts=[
            {'product': 3, 'product_code': 'W-1', 'inventory_count_id': 4, 'serial': 2}
        ],
    )
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.confirm', AsyncMock(return_value=done)) as confirm,
    ):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/confirm', json={'include_uncounted': True})
            bare = await client.post(f'{BASE}/1/confirm')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['serial'], body['lines'][0]['difference']) == (5, '-8')
    assert body['negative_available'][0]['available'] == '-1'
    assert body['corrected_by_other_counts'][0]['inventory_count_id'] == 4
    assert confirm.await_args_list[0].kwargs['include_uncounted'] is True
    # Without a body, uncounted products are left alone.
    assert bare.status_code == status.HTTP_200_OK
    assert confirm.await_args_list[1].kwargs['include_uncounted'] is False


@pytest.mark.asyncio
async def test_cancel_returns_the_count() -> None:
    _auth()
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.cancel', AsyncMock(return_value=_count(status='cancelled'))),
    ):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/cancel')

    assert (response.status_code, response.json()['status']) == (200, 'cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'code'),
    [('confirm', 409), ('confirm', 403), ('confirm', 422), ('cancel', 409), ('cancel', 403)],
)
async def test_service_refusals_pass_through(action: str, code: int) -> None:
    _auth()
    refusal = HTTPException(status_code=code, detail='refused')
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count())),
        patch(f'{SERVICE}.{action}', AsyncMock(side_effect=refusal)),
    ):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/{action}')

    assert response.status_code == code
    assert response.json() == {'detail': 'refused'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('method', 'path', 'body'),
    [
        ('put', '/1', {'comment': 'x'}),
        ('put', '/1/lines', {'lines': [{'product': 3, 'quantity': '1'}]}),
        ('put', '/1/lines/1', {'quantity': '1'}),
        ('delete', '/1/lines/1', None),
    ],
)
async def test_editing_a_confirmed_count_is_409(method: str, path: str, body: dict | None) -> None:
    _auth()
    refusal = HTTPException(status_code=409, detail='Document is completed')
    service = {
        '/1': 'update',
        '/1/lines': 'set_lines',
        '/1/lines/1': 'update_line' if method == 'put' else 'remove_line',
    }[path]
    with (
        patch(f'{SHARED}.get_document', AsyncMock(return_value=_count(completed=True))),
        patch(f'{SERVICE}.{service}', AsyncMock(side_effect=refusal)),
    ):
        async with _client() as client:
            kwargs = {} if body is None else {'json': body}
            response = await getattr(client, method)(BASE + path, **kwargs)

    assert response.status_code == status.HTTP_409_CONFLICT
