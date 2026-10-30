"""The /purchase-requests routes (spec 031): the HTTP contract, with the service patched.

Behaviour against a real database is covered in tests/integration/test_purchase_request_flow.py.
"""

import re
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from datetime import date, datetime
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

BASE = '/api/v1/purchase-requests'
SERVICE = 'app.services.purchase_request_service'
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
        purchase_request_detail_id=1,
        product=3,
        product_code='W-1',
        product_name='Widget',
        quantity=Decimal('2.00'),
        warehouse=1,
        to_purchase=False,
        min_order_qty=1,
        purchase_order_detail=None,
        purchase_order=None,
        purchase_order_status=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _request(**overrides) -> SimpleNamespace:
    """Every field of PurchaseRequestResponse."""
    base = dict(
        purchase_request_id=1,
        serial=None,
        status='draft',
        warehouse=1,
        facility=1,
        date=WHEN,
        comment=None,
        creator=7,
        updater=7,
        approver=None,
        approval_time=None,
        line_count=1,
        warnings=[],
        lines=[_line()],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@contextmanager
def _service(request: SimpleNamespace | None = None, *, found: bool = True):
    """Patch the whole service: every state-changing call answers with `request`."""
    request = request or _request()
    mocks: dict[str, AsyncMock] = {}
    with ExitStack() as stack:
        for name, value in {
            'get_request': request if found else None,
            'attach': request,
            'create': request,
            'update': request,
            'add_line': request,
            'update_line': request,
            'remove_line': request,
            'submit': request,
            'cancel': request,
            'mark': request,
            'approve': request,
            'reject': request,
            'list_requests': ([request], 1),
            'approval_queue': ([request], 1),
            'approve_many': [],
        }.items():
            mocks[name] = stack.enter_context(
                patch(f'{SERVICE}.{name}', AsyncMock(return_value=value))
            )
        yield mocks


# ── Authentication and rights ─────────────────────────────────────────────────

# PurchaseRequest (86), PurchaseRequestApproval (96) and PurchasesOrders (20) share one mask in
# these tests, so each route needs the one right it declares.
ROUTES = [
    ('get', '', AccessRight.READ),
    ('post', '', AccessRight.CREATE),
    ('get', '/approval-queue', AccessRight.READ),
    ('post', '/approve', AccessRight.UPDATE),
    ('get', '/1', AccessRight.READ),
    ('put', '/1', AccessRight.UPDATE),
    ('post', '/1/lines', AccessRight.UPDATE),
    ('put', '/1/lines/1', AccessRight.UPDATE),
    ('delete', '/1/lines/1', AccessRight.UPDATE),
    ('post', '/1/submit', AccessRight.UPDATE),
    ('post', '/1/cancel', AccessRight.DELETE),
    ('put', '/1/lines/1/to-purchase', AccessRight.UPDATE),
    ('post', '/1/approve', AccessRight.UPDATE),
    ('post', '/1/reject', AccessRight.UPDATE),
]
BODIES = {
    ('post', ''): {'warehouse': 1, 'comment': 'x'},
    ('post', '/approve'): {'ids': [1]},
    ('put', '/1'): {'comment': 'x'},
    ('post', '/1/lines'): {'product': 3, 'quantity': '1'},
    ('put', '/1/lines/1'): {'quantity': '1'},
    ('put', '/1/lines/1/to-purchase'): {'to_purchase': True},
    ('post', '/1/reject'): {'reason': 'too expensive'},
}
ALL_RIGHTS = 0b1111


def _body(method: str, path: str) -> dict:
    body = BODIES.get((method, path))
    return {} if body is None else {'json': body}


def test_the_table_covers_every_route_of_the_router() -> None:
    served = {
        (method.lower(), route.path.removeprefix(BASE))
        for route in app.routes
        if route.path.startswith(BASE)
        for method in route.methods
    }
    declared = {
        (method, re.sub(r'/\d+', lambda m: '/{' + next(names) + '}', path))
        for method, path, _ in ROUTES
        for names in [iter(['request_id', 'line_id'])]
    }
    assert len(ROUTES) == 14
    assert served == declared


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_a_token_it_is_401(method: str, path: str, right: AccessRight) -> None:
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize('held', ['nothing', 'every other right'])
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_the_right_it_is_403(
    method: str, path: str, right: AccessRight, held: str
) -> None:
    """Both no rights at all and every other right held: only the one the route needs is missing."""
    _auth(administrator=False, rights=0 if held == 'nothing' else ALL_RIGHTS & ~int(right))
    with _service() as service:
        async with _client() as client:
            response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service['get_request'].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_user_with_no_privilege_row_is_403() -> None:
    _auth(administrator=False, rights=None)
    async with _client() as client:
        response = await client.get(BASE)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_the_exact_right_is_enough(method: str, path: str, right: AccessRight) -> None:
    """Holding only the declared right on the object lets the call through."""
    _auth(administrator=False, rights=int(right))
    with _service():
        async with _client() as client:
            response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), response.text


# ── Reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_the_request_with_its_lines_and_warnings() -> None:
    _auth()
    request = _request(
        status='submitted',
        serial=12,
        comment='for May',
        lines=[_line(to_purchase=True, purchase_order_detail=5, purchase_order=2)],
        warnings=['Product W-1 has no supplier'],
    )
    with _service(request) as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['purchase_request_id'], body['status'], body['serial']) == (1, 'submitted', 12)
    assert body['comment'] == 'for May'
    assert body['line_count'] == 1
    assert body['lines'][0]['purchase_request_detail_id'] == 1
    assert body['lines'][0]['quantity'] == '2.00'
    assert (body['lines'][0]['to_purchase'], body['lines'][0]['purchase_order']) == (True, 2)
    assert body['warnings'] == ['Product W-1 has no supplier']
    assert service['get_request'].await_args.args[1] == 1
    service['attach'].assert_awaited_once()


@pytest.mark.asyncio
async def test_list_returns_items_and_total() -> None:
    _auth()
    items = [_request(), _request(purchase_request_id=2, status='approved', approver=7)]
    with _service() as service:
        service['list_requests'].return_value = (items, 41)
        async with _client() as client:
            response = await client.get(BASE)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert set(body) == {'items', 'total'}
    assert body['total'] == 41
    assert [item['purchase_request_id'] for item in body['items']] == [1, 2]
    assert [item['status'] for item in body['items']] == ['draft', 'approved']
    assert 'lines' not in body['items'][0]


@pytest.mark.asyncio
async def test_list_passes_its_query_parameters_to_the_service() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(
                BASE,
                params={
                    'all': 'true',
                    'status': 'submitted',
                    'warehouse': 4,
                    'facility': 2,
                    'creator': 9,
                    'serial': 12,
                    'id': 33,
                    'from': '2026-09-01',
                    'to': '2026-09-30',
                    'skip': 10,
                    'limit': 5,
                },
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    kwargs = service['list_requests'].await_args.kwargs
    assert kwargs['current'].user_id == 'tester'
    assert {k: v for k, v in kwargs.items() if k != 'current'} == {
        'everyone': True,
        'state': 'submitted',
        'warehouse': 4,
        'facility': 2,
        'creator': 9,
        'serial': 12,
        'request_id': 33,
        'date_from': date(2026, 9, 1),
        'date_to': date(2026, 9, 30),
        'skip': 10,
        'limit': 5,
    }


@pytest.mark.asyncio
async def test_list_defaults() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            await client.get(BASE)

    kwargs = service['list_requests'].await_args.kwargs
    assert kwargs['everyone'] is False
    assert kwargs['state'] is None
    assert kwargs['request_id'] is None
    assert kwargs['date_from'] is None
    assert kwargs['date_to'] is None
    assert (kwargs['skip'], kwargs['limit']) == (0, 20)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params', [{'skip': -1}, {'limit': 0}, {'limit': 101}, {'from': 'yesterday'}, {'id': 'x'}]
)
async def test_list_with_bad_paging_or_filters_is_422(params: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(BASE, params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['list_requests'].assert_not_awaited()


@pytest.mark.asyncio
async def test_list_with_an_unknown_status_is_422() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='status must be one of draft, submitted')
    with _service() as service:
        service['list_requests'].side_effect = refusal
        async with _client() as client:
            response = await client.get(BASE, params={'status': 'paid'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert service['list_requests'].await_args.kwargs['state'] == 'paid'


@pytest.mark.asyncio
async def test_approval_queue_is_not_treated_as_an_id() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/approval-queue', params={'facility': 2, 'skip': 5})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['total'] == 1
    assert response.json()['items'][0]['purchase_request_id'] == 1
    assert service['approval_queue'].await_args.kwargs == {'facility': 2, 'skip': 5, 'limit': 20}
    service['get_request'].assert_not_awaited()


@pytest.mark.asyncio
async def test_approve_is_not_treated_as_an_id() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/approve', json={'ids': [1]})

    assert response.status_code == status.HTTP_200_OK, response.text
    service['get_request'].assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_request_is_404_on_every_id_route() -> None:
    _auth()
    with _service(found=False) as service:
        async with _client() as client:
            responses = [
                await client.get(f'{BASE}/9'),
                await client.put(f'{BASE}/9', json={'comment': 'x'}),
                await client.post(f'{BASE}/9/lines', json={'product': 3}),
                await client.put(f'{BASE}/9/lines/1', json={'quantity': '1'}),
                await client.delete(f'{BASE}/9/lines/1'),
                await client.post(f'{BASE}/9/submit'),
                await client.post(f'{BASE}/9/cancel'),
                await client.put(f'{BASE}/9/lines/1/to-purchase', json={'to_purchase': True}),
                await client.post(f'{BASE}/9/approve'),
                await client.post(f'{BASE}/9/reject', json={'reason': 'no'}),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 10
    assert {r.json()['detail'] for r in responses} == {'Purchase request not found'}
    for name in (
        'update',
        'add_line',
        'update_line',
        'remove_line',
        'submit',
        'cancel',
        'mark',
        'approve',
        'reject',
        'attach',
    ):
        service[name].assert_not_awaited()


# ── Writes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_201_with_the_draft() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 4, 'comment': 'c'})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['status'] == 'draft'
    assert response.json()['lines'][0]['product_code'] == 'W-1'
    args = service['create'].await_args
    assert (args.args[1].warehouse, args.args[1].comment) == (4, 'c')
    assert args.kwargs['current'].employee_id == 7


@pytest.mark.asyncio
async def test_create_without_a_warehouse_leaves_it_to_the_service() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(BASE, json={'comment': None})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert service['create'].await_args.args[1].warehouse is None


@pytest.mark.asyncio
async def test_create_on_an_unknown_warehouse_is_404() -> None:
    _auth()
    with _service() as service:
        service['create'].side_effect = HTTPException(status_code=404, detail='Warehouse not found')
        async with _client() as client:
            response = await client.post(BASE, json={'warehouse': 99, 'comment': None})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_update_returns_the_request() -> None:
    _auth()
    with _service(_request(comment='new')) as service:
        async with _client() as client:
            response = await client.put(f'{BASE}/1', json={'warehouse': 2, 'comment': 'new'})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['comment'] == 'new'
    args = service['update'].await_args
    assert args.args[1] == 1
    assert (args.args[2].warehouse, args.args[2].comment) == (2, 'new')


@pytest.mark.asyncio
async def test_line_routes_return_the_whole_request() -> None:
    _auth()
    request = _request(lines=[_line(quantity=Decimal('5.00'))])
    with _service(request) as service:
        async with _client() as client:
            added = await client.post(f'{BASE}/1/lines', json={'product': 3, 'quantity': '5'})
            updated = await client.put(f'{BASE}/1/lines/1', json={'quantity': '5.50'})
            removed = await client.delete(f'{BASE}/1/lines/1')

    for response in (added, updated, removed):
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()['lines'][0]['quantity'] == '5.00'
    assert service['add_line'].await_args.args[2].product == 3
    assert service['add_line'].await_args.args[2].quantity == Decimal('5')
    assert service['update_line'].await_args.args[1:3] == (1, 1)
    assert service['update_line'].await_args.args[3].quantity == Decimal('5.50')
    assert service['remove_line'].await_args.args[1:3] == (1, 1)


@pytest.mark.asyncio
async def test_add_line_needs_only_a_product() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/lines', json={'product': 3})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert service['add_line'].await_args.args[2].quantity is None


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Line not found')
    with _service() as service:
        service['update_line'].side_effect = error
        service['remove_line'].side_effect = error
        service['mark'].side_effect = error
        async with _client() as client:
            responses = [
                await client.put(f'{BASE}/1/lines/9', json={'quantity': '5'}),
                await client.delete(f'{BASE}/1/lines/9'),
                await client.put(f'{BASE}/1/lines/9/to-purchase', json={'to_purchase': False}),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 3


@pytest.mark.asyncio
async def test_submit_and_cancel_return_the_request() -> None:
    _auth()
    with _service() as service:
        service['submit'].return_value = _request(status='submitted', serial=5)
        service['cancel'].return_value = _request(status='cancelled')
        service['attach'].side_effect = lambda db, request: request
        async with _client() as client:
            submitted = await client.post(f'{BASE}/1/submit')
            cancelled = await client.post(f'{BASE}/1/cancel')

    assert (submitted.status_code, submitted.json()['status']) == (200, 'submitted')
    assert submitted.json()['serial'] == 5
    assert (cancelled.status_code, cancelled.json()['status']) == (200, 'cancelled')
    assert service['submit'].await_args.args[1] == 1
    assert service['cancel'].await_args.args[1] == 1


@pytest.mark.asyncio
async def test_mark_passes_the_flag_and_returns_the_request() -> None:
    _auth()
    request = _request(lines=[_line(to_purchase=True)])
    with _service(request) as service:
        async with _client() as client:
            response = await client.put(f'{BASE}/1/lines/4/to-purchase', json={'to_purchase': True})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['lines'][0]['to_purchase'] is True
    assert service['mark'].await_args.args[1:3] == (1, 4)
    assert service['mark'].await_args.kwargs['to_purchase'] is True


@pytest.mark.asyncio
async def test_approve_and_reject_return_the_request() -> None:
    _auth()
    approved = _request(status='approved', approver=7, approval_time=WHEN)
    with _service(approved) as service:
        async with _client() as client:
            first = await client.post(f'{BASE}/1/approve')
            second = await client.post(f'{BASE}/1/reject', json={'reason': '  too much  '})

    assert first.status_code == second.status_code == status.HTTP_200_OK
    assert first.json()['status'] == 'approved'
    assert first.json()['approver'] == 7
    assert first.json()['approval_time'] is not None
    assert service['approve'].await_args.args[1] == 1
    assert service['reject'].await_args.args[1] == 1
    assert service['reject'].await_args.args[2].reason == 'too much'


@pytest.mark.asyncio
async def test_bulk_approve_returns_each_result() -> None:
    _auth()
    results = [
        {'purchase_request_id': 1, 'approved': True, 'reason': None, 'code': None, 'params': None},
        {
            'purchase_request_id': 2,
            'approved': False,
            'reason': 'Not submitted',
            'code': 'purchase_request.not_submitted',
            'params': {'status': 'draft'},
        },
    ]
    with _service() as service:
        service['approve_many'].return_value = results
        async with _client() as client:
            response = await client.post(f'{BASE}/approve', json={'ids': [1, 2]})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {
        'results': [
            {
                'purchase_request_id': 1,
                'approved': True,
                'reason': None,
                'code': None,
                'params': None,
            },
            {
                'purchase_request_id': 2,
                'approved': False,
                'reason': 'Not submitted',
                'code': 'purchase_request.not_submitted',
                'params': {'status': 'draft'},
            },
        ]
    }
    assert service['approve_many'].await_args.args[1] == [1, 2]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'method', 'path', 'body', 'code'),
    [
        ('submit', 'post', '/1/submit', None, 409),
        ('submit', 'post', '/1/submit', None, 422),
        ('approve', 'post', '/1/approve', None, 409),
        ('approve', 'post', '/1/approve', None, 422),
        ('reject', 'post', '/1/reject', {'reason': 'no'}, 409),
        ('cancel', 'post', '/1/cancel', None, 409),
        ('cancel', 'post', '/1/cancel', None, 422),
        ('mark', 'put', '/1/lines/1/to-purchase', {'to_purchase': True}, 409),
        ('mark', 'put', '/1/lines/1/to-purchase', {'to_purchase': True}, 422),
        ('update', 'put', '/1', {'comment': 'x'}, 409),
        ('add_line', 'post', '/1/lines', {'product': 3}, 422),
        ('approve_many', 'post', '/approve', {'ids': [1]}, 409),
    ],
)
async def test_service_refusals_pass_through(
    action: str, method: str, path: str, body: dict | None, code: int
) -> None:
    _auth()
    refusal = HTTPException(status_code=code, detail='refused')
    with _service() as service:
        service[action].side_effect = refusal
        async with _client() as client:
            kwargs = {} if body is None else {'json': body}
            response = await getattr(client, method)(BASE + path, **kwargs)

    assert response.status_code == code
    assert response.json()['detail'] == 'refused'


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['', ' ', '   \t\n', None])
async def test_reject_with_a_blank_reason_is_422(reason: str | None) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/reject', json={'reason': reason})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['reject'].assert_not_awaited()


@pytest.mark.asyncio
async def test_reject_without_a_reason_or_with_a_long_one_is_422() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            missing = await client.post(f'{BASE}/1/reject', json={})
            long = await client.post(f'{BASE}/1/reject', json={'reason': 'x' * 501})

    assert missing.status_code == long.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['reject'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('ids', [[], list(range(1, 102)), ['x'], None])
async def test_bulk_approve_with_a_bad_ids_list_is_422(ids: list | None) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/approve', json={'ids': ids})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['approve_many'].assert_not_awaited()


@pytest.mark.asyncio
async def test_bulk_approve_accepts_100_ids() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/approve', json={'ids': list(range(1, 101))})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert len(service['approve_many'].await_args.args[1]) == 100


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('path', 'body'),
    [
        ('/lines', {'product': 3, 'quantity': '0'}),
        ('/lines', {'product': 3, 'quantity': '-1'}),
        ('/lines', {'product': 3, 'quantity': '1.005'}),
        ('/lines', {'quantity': '1'}),
        ('/lines/1', {'quantity': '0'}),
        ('/lines/1', {'quantity': '1.234'}),
    ],
)
async def test_line_quantity_zero_or_three_decimals_is_422(path: str, body: dict) -> None:
    _auth()
    method = 'post' if path == '/lines' else 'put'
    with _service() as service:
        async with _client() as client:
            response = await getattr(client, method)(f'{BASE}/1{path}', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['add_line'].assert_not_awaited()
    service['update_line'].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_line_quantity_with_two_decimals_is_accepted() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/lines', json={'product': 3, 'quantity': '0.25'})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert service['add_line'].await_args.args[2].quantity == Decimal('0.25')


@pytest.mark.asyncio
async def test_to_purchase_needs_a_boolean() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            missing = await client.put(f'{BASE}/1/lines/1/to-purchase', json={})
            wrong = await client.put(f'{BASE}/1/lines/1/to-purchase', json={'to_purchase': 'maybe'})

    assert missing.status_code == wrong.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['mark'].assert_not_awaited()
