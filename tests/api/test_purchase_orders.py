"""The /purchase-orders routes (spec 031): the HTTP contract, with the services patched.

Behaviour against a real database is covered in tests/integration/test_purchase_order_*_flow.py and
tests/integration/test_purchase_cost_flow.py.
"""

import re
from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.clock import local_today
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import AccessRight
from app.main import app
from app.services import mail

BASE = '/api/v1/purchase-orders'
SERVICE = 'app.services.purchase_order_service'
COST_SERVICE = 'app.services.purchase_cost_service'
PAYMENT_SERVICE = 'app.services.supplier_payment_service'
WHEN = datetime(2026, 10, 1, 10, 0)
PDF = b'%PDF-1.7 fake'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    """A mailer is configured unless a test says otherwise."""
    app.dependency_overrides[mail.get_mailer] = lambda: object()
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
    """Every field of PurchaseOrderLine."""
    base = dict(
        purchase_order_detail_id=1,
        product=3,
        product_code='W-1',
        product_name='Widget',
        warehouse=1,
        quantity=Decimal('2.0000'),
        price=Decimal('10.0000000'),
        tax_rate=Decimal('0.1600'),
        amount=Decimal('20.00'),
        purchase_request_detail=None,
        purchase_request=None,
        quantity_received=None,
        quantity_pending=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _order(**overrides) -> SimpleNamespace:
    """Every field of PurchaseOrderResponse."""
    base = dict(
        purchase_order_id=1,
        status='draft',
        supplier=5,
        supplier_name='Acme',
        estimated_receipt_date=None,
        comment=None,
        creator=7,
        updater=7,
        creation_time=WHEN,
        modification_time=WHEN,
        line_count=1,
        subtotal=Decimal('20.00'),
        tax=Decimal('3.20'),
        total=Decimal('23.20'),
        lines=[_line()],
        warnings=[],
        skipped=[],
        receipts=[],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _group(**overrides) -> dict:
    """A ToPurchaseGroup, its lines objects with the ToPurchaseLine fields."""
    base = dict(
        supplier=5,
        supplier_name='Acme',
        facility=1,
        line_count=1,
        estimated_total=Decimal('20.00'),
        lines=[
            SimpleNamespace(
                purchase_request_detail_id=11,
                purchase_request=2,
                serial=12,
                product=3,
                product_code='W-1',
                product_name='Widget',
                quantity=Decimal('2.00'),
                warehouse=1,
                cost=Decimal('10.00'),
            )
        ],
    )
    base.update(overrides)
    return base


def _change(**overrides) -> SimpleNamespace:
    """Every field of CostChange."""
    base = dict(
        product_cost_change_id=1,
        product=3,
        receipt=4,
        purchase_order_detail=None,
        previous_cost=Decimal('10.00'),
        new_cost=Decimal('12.00'),
        quantity_on_hand=Decimal('5.00'),
        quantity_received=Decimal('5.00'),
        unit_cost=Decimal('12.00'),
        reverted_change=None,
        creation_time=WHEN,
        creator=7,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _balance() -> dict:
    """Every field of PurchaseOrderBalance."""
    return dict(
        purchase_order_id=1,
        supplier=5,
        status='completed',
        counts=True,
        total=Decimal('8000.00'),
        applied=Decimal('3000.00'),
        balance=Decimal('5000.00'),
        applications=[
            SimpleNamespace(
                purchase_order_payment_id=4,
                purchase_order=1,
                amount=Decimal('3000.00'),
                applier=7,
                date=WHEN,
                cancelled=False,
                supplier_payment=2,
                serial=6,
            )
        ],
    )


@contextmanager
def _service(order: SimpleNamespace | None = None, *, found: bool = True):
    """Patch both services: every state-changing call answers with `order`."""
    order = order or _order()
    mocks: dict[str, AsyncMock] = {}
    with ExitStack() as stack:
        for name, value in {
            'get_order': order if found else None,
            'attach': order,
            'create': order,
            'create_from_group': order,
            'update': order,
            'add_line': order,
            'update_line': order,
            'remove_line': order,
            'add_request_lines': order,
            'confirm': order,
            'cancel': order,
            'list_orders': ([order], 1),
            'to_purchase_groups': [],
            'document': (PDF, {}),
            'email_order': WHEN,
        }.items():
            mocks[name] = stack.enter_context(
                patch(f'{SERVICE}.{name}', AsyncMock(return_value=value))
            )
        mocks['history'] = stack.enter_context(
            patch(f'{COST_SERVICE}.history', AsyncMock(return_value=([_change()], 1)))
        )
        mocks['purchase_order_balance'] = stack.enter_context(
            patch(
                f'{PAYMENT_SERVICE}.purchase_order_balance',
                AsyncMock(return_value=_balance()),
            )
        )
        yield mocks


# ── Authentication and rights ─────────────────────────────────────────────────

ROUTES = [
    ('get', '', AccessRight.READ),
    ('post', '', AccessRight.CREATE),
    ('get', '/to-purchase', AccessRight.READ),
    ('post', '/to-purchase', AccessRight.CREATE),
    ('get', '/cost-changes?product=3', AccessRight.READ),
    ('get', '/1', AccessRight.READ),
    ('put', '/1', AccessRight.UPDATE),
    ('post', '/1/lines', AccessRight.UPDATE),
    ('put', '/1/lines/1', AccessRight.UPDATE),
    ('delete', '/1/lines/1', AccessRight.UPDATE),
    ('post', '/1/request-lines', AccessRight.UPDATE),
    ('post', '/1/confirm', AccessRight.UPDATE),
    ('post', '/1/cancel', AccessRight.DELETE),
    ('get', '/1/document', AccessRight.READ),
    ('post', '/1/email', AccessRight.UPDATE),
    # Supplier payments' right (21), not the PO's (spec 032, FR-019).
    ('get', '/1/payments', AccessRight.READ),
]
BODIES = {
    ('post', ''): {'supplier': 5, 'comment': 'x'},
    ('post', '/to-purchase'): {'supplier': 5, 'facility': 1},
    ('put', '/1'): {'comment': 'x'},
    ('post', '/1/lines'): {'product': 3, 'quantity': '1'},
    ('put', '/1/lines/1'): {'quantity': '1'},
    ('post', '/1/request-lines'): {'purchase_request': 2},
    ('post', '/1/email'): {'to': ['a@x.mx']},
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
        (method, re.sub(r'/\d+', lambda m: '/{' + next(names) + '}', path.split('?')[0]))
        for method, path, _ in ROUTES
        for names in [iter(['order_id', 'line_id'])]
    }
    assert len(ROUTES) == 16
    assert served == declared


@pytest.mark.asyncio
@pytest.mark.parametrize(('method', 'path', 'right'), ROUTES)
async def test_without_a_token_it_is_401(method: str, path: str, right: AccessRight) -> None:
    async with _client() as client:
        response = await getattr(client, method)(BASE + path, **_body(method, path.split('?')[0]))

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
            response = await getattr(client, method)(
                BASE + path, **_body(method, path.split('?')[0])
            )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    service['get_order'].assert_not_awaited()


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
            response = await getattr(client, method)(
                BASE + path, **_body(method, path.split('?')[0])
            )

    assert response.status_code in (status.HTTP_200_OK, status.HTTP_201_CREATED), response.text


# ── Reads ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_returns_the_order_with_its_lines_and_warnings() -> None:
    _auth()
    order = _order(
        status='completed',
        comment='for May',
        estimated_receipt_date=WHEN,
        lines=[
            _line(
                purchase_request_detail=11,
                purchase_request=2,
                quantity_received=Decimal('1.0000'),
                quantity_pending=Decimal('1.0000'),
            )
        ],
        warnings=['Product W-1 has no supplier'],
    )
    with _service(order) as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/1')

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert (body['purchase_order_id'], body['status'], body['supplier']) == (1, 'completed', 5)
    assert (body['supplier_name'], body['comment']) == ('Acme', 'for May')
    assert body['estimated_receipt_date'] is not None
    assert (body['subtotal'], body['tax'], body['total']) == ('20.00', '3.20', '23.20')
    assert body['line_count'] == 1
    line = body['lines'][0]
    assert (line['purchase_order_detail_id'], line['product_code']) == (1, 'W-1')
    assert (line['quantity'], line['price'], line['amount']) == ('2.0000', '10.0000000', '20.00')
    assert line['tax_rate'] == '0.1600'
    assert (line['purchase_request_detail'], line['purchase_request']) == (11, 2)
    assert (line['quantity_received'], line['quantity_pending']) == ('1.0000', '1.0000')
    assert body['warnings'] == ['Product W-1 has no supplier']
    assert (body['skipped'], body['receipts']) == ([], [])
    assert service['get_order'].await_args.args[1] == 1
    service['attach'].assert_awaited_once()


@pytest.mark.asyncio
async def test_skipped_lines_and_generated_receipts_come_through() -> None:
    _auth()
    order = _order(
        skipped=[{'purchase_request_detail': 11, 'reason': 'Already on order 4'}],
        receipts=[{'inventory_receipt_id': 8, 'warehouse': 2, 'facility': 1, 'line_count': 3}],
    )
    with _service(order):
        async with _client() as client:
            response = await client.post(f'{BASE}/1/confirm')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['skipped'] == [
        {'purchase_request_detail': 11, 'reason': 'Already on order 4'}
    ]
    assert response.json()['receipts'] == [
        {'inventory_receipt_id': 8, 'warehouse': 2, 'facility': 1, 'line_count': 3}
    ]


@pytest.mark.asyncio
async def test_list_returns_items_and_total() -> None:
    _auth()
    items = [_order(), _order(purchase_order_id=2, status='cancelled')]
    with _service() as service:
        service['list_orders'].return_value = (items, 41)
        async with _client() as client:
            response = await client.get(BASE)

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert set(body) == {'items', 'total'}
    assert body['total'] == 41
    assert [item['purchase_order_id'] for item in body['items']] == [1, 2]
    assert [item['status'] for item in body['items']] == ['draft', 'cancelled']
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
                    'status': 'completed',
                    'supplier': 5,
                    'creator': 9,
                    'id': 33,
                    'from': '2026-09-01',
                    'to': '2026-09-30',
                    'skip': 10,
                    'limit': 5,
                },
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    kwargs = service['list_orders'].await_args.kwargs
    assert kwargs['current'].user_id == 'tester'
    assert {k: v for k, v in kwargs.items() if k != 'current'} == {
        'everyone': True,
        'state': 'completed',
        'supplier': 5,
        'creator': 9,
        'order_id': 33,
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

    kwargs = service['list_orders'].await_args.kwargs
    assert kwargs['everyone'] is False
    assert kwargs['state'] is None
    assert kwargs['supplier'] is None
    assert kwargs['order_id'] is None
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
    service['list_orders'].assert_not_awaited()


@pytest.mark.asyncio
async def test_list_with_an_unknown_status_is_422() -> None:
    _auth()
    refusal = HTTPException(status_code=422, detail='status must be one of draft, completed')
    with _service() as service:
        service['list_orders'].side_effect = refusal
        async with _client() as client:
            response = await client.get(BASE, params={'status': 'bogus'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert service['list_orders'].await_args.kwargs['state'] == 'bogus'


@pytest.mark.asyncio
async def test_to_purchase_returns_the_groups_and_is_not_treated_as_an_id() -> None:
    _auth()
    with _service() as service:
        service['to_purchase_groups'].return_value = [_group(), _group(supplier=None)]
        async with _client() as client:
            response = await client.get(f'{BASE}/to-purchase', params={'facility': 2})

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert set(body) == {'groups'}
    assert [group['supplier'] for group in body['groups']] == [5, None]
    group = body['groups'][0]
    assert (group['supplier_name'], group['facility'], group['line_count']) == ('Acme', 1, 1)
    assert group['estimated_total'] == '20.00'
    assert group['lines'][0]['purchase_request_detail_id'] == 11
    assert group['lines'][0]['product_code'] == 'W-1'
    assert group['lines'][0]['cost'] == '10.00'
    assert service['to_purchase_groups'].await_args.kwargs == {'facility': 2}
    service['get_order'].assert_not_awaited()


@pytest.mark.asyncio
async def test_to_purchase_without_a_facility_asks_for_all() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/to-purchase')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json() == {'groups': []}
    assert service['to_purchase_groups'].await_args.kwargs == {'facility': None}


@pytest.mark.asyncio
async def test_cost_changes_returns_items_and_total_and_is_not_treated_as_an_id() -> None:
    _auth()
    with _service() as service:
        service['history'].return_value = ([_change(), _change(reverted_change=1)], 9)
        async with _client() as client:
            response = await client.get(
                f'{BASE}/cost-changes', params={'product': 3, 'skip': 5, 'limit': 2}
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert set(body) == {'items', 'total'}
    assert body['total'] == 9
    assert [item['reverted_change'] for item in body['items']] == [None, 1]
    assert body['items'][0]['previous_cost'] == '10.00'
    assert body['items'][0]['new_cost'] == '12.00'
    assert body['items'][0]['receipt'] == 4
    assert service['history'].await_args.args[1] == 3
    assert service['history'].await_args.kwargs == {'skip': 5, 'limit': 2}
    service['get_order'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params',
    [{}, {'product': 'x'}, {'product': 3, 'skip': -1}, {'product': 3, 'limit': 0}],
)
async def test_cost_changes_with_bad_parameters_is_422(params: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/cost-changes', params=params)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['history'].assert_not_awaited()


@pytest.mark.asyncio
async def test_cost_changes_of_an_unknown_product_is_404() -> None:
    _auth()
    with _service() as service:
        service['history'].side_effect = HTTPException(status_code=404, detail='Product not found')
        async with _client() as client:
            response = await client.get(f'{BASE}/cost-changes', params={'product': 99})

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Product not found'}


@pytest.mark.asyncio
async def test_unknown_order_is_404_on_every_id_route() -> None:
    _auth()
    with _service(found=False) as service:
        async with _client() as client:
            responses = [
                await client.get(f'{BASE}/9'),
                await client.put(f'{BASE}/9', json={'comment': 'x'}),
                await client.post(f'{BASE}/9/lines', json={'product': 3, 'quantity': '1'}),
                await client.put(f'{BASE}/9/lines/1', json={'quantity': '1'}),
                await client.delete(f'{BASE}/9/lines/1'),
                await client.post(f'{BASE}/9/request-lines', json={'purchase_request': 2}),
                await client.post(f'{BASE}/9/confirm'),
                await client.post(f'{BASE}/9/cancel'),
                await client.get(f'{BASE}/9/document'),
                await client.post(f'{BASE}/9/email', json={'to': ['a@x.mx']}),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 10
    assert {r.json()['detail'] for r in responses} == {'Purchase order not found'}
    for name in (
        'update',
        'add_line',
        'update_line',
        'remove_line',
        'add_request_lines',
        'confirm',
        'cancel',
        'document',
        'email_order',
        'attach',
    ):
        service[name].assert_not_awaited()


# ── Payments (spec 032, US6) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_payments_returns_the_balance_and_the_applications() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/1/payments')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert service['purchase_order_balance'].await_args.args[1].purchase_order_id == 1
    body = response.json()
    assert (body['purchase_order_id'], body['supplier'], body['status']) == (1, 5, 'completed')
    assert (body['counts'], body['total'], body['applied'], body['balance']) == (
        True,
        '8000.00',
        '3000.00',
        '5000.00',
    )
    assert body['applications'] == [
        {
            'purchase_order_payment_id': 4,
            'purchase_order': 1,
            'amount': '3000.00',
            'applier': 7,
            'date': '2026-10-01T10:00:00',
            'cancelled': False,
            'supplier_payment': 2,
            'serial': 6,
        }
    ]


@pytest.mark.asyncio
async def test_payments_of_an_order_that_does_not_count_has_no_balance() -> None:
    _auth()
    with _service() as service:
        service['purchase_order_balance'].return_value = _balance() | {
            'counts': False,
            'balance': None,
            'applications': [],
        }
        async with _client() as client:
            response = await client.get(f'{BASE}/1/payments')

    assert (response.json()['counts'], response.json()['balance']) == (False, None)


@pytest.mark.asyncio
async def test_payments_of_an_unknown_order_is_404() -> None:
    _auth()
    with _service(found=False) as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/9/payments')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    service['purchase_order_balance'].assert_not_awaited()


# ── Document ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_document_is_the_pdf_inline() -> None:
    _auth()
    with _service(_order(purchase_order_id=7)) as service:
        async with _client() as client:
            response = await client.get(f'{BASE}/7/document')

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="orden-compra-00000007.pdf"'
    assert response.content == PDF
    assert service['document'].await_args.args[1].purchase_order_id == 7


@pytest.mark.asyncio
async def test_the_document_of_an_unconfirmed_order_passes_the_service_409_through() -> None:
    _auth()
    with _service() as service:
        service['document'].side_effect = HTTPException(status_code=409, detail='Not confirmed')
        async with _client() as client:
            response = await client.get(f'{BASE}/1/document')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': 'Not confirmed'}


# ── Email ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_email_sends_to_the_addresses_and_answers_the_recipients_and_time() -> None:
    _auth()
    mailer = object()
    app.dependency_overrides[mail.get_mailer] = lambda: mailer
    with _service() as service:
        async with _client() as client:
            response = await client.post(
                f'{BASE}/1/email',
                json={'to': ['a@x.mx', ' B@y.mx '], 'message': 'Here it is'},
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    body = response.json()
    assert body['recipients'] == ['a@x.mx', 'B@y.mx']
    assert body['sent_at']
    args = service['email_order'].await_args
    assert args.args[1].purchase_order_id == 1
    assert args.args[2:] == (['a@x.mx', 'B@y.mx'], 'Here it is')
    assert args.kwargs['mailer'] is mailer
    assert args.kwargs['current'].employee_id == 7


@pytest.mark.asyncio
async def test_email_without_mail_settings_is_503_and_sends_nothing() -> None:
    _auth()
    app.dependency_overrides[mail.get_mailer] = lambda: None
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/email', json={'to': ['a@x.mx']})

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert response.json() == {'detail': 'Email is not configured'}
    service['email_order'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'to': []},
        {'to': ['not-an-address']},
        {'to': ['a@x.mx'], 'message': 'x' * 1001},
        {'to': [f'u{n}@x.mx' for n in range(11)]},
        {},
    ],
    ids=['no recipients', 'bad address', 'long message', 'eleven recipients', 'no body fields'],
)
async def test_email_with_a_bad_body_is_422(body: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/1/email', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['email_order'].assert_not_awaited()


@pytest.mark.asyncio
async def test_email_accepts_a_message_of_1000_characters() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(
                f'{BASE}/1/email', json={'to': ['a@x.mx'], 'message': 'x' * 1000}
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    assert len(service['email_order'].await_args.args[3]) == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize('code', [409, 502])
async def test_email_service_refusals_pass_through(code: int) -> None:
    _auth()
    with _service() as service:
        service['email_order'].side_effect = HTTPException(status_code=code, detail='refused')
        async with _client() as client:
            response = await client.post(f'{BASE}/1/email', json={'to': ['a@x.mx']})

    assert response.status_code == code
    assert response.json() == {'detail': 'refused'}


# ── Writes ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_is_201_with_the_draft() -> None:
    _auth()
    tomorrow = local_today() + timedelta(days=1)
    with _service() as service:
        async with _client() as client:
            response = await client.post(
                BASE,
                json={
                    'supplier': 5,
                    'estimated_receipt_date': tomorrow.isoformat(),
                    'comment': 'c',
                },
            )

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['status'] == 'draft'
    assert response.json()['lines'][0]['product_code'] == 'W-1'
    args = service['create'].await_args
    assert args.args[1].supplier == 5
    assert args.args[1].estimated_receipt_date == tomorrow
    assert args.args[1].comment == 'c'
    assert args.kwargs['current'].employee_id == 7
    service['attach'].assert_awaited_once()


@pytest.mark.asyncio
async def test_create_needs_a_supplier() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            missing = await client.post(BASE, json={'comment': 'c'})
            null = await client.post(BASE, json={'supplier': None, 'comment': 'c'})

    assert missing.status_code == null.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['create'].assert_not_awaited()


@pytest.mark.asyncio
async def test_create_on_an_unknown_supplier_is_404() -> None:
    _auth()
    with _service() as service:
        service['create'].side_effect = HTTPException(status_code=404, detail='Supplier not found')
        async with _client() as client:
            response = await client.post(BASE, json={'supplier': 99, 'comment': None})

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_create_from_a_group_is_201_with_the_draft() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/to-purchase', json={'supplier': 5, 'facility': 2})

    assert response.status_code == status.HTTP_201_CREATED, response.text
    assert response.json()['purchase_order_id'] == 1
    args = service['create_from_group'].await_args
    assert args.args[1:] == (5, 2)
    assert args.kwargs['current'].employee_id == 7
    service['get_order'].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'body',
    [
        {'supplier': None, 'facility': 1},
        {'supplier': 5},
        {'facility': 1},
        {'supplier': 5, 'facility': None},
    ],
)
async def test_create_from_a_group_needs_a_supplier_and_a_facility(body: dict) -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(f'{BASE}/to-purchase', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['create_from_group'].assert_not_awaited()


@pytest.mark.asyncio
async def test_update_returns_the_order() -> None:
    _auth()
    with _service(_order(comment='new')) as service:
        async with _client() as client:
            response = await client.put(f'{BASE}/1', json={'supplier': 6, 'comment': 'new'})

    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()['comment'] == 'new'
    args = service['update'].await_args
    assert args.args[1] == 1
    assert (args.args[2].supplier, args.args[2].comment) == (6, 'new')
    assert args.kwargs['current'].employee_id == 7


@pytest.mark.asyncio
async def test_line_routes_return_the_whole_order() -> None:
    _auth()
    order = _order(lines=[_line(quantity=Decimal('5.0000'))])
    with _service(order) as service:
        async with _client() as client:
            added = await client.post(
                f'{BASE}/1/lines',
                json={'product': 3, 'quantity': '5', 'price': '9.5', 'tax_rate': '0.08'},
            )
            updated = await client.put(f'{BASE}/1/lines/1', json={'quantity': '5.5'})
            removed = await client.delete(f'{BASE}/1/lines/1')
            requested = await client.post(f'{BASE}/1/request-lines', json={'purchase_request': 2})

    for response in (added, updated, removed, requested):
        assert response.status_code == status.HTTP_200_OK, response.text
        assert response.json()['lines'][0]['quantity'] == '5.0000'
    line = service['add_line'].await_args.args[2]
    assert (line.product, line.quantity) == (3, Decimal('5'))
    assert (line.price, line.tax_rate, line.warehouse) == (Decimal('9.5'), Decimal('0.08'), None)
    assert service['add_line'].await_args.args[1] == 1
    assert service['update_line'].await_args.args[1:3] == (1, 1)
    assert service['update_line'].await_args.args[3].quantity == Decimal('5.5')
    assert service['remove_line'].await_args.args[1:3] == (1, 1)
    assert service['add_request_lines'].await_args.args[1:3] == (1, 2)


@pytest.mark.asyncio
async def test_confirm_and_cancel_return_the_order() -> None:
    _auth()
    with _service() as service:
        service['confirm'].return_value = _order(status='completed')
        service['cancel'].return_value = _order(status='cancelled')
        service['attach'].side_effect = lambda db, order: order
        async with _client() as client:
            confirmed = await client.post(f'{BASE}/1/confirm')
            cancelled = await client.post(f'{BASE}/1/cancel')

    assert (confirmed.status_code, confirmed.json()['status']) == (200, 'completed')
    assert (cancelled.status_code, cancelled.json()['status']) == (200, 'cancelled')
    assert service['confirm'].await_args.args[1] == 1
    assert service['cancel'].await_args.args[1] == 1


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    error = HTTPException(status_code=404, detail='Line not found')
    with _service() as service:
        service['update_line'].side_effect = error
        service['remove_line'].side_effect = error
        async with _client() as client:
            responses = [
                await client.put(f'{BASE}/1/lines/9', json={'quantity': '5'}),
                await client.delete(f'{BASE}/1/lines/9'),
            ]

    assert [r.status_code for r in responses] == [status.HTTP_404_NOT_FOUND] * 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('action', 'method', 'path', 'body', 'code'),
    [
        ('update', 'put', '/1', {'comment': 'x'}, 409),
        ('update', 'put', '/1', {'comment': 'x'}, 422),
        ('add_line', 'post', '/1/lines', {'product': 3, 'quantity': '1'}, 409),
        ('add_line', 'post', '/1/lines', {'product': 3, 'quantity': '1'}, 422),
        ('update_line', 'put', '/1/lines/1', {'quantity': '1'}, 409),
        ('remove_line', 'delete', '/1/lines/1', None, 409),
        ('add_request_lines', 'post', '/1/request-lines', {'purchase_request': 2}, 409),
        ('add_request_lines', 'post', '/1/request-lines', {'purchase_request': 2}, 422),
        ('confirm', 'post', '/1/confirm', None, 409),
        ('confirm', 'post', '/1/confirm', None, 422),
        ('cancel', 'post', '/1/cancel', None, 409),
        ('cancel', 'post', '/1/cancel', None, 422),
        ('create_from_group', 'post', '/to-purchase', {'supplier': 5, 'facility': 1}, 409),
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
    assert response.json() == {'detail': 'refused'}


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('path', 'body'),
    [
        ('/lines', {'product': 3, 'quantity': '0'}),
        ('/lines', {'product': 3, 'quantity': '-1'}),
        ('/lines', {'product': 3, 'quantity': '1.00001'}),
        ('/lines', {'quantity': '1'}),
        ('/lines', {'product': 3}),
        ('/lines', {'product': 3, 'quantity': '1', 'price': '-0.01'}),
        ('/lines', {'product': 3, 'quantity': '1', 'price': '1.00000001'}),
        ('/lines', {'product': 3, 'quantity': '1', 'tax_rate': '1'}),
        ('/lines', {'product': 3, 'quantity': '1', 'tax_rate': '-0.1'}),
        ('/lines/1', {'quantity': '0'}),
        ('/lines/1', {'quantity': '1.00001'}),
        ('/lines/1', {'price': '-1'}),
        ('/lines/1', {'price': '1.00000001'}),
        ('/lines/1', {'tax_rate': '1'}),
    ],
)
async def test_a_bad_line_quantity_price_or_tax_rate_is_422(path: str, body: dict) -> None:
    _auth()
    method = 'post' if path == '/lines' else 'put'
    with _service() as service:
        async with _client() as client:
            response = await getattr(client, method)(f'{BASE}/1{path}', json=body)

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['add_line'].assert_not_awaited()
    service['update_line'].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_line_with_four_decimals_of_quantity_and_seven_of_price_is_accepted() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.post(
                f'{BASE}/1/lines',
                json={'product': 3, 'quantity': '0.0001', 'price': '0', 'tax_rate': '0.9999'},
            )

    assert response.status_code == status.HTTP_200_OK, response.text
    line = service['add_line'].await_args.args[2]
    assert (line.quantity, line.price, line.tax_rate) == (
        Decimal('0.0001'),
        Decimal('0'),
        Decimal('0.9999'),
    )


@pytest.mark.asyncio
async def test_an_update_line_with_no_fields_is_accepted() -> None:
    _auth()
    with _service() as service:
        async with _client() as client:
            response = await client.put(f'{BASE}/1/lines/1', json={})

    assert response.status_code == status.HTTP_200_OK, response.text
    line = service['update_line'].await_args.args[3]
    assert (line.quantity, line.price, line.tax_rate, line.warehouse) == (None,) * 4


@pytest.mark.asyncio
async def test_an_estimated_receipt_date_in_the_past_is_422_on_create_and_update() -> None:
    _auth()
    yesterday = (local_today() - timedelta(days=1)).isoformat()
    with _service() as service:
        async with _client() as client:
            created = await client.post(
                BASE, json={'supplier': 5, 'estimated_receipt_date': yesterday, 'comment': None}
            )
            updated = await client.put(
                f'{BASE}/1', json={'estimated_receipt_date': yesterday, 'comment': None}
            )

    assert created.status_code == updated.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    service['create'].assert_not_awaited()
    service['update'].assert_not_awaited()


@pytest.mark.asyncio
async def test_an_estimated_receipt_date_of_today_is_accepted_on_create_and_update() -> None:
    _auth()
    today = local_today()
    with _service() as service:
        async with _client() as client:
            created = await client.post(
                BASE,
                json={'supplier': 5, 'estimated_receipt_date': today.isoformat(), 'comment': None},
            )
            updated = await client.put(
                f'{BASE}/1', json={'estimated_receipt_date': today.isoformat(), 'comment': None}
            )

    assert created.status_code == status.HTTP_201_CREATED, created.text
    assert updated.status_code == status.HTTP_200_OK, updated.text
    assert service['create'].await_args.args[1].estimated_receipt_date == today
    assert service['update'].await_args.args[2].estimated_receipt_date == today
