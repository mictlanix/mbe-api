"""The /expense-vouchers routes (spec 032, US1–US3): the HTTP contract, services patched."""

from collections.abc import Generator
from datetime import date, datetime
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

BASE = '/api/v1/expense-vouchers'
SERVICE = 'app.services.expense_voucher_service'
WHEN = datetime(2026, 10, 27, 12, 0)
_PDF = b'%PDF-1.7 voucher'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True, rights: int | None = None, facility: int = 1) -> None:
    """`rights` is the privilege mask a non-administrator holds on every object."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=facility,
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
        expense_voucher_detail_id=1,
        expense=3,
        expense_name='VARIOS',
        amount=Decimal('100.50'),
        comment='Garrafones',
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _voucher(**overrides) -> SimpleNamespace:
    base = dict(
        expense_voucher_id=1,
        status='draft',
        voided=False,
        completed=False,
        cancelled=False,
        facility=1,
        cash_session=4,
        creator=7,
        creator_name='Ana Ruiz',
        date=WHEN,
        comment=None,
        total=Decimal('100.50'),
        line_count=1,
        lines=[_line()],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _confirmed(**overrides) -> SimpleNamespace:
    return _voucher(**{'status': 'completed', 'completed': True, **overrides})


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
    ('post', '/1/cancel', AccessRight.UPDATE),
    ('post', '/1/void', AccessRight.DELETE),
    ('get', '/1/ticket', AccessRight.READ),
]
BODIES = {
    ('post', ''): {},
    ('put', '/1'): {'comment': 'x'},
    ('post', '/1/lines'): {'expense': 3, 'amount': '1'},
    ('put', '/1/lines/1'): {'amount': '1'},
    ('post', '/1/void'): {'reason': 'error'},
}


def _body(method: str, path: str) -> dict:
    body = BODIES.get((method, path))
    return {} if body is None else {'json': body}


@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_a_token_it_is_401(method: str, path: str, right: AccessRight) -> None:
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_the_right_it_is_403(method: str, path: str, right: AccessRight) -> None:
    """Every other right on object 82 is held, and only the one the route needs is missing."""
    _auth(administrator=False, rights=0b1111 & ~int(right))
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_every_route_checks_its_right_on_object_82() -> None:
    """The grant each route declares: ExpenseTicket (82), with the contract's right."""
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
        path = route.path.replace('{voucher_id}', '1').replace('{line_id}', '1')
        for method in route.methods:
            found[(method, path)] = grants

    assert set(found) == set(expected)
    for key, right in expected.items():
        assert found[key] == [(SystemObject.EXPENSE_TICKET, right)], key


# ── Reads (FR-008) ────────────────────────────────────────────────────────────


async def test_get_returns_the_voucher_with_its_lines() -> None:
    _auth()
    with patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=_voucher())):
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['expense_voucher_id'], body['status'], body['voided']) == (1, 'draft', False)
    assert (body['creator_name'], body['total'], body['line_count']) == ('Ana Ruiz', '100.50', 1)
    assert body['lines'] == [
        {
            'expense_voucher_detail_id': 1,
            'expense': 3,
            'expense_name': 'VARIOS',
            'amount': '100.50',
            'comment': 'Garrafones',
        }
    ]


async def test_get_and_ticket_of_another_facility_are_403_for_a_non_administrator() -> None:
    _auth(administrator=False, rights=0b1111)
    with patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=_confirmed(facility=2))):
        async with _client() as client:
            responses = [await client.get(f'{BASE}/1'), await client.get(f'{BASE}/1/ticket')]

    assert [r.status_code for r in responses] == [status.HTTP_403_FORBIDDEN] * 2


async def test_an_administrator_reads_any_facility() -> None:
    _auth(facility=1)
    with patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=_voucher(facility=2))):
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK


async def test_unknown_voucher_is_404_on_every_route_that_names_one() -> None:
    _auth()
    missing = HTTPException(status_code=404, detail='Expense voucher not found')
    with (
        patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=None)),
        patch(f'{SERVICE}.update_voucher', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.add_line', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.update_line', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.remove_line', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.confirm_voucher', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.cancel_draft', AsyncMock(side_effect=missing)),
        patch(f'{SERVICE}.void_voucher', AsyncMock(side_effect=missing)),
    ):
        async with _client() as client:
            responses = [
                await client.get(f'{BASE}/9'),
                await client.put(f'{BASE}/9', json={'comment': 'x'}),
                await client.post(f'{BASE}/9/lines', json={'expense': 3, 'amount': '1'}),
                await client.put(f'{BASE}/9/lines/1', json={'amount': '1'}),
                await client.delete(f'{BASE}/9/lines/1'),
                await client.post(f'{BASE}/9/confirm'),
                await client.post(f'{BASE}/9/cancel'),
                await client.post(f'{BASE}/9/void', json={'reason': 'error'}),
                await client.get(f'{BASE}/9/ticket'),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 9
    assert responses[0].json()['detail'] == 'Expense voucher not found'


async def test_unknown_line_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Line not found')
    with (
        patch(f'{SERVICE}.update_line', AsyncMock(side_effect=error)),
        patch(f'{SERVICE}.remove_line', AsyncMock(side_effect=error)),
    ):
        async with _client() as client:
            updated = await client.put(f'{BASE}/1/lines/9', json={'amount': '5'})
            removed = await client.delete(f'{BASE}/1/lines/9')

    assert updated.status_code == removed.status_code == status.HTTP_404_NOT_FOUND


async def test_list_returns_summaries_and_passes_the_filters() -> None:
    _auth()
    items = [_confirmed(expense_voucher_id=2), _voucher()]
    with patch(f'{SERVICE}.list_vouchers', AsyncMock(return_value=(items, 2))) as listing:
        async with _client() as client:
            response = await client.get(
                BASE,
                params={
                    'status': 'completed',
                    'facility': 2,
                    'cash_session': 4,
                    'creator': 7,
                    'from': '2026-10-01',
                    'to': '2026-10-31',
                    'skip': 1,
                    'limit': 5,
                },
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['total'] == 2
    assert [item['expense_voucher_id'] for item in body['items']] == [2, 1]
    assert 'lines' not in body['items'][0]
    kwargs = listing.await_args.kwargs
    assert kwargs['state'] == 'completed'
    assert (kwargs['facility'], kwargs['cash_session'], kwargs['creator']) == (2, 4, 7)
    assert (kwargs['date_from'], kwargs['date_to']) == (date(2026, 10, 1), date(2026, 10, 31))
    assert (kwargs['skip'], kwargs['limit']) == (1, 5)


@pytest.mark.parametrize(('code', 'detail'), [(403, 'Other facility'), (422, 'Bad status')])
async def test_list_refusals_pass_through(code: int, detail: str) -> None:
    _auth(administrator=False, rights=0b1111)
    with patch(f'{SERVICE}.list_vouchers', AsyncMock(side_effect=HTTPException(code, detail))):
        async with _client() as client:
            response = await client.get(BASE, params={'facility': 2})

    assert response.status_code == code


# ── Writes (FR-001 to FR-003) ─────────────────────────────────────────────────


async def test_create_is_201_with_the_draft() -> None:
    _auth()
    with patch(f'{SERVICE}.create_voucher', AsyncMock(return_value=_voucher())) as create:
        async with _client() as client:
            response = await client.post(BASE, json={'comment': 'gasolina'})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['status'] == 'draft'
    assert create.await_args.args[1].comment == 'gasolina'


async def test_create_without_an_open_session_is_409() -> None:
    _auth()
    refusal = HTTPException(status_code=409, detail='An open cash session is required')
    with patch(f'{SERVICE}.create_voucher', AsyncMock(side_effect=refusal)):
        async with _client() as client:
            response = await client.post(BASE, json={})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == 'An open cash session is required'


@pytest.mark.parametrize(
    ('method', 'path', 'body'),
    [
        ('post', '', {'comment': 'x' * 501}),
        ('put', '/1', {'comment': 'x' * 501}),
        ('post', '/1/lines', {'expense': 3, 'amount': '0'}),
        ('post', '/1/lines', {'expense': 3, 'amount': '-1'}),
        ('post', '/1/lines', {'expense': 3, 'amount': '1.001'}),
        ('post', '/1/lines', {'expense': 3, 'amount': '100000000'}),
        ('post', '/1/lines', {'amount': '1'}),
        ('post', '/1/lines', {'expense': 3, 'amount': '1', 'comment': 'x' * 501}),
        ('put', '/1/lines/1', {'amount': '0'}),
        ('put', '/1/lines/1', {'amount': '1.001'}),
        ('post', '/1/void', {}),
        ('post', '/1/void', {'reason': ''}),
        ('post', '/1/void', {'reason': 'x' * 501}),
    ],
)
async def test_a_malformed_body_is_422(method: str, path: str, body: dict) -> None:
    _auth()
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


async def test_writes_return_the_whole_voucher() -> None:
    _auth()
    voucher = _voucher()
    with (
        patch(f'{SERVICE}.update_voucher', AsyncMock(return_value=voucher)),
        patch(f'{SERVICE}.add_line', AsyncMock(return_value=voucher)) as add,
        patch(f'{SERVICE}.update_line', AsyncMock(return_value=voucher)) as change,
        patch(f'{SERVICE}.remove_line', AsyncMock(return_value=voucher)),
        patch(f'{SERVICE}.confirm_voucher', AsyncMock(return_value=_confirmed())),
        patch(f'{SERVICE}.cancel_draft', AsyncMock(return_value=voucher)),
    ):
        async with _client() as client:
            responses = [
                await client.put(f'{BASE}/1', json={'comment': 'x'}),
                await client.post(f'{BASE}/1/lines', json={'expense': 3, 'amount': '100.50'}),
                await client.put(f'{BASE}/1/lines/1', json={'comment': 'y'}),
                await client.delete(f'{BASE}/1/lines/1'),
                await client.post(f'{BASE}/1/confirm'),
                await client.post(f'{BASE}/1/cancel'),
            ]

    for response in responses:
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()['lines'][0]['expense_voucher_detail_id'] == 1
    assert responses[4].json()['status'] == 'completed'
    assert add.await_args.args[2].amount == Decimal('100.50')
    assert change.await_args.args[2] == 1
    assert change.await_args.args[3].model_dump(exclude_unset=True) == {'comment': 'y'}


@pytest.mark.parametrize(
    ('service', 'method', 'path', 'code', 'detail'),
    [
        ('update_voucher', 'put', '/1', 403, "Only the voucher's cashier can change it"),
        ('add_line', 'post', '/1/lines', 409, 'Only a draft expense voucher can be changed'),
        ('add_line', 'post', '/1/lines', 422, 'Unknown expense category'),
        ('confirm_voucher', 'post', '/1/confirm', 409, 'An open cash session is required'),
        ('confirm_voucher', 'post', '/1/confirm', 409, 'The total must be above 0'),
        ('confirm_voucher', 'post', '/1/confirm', 403, "Only the voucher's cashier can change it"),
        ('cancel_draft', 'post', '/1/cancel', 409, 'Only a draft expense voucher can be changed'),
        ('void_voucher', 'post', '/1/void', 409, 'The cash session is closed'),
        ('void_voucher', 'post', '/1/void', 409, 'Only a confirmed voucher can be voided'),
        ('void_voucher', 'post', '/1/void', 403, 'Document belongs to another facility'),
    ],
)
async def test_service_refusals_pass_through(
    service: str, method: str, path: str, code: int, detail: str
) -> None:
    _auth()
    with patch(f'{SERVICE}.{service}', AsyncMock(side_effect=HTTPException(code, detail))):
        async with _client() as client:
            response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == code
    assert response.json()['detail'] == detail


# ── Void (FR-004) ─────────────────────────────────────────────────────────────


async def test_void_passes_the_reason_and_returns_the_voided_voucher() -> None:
    _auth()
    voided = _confirmed(status='cancelled', cancelled=True, voided=True)
    with patch(f'{SERVICE}.void_voucher', AsyncMock(return_value=voided)) as void:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/void', json={'reason': 'Capturado dos veces'})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert (response.json()['status'], response.json()['voided']) == ('cancelled', True)
    assert void.await_args.args[1:] == (1, 'Capturado dos veces')


# ── Ticket (FR-009) ───────────────────────────────────────────────────────────


async def test_the_ticket_of_a_confirmed_voucher_is_a_pdf() -> None:
    _auth()
    voucher = _confirmed()
    with (
        patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=voucher)),
        patch(
            'app.services.print_contexts.expense_voucher_context',
            AsyncMock(return_value=('expense_voucher_ticket.html', {})),
        ) as context,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with _client() as client:
            response = await client.get(f'{BASE}/1/ticket')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert response.content == _PDF
    assert context.await_args.args[1] is voucher
    assert render.await_args.args[0] == 'expense_voucher_ticket.html'


@pytest.mark.parametrize(
    'voucher',
    [_voucher(), _voucher(status='cancelled', cancelled=True), _confirmed(cancelled=True)],
    ids=['draft', 'cancelled-draft', 'voided'],
)
async def test_the_ticket_of_anything_but_a_confirmed_voucher_is_409(
    voucher: SimpleNamespace,
) -> None:
    _auth()
    with (
        patch(f'{SERVICE}.get_voucher', AsyncMock(return_value=voucher)),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with _client() as client:
            response = await client.get(f'{BASE}/1/ticket')

    assert response.status_code == status.HTTP_409_CONFLICT
    render.assert_not_awaited()
