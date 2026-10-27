"""The /supplier-payments routes (spec 032, US4–US6): the HTTP contract, with the service patched.

Behaviour against a real database is in tests/unit/test_supplier_payments.py and
tests/integration/test_supplier_payment_flow.py.
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

BASE = '/api/v1/supplier-payments'
SERVICE = 'app.services.supplier_payment_service'
WHEN = datetime(2026, 10, 27, 12, 0)


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


def _application(**overrides) -> SimpleNamespace:
    base = dict(
        purchase_order_payment_id=4,
        purchase_order=9,
        amount=Decimal('6000.00'),
        applier=7,
        date=WHEN,
        cancelled=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _payment(**overrides) -> SimpleNamespace:
    """Every field of SupplierPaymentResponse."""
    base = dict(
        supplier_payment_id=1,
        facility=1,
        serial=3,
        status='completed',
        supplier=5,
        supplier_name='Acme',
        amount=Decimal('10000.0000'),
        method=3,
        date=WHEN,
        reference='SPEI 1',
        comment=None,
        cash_session=None,
        creator=7,
        applied=Decimal('6000.00'),
        credit=Decimal('4000.00'),
        applications=[_application()],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _summary() -> dict:
    return dict(
        supplier=5,
        supplier_name='Acme',
        cutoff=WHEN,
        owed=Decimal('5000.00'),
        credit=Decimal('1000.00'),
        net=Decimal('4000.00'),
        orders=[
            dict(
                purchase_order_id=9,
                confirmed=WHEN,
                total=Decimal('8000.00'),
                applied=Decimal('3000.00'),
                balance=Decimal('5000.00'),
            )
        ],
        payments=[
            dict(
                supplier_payment_id=1,
                facility=1,
                serial=3,
                date=WHEN,
                amount=Decimal('10000.0000'),
                credit=Decimal('1000.00'),
            )
        ],
    )


@contextmanager
def _service(payment: SimpleNamespace | None = None, *, found: bool = True):
    payment = payment or _payment()
    mocks: dict[str, AsyncMock] = {}
    with ExitStack() as stack:
        for name, value in {
            'get_payment': payment if found else None,
            'create_payment': payment,
            'apply_payment': payment,
            'reverse_application': payment,
            'cancel_payment': payment,
            'list_payments': ([payment], 1),
            'supplier_balance': _summary(),
        }.items():
            mocks[name] = stack.enter_context(
                patch(f'{SERVICE}.{name}', AsyncMock(return_value=value))
            )
        yield mocks


# ── Authentication and rights ─────────────────────────────────────────────────

ROUTES = [
    ('get', '', AccessRight.READ),
    ('post', '', AccessRight.CREATE),
    ('get', '/suppliers/5/balance', AccessRight.READ),
    ('get', '/1', AccessRight.READ),
    ('post', '/1/applications', AccessRight.UPDATE),
    ('post', '/1/applications/4/reverse', AccessRight.DELETE),
    ('post', '/1/cancel', AccessRight.DELETE),
]
APPLY = {'applications': [{'purchase_order': 9, 'amount': '6000'}]}
BODIES = {
    ('post', ''): {'supplier': 5, 'amount': '10000', 'method': 3},
    ('post', '/1/applications'): APPLY,
    ('post', '/1/applications/4/reverse'): {'reason': 'wrong PO'},
    ('post', '/1/cancel'): {'reason': 'duplicated'},
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
    names = {'/suppliers/5': '/suppliers/{supplier_id}', '/1': '/{payment_id}'}
    declared = set()
    for method, path, _ in ROUTES:
        path = re.sub(r'^/suppliers/5|^/1', lambda m: names[m.group(0)], path)
        declared.add((method, path.replace('/applications/4', '/applications/{application_id}')))
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
    _auth(administrator=False, rights=0 if held == 'nothing' else ALL_RIGHTS & ~int(right))
    with _service() as service:
        async with _client() as client:
            response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code == status.HTTP_403_FORBIDDEN
    for mock in service.values():
        mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_the_exact_right_is_enough(method: str, path: str, right: AccessRight) -> None:
    _auth(administrator=False, rights=int(right))
    with _service():
        async with _client() as client:
            response = await getattr(client, method)(BASE + path, **_body(method, path))

    assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), response.text


# ── Reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_the_payment_with_its_applications() -> None:
    _auth()
    with _service():
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['supplier_payment_id'], body['facility'], body['serial']) == (1, 1, 3)
    assert (body['status'], body['supplier'], body['supplier_name']) == ('completed', 5, 'Acme')
    assert (body['amount'], body['method'], body['reference']) == ('10000.0000', 3, 'SPEI 1')
    assert (body['applied'], body['credit']) == ('6000.00', '4000.00')
    assert (body['cash_session'], body['creator'], body['comment']) == (None, 7, None)
    assert body['applications'] == [
        {
            'purchase_order_payment_id': 4,
            'purchase_order': 9,
            'amount': '6000.00',
            'applier': 7,
            'date': '2026-10-27T12:00:00',
            'cancelled': False,
        }
    ]


@pytest.mark.asyncio
async def test_list_returns_summaries_and_total() -> None:
    _auth()
    with _service() as service:
        service['list_payments'].return_value = ([_payment(), _payment(supplier_payment_id=2)], 9)
        async with _client() as client:
            response = await client.get(BASE)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['total'] == 9
    assert [item['supplier_payment_id'] for item in body['items']] == [1, 2]
    assert 'applications' not in body['items'][0]
    assert body['items'][0]['credit'] == '4000.00'


@pytest.mark.asyncio
async def test_list_passes_its_filters_to_the_service() -> None:
    _auth()
    params = {
        'supplier': 5,
        'facility': 2,
        'method': 1,
        'status': 'cancelled',
        'from': '2026-10-01',
        'to': '2026-10-31',
        'skip': 10,
        'limit': 5,
    }
    with _service() as service:
        async with _client() as client:
            response = await client.get(BASE, params=params)

    assert response.status_code == status.HTTP_200_OK, response.text
    assert service['list_payments'].await_args.kwargs == {
        'supplier': 5,
        'facility': 2,
        'method': 1,
        'state': 'cancelled',
        'date_from': date(2026, 10, 1),
        'date_to': date(2026, 10, 31),
        'skip': 10,
        'limit': 5,
    }


@pytest.mark.asyncio
async def test_list_defaults() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            await client.get(BASE)

    kwargs = service['list_payments'].await_args.kwargs
    assert {k: v for k, v in kwargs.items() if v is not None} == {'skip': 0, 'limit': 20}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params',
    [{'status': 'draft'}, {'skip': -1}, {'limit': 0}, {'limit': 101}, {'from': 'x'}],
)
async def test_list_with_bad_filters_is_422(params: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(BASE, params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['list_payments'].assert_not_awaited()


@pytest.mark.asyncio
async def test_the_supplier_balance() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/suppliers/5/balance')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert service['supplier_balance'].await_args.args[1] == 5
    body = response.json()
    assert (body['supplier'], body['supplier_name']) == (5, 'Acme')
    assert (body['owed'], body['credit'], body['net']) == ('5000.00', '1000.00', '4000.00')
    assert body['cutoff'] == '2026-10-27T12:00:00'
    assert body['orders'] == [
        {
            'purchase_order_id': 9,
            'confirmed': '2026-10-27T12:00:00',
            'total': '8000.00',
            'applied': '3000.00',
            'balance': '5000.00',
        }
    ]
    assert body['payments'][0] == {
        'supplier_payment_id': 1,
        'facility': 1,
        'serial': 3,
        'date': '2026-10-27T12:00:00',
        'amount': '10000.0000',
        'credit': '1000.00',
    }


# ── Writes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_201_and_passes_the_body() -> None:
    _auth()
    body = {
        'supplier': 5,
        'amount': '10000',
        'method': 3,
        'date': '2026-10-20T09:00:00',
        'reference': 'SPEI 1',
        'comment': 'Factura A',
        'facility': 2,
        'applications': [{'purchase_order': 9, 'amount': '6000'}],
    }
    with _service() as service:
        async with _client() as client:
            response = await client.post(BASE, json=body)

    assert response.status_code == status.HTTP_201_CREATED, response.text
    data = service['create_payment'].await_args.args[1]
    assert (data.supplier, data.amount, data.method, data.facility) == (5, Decimal(10000), 3, 2)
    assert data.applications[0].purchase_order == 9
    assert service['create_payment'].await_args.kwargs['current'].employee_id == 7


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'change',
    [
        {'amount': '0'},
        {'amount': '1.001'},
        {'amount': '100000000'},
        {'method': 77},
        {'date': '2999-01-01T00:00:00'},
        {'method': 1, 'date': '2026-10-01T00:00:00'},
        {'method': 1, 'facility': 1},
        {'reference': 'x' * 51},
        {'comment': 'x' * 501},
        {'applications': [{'purchase_order': 9, 'amount': '1'}] * 2},
        {'applications': [{'purchase_order': n, 'amount': '1'} for n in range(51)]},
    ],
)
async def test_a_bad_create_body_is_422(change: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(
                BASE, json={'supplier': 5, 'amount': '10', 'method': 3} | change
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['create_payment'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'applications': []},
        {'applications': [{'purchase_order': 9, 'amount': '1'}] * 2},
        {'applications': [{'purchase_order': 9, 'amount': '-1'}]},
        {'applications': [{'purchase_order': n, 'amount': '1'} for n in range(51)]},
    ],
)
async def test_a_bad_applications_body_is_422(body: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/applications', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['apply_payment'].assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_reverse_and_cancel_pass_their_arguments() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            applied = await client.post(f'{BASE}/1/applications', json=APPLY)
            reversed_ = await client.post(
                f'{BASE}/1/applications/4/reverse', json={'reason': 'wrong PO'}
            )
            cancelled = await client.post(f'{BASE}/1/cancel', json={'reason': 'duplicated'})

    assert [r.status_code for r in (applied, reversed_, cancelled)] == [200] * 3
    assert service['apply_payment'].await_args.args[1] == 1
    assert service['apply_payment'].await_args.args[2].applications[0].purchase_order == 9
    assert service['reverse_application'].await_args.args[1:] == (1, 4, 'wrong PO')
    assert service['cancel_payment'].await_args.args[1:] == (1, 'duplicated')


@pytest.mark.asyncio
@pytest.mark.parametrize('path', ['/1/applications/4/reverse', '/1/cancel'])
@pytest.mark.parametrize('body', [{}, {'reason': ''}, {'reason': 'x' * 501}])
async def test_a_missing_or_overlong_reason_is_422(path: str, body: dict) -> None:
    _auth()
    with _service():
        async with _client() as client:
            response = await client.post(BASE + path, json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_an_unknown_payment_is_404_on_get() -> None:
    _auth()
    with _service(found=False):
        async with _client() as client:
            response = await client.get(f'{BASE}/9')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Supplier payment not found'}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'path', 'body'),
    [
        ('create_payment', '', BODIES[('post', '')]),
        ('apply_payment', '/1/applications', APPLY),
        ('reverse_application', '/1/applications/4/reverse', {'reason': 'x'}),
        ('cancel_payment', '/1/cancel', {'reason': 'x'}),
    ],
)
@pytest.mark.parametrize('code', [404, 409, 422])
async def test_service_refusals_pass_through(action: str, path: str, body: dict, code: int) -> None:
    _auth()
    with _service() as service:
        service[action].side_effect = HTTPException(status_code=code, detail='refused')
        async with _client() as client:
            response = await client.post(BASE + path, json=body)

    assert response.status_code == code
    assert response.json() == {'detail': 'refused'}


@pytest.mark.asyncio
async def test_the_balance_of_an_unknown_supplier_is_404() -> None:
    _auth()
    with _service() as service:
        service['supplier_balance'].side_effect = HTTPException(404, 'Supplier not found')
        async with _client() as client:
            response = await client.get(f'{BASE}/suppliers/9/balance')

    assert response.status_code == status.HTTP_404_NOT_FOUND
