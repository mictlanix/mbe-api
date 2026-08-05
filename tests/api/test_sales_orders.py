"""Tests for the /sales-orders endpoints.

Follows the established pattern: dependency_overrides swap out auth and the database, and the
service is patched, so these assert the HTTP contract rather than re-testing the service logic
covered in tests/unit/test_sales_order_service.py.
"""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int = 7, point_sale_id: int | None = 3) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
        point_sale_id=point_sale_id,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _order(**overrides) -> SimpleNamespace:
    base = dict(
        sales_order_id=1,
        facility=1,
        serial=None,
        point_sale=3,
        salesperson=7,
        customer=2,
        customer_name=None,
        sales_quote=None,
        payment_terms=0,
        date='2026-07-25T00:00:00',
        promise_date='2026-08-01T00:00:00',
        due_date='2026-07-25T00:00:00',
        contact=None,
        ship_to=None,
        recipient=None,
        recipient_name=None,
        currency=0,
        exchange_rate=Decimal('1'),
        priority=1,
        comment=None,
        status='draft',
        lines=[],
        subtotal=Decimal('0.00'),
        tax_total=Decimal('0.00'),
        total=Decimal('0.00'),
        balance=Decimal('0.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication and authorization ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_create_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post('/api/v1/sales-orders', json={})

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_confirm_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post('/api/v1/sales-orders/1/confirm')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Happy paths ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_a_draft() -> None:
    _auth()
    with patch(
        'app.services.sales_order_service.create_order', AsyncMock(return_value=_order())
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders', json={})

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['status'] == 'draft'
    assert body['serial'] is None


@pytest.mark.asyncio
async def test_create_accepts_an_empty_body_and_uses_defaults() -> None:
    """FR-010 — an empty body opens a draft on configured defaults."""
    _auth()
    create = AsyncMock(return_value=_order())
    with patch('app.services.sales_order_service.create_order', create):
        async with await _client() as client:
            await client.post('/api/v1/sales-orders', json={})

    passed = create.await_args.args[1]
    assert passed.customer is None
    assert passed.currency is None


@pytest.mark.asyncio
async def test_get_returns_the_order_with_derived_money() -> None:
    _auth()
    order = _order(total=Decimal('232.00'), balance=Decimal('232.00'))
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=order)), patch(
        'app.services.sales_order_service.attach_derived', AsyncMock(return_value=order)
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/1')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['total'] == '232.00'
    assert response.json()['balance'] == '232.00'


@pytest.mark.asyncio
async def test_list_returns_paginated_envelope() -> None:
    _auth()
    with patch(
        'app.services.sales_order_service.list_orders',
        AsyncMock(return_value=([_order()], 1)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders')

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['total'] == 1
    assert len(body['items']) == 1


@pytest.mark.asyncio
async def test_list_passes_explicit_filters_through() -> None:
    """FR-009 — narrowing is explicit; there is no wildcard and no implicit default."""
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.sales_order_service.list_orders', listing):
        async with await _client() as client:
            await client.get('/api/v1/sales-orders?mine=true&customer=5&status=completed')

    kwargs = listing.await_args.kwargs
    assert kwargs['mine'] is True
    assert kwargs['customer'] == 5
    assert kwargs['order_status'] == 'completed'


@pytest.mark.asyncio
async def test_confirm_returns_the_completed_order() -> None:
    _auth()
    confirmed = _order(serial=42, status='completed')
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch(
        'app.services.sales_order_service.confirm_order', AsyncMock(return_value=confirmed)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders/1/confirm')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['serial'] == 42
    assert response.json()['status'] == 'completed'


# ── Not found ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_unknown_order_is_404() -> None:
    _auth()
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_confirm_unknown_order_is_404() -> None:
    _auth()
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders/999/confirm')

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch('app.services.sales_order_service.get_line', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.put(
                '/api/v1/sales-orders/1/lines/999', json={'quantity': '2'}
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Lifecycle conflicts ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_editing_a_completed_order_is_409() -> None:
    _auth()
    conflict = HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Document is completed')
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch('app.services.sales_order_service.update_order', AsyncMock(side_effect=conflict)):
        async with await _client() as client:
            response = await client.put('/api/v1/sales-orders/1', json={'comment': 'nope'})

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_cancelling_a_paid_order_is_409_and_points_at_refund() -> None:
    """SC-010 — the refusal has to tell the caller which route to take instead."""
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='A paid order cannot be cancelled — refund it instead',
    )
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch('app.services.sales_order_service.cancel_order', AsyncMock(side_effect=conflict)):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders/1/cancel')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'refund' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_confirming_with_zero_priced_lines_names_them() -> None:
    """FR-017 — the caller has to learn which lines blocked it, not just that something did."""
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={'message': 'Order has lines priced at zero', 'lines': ['Widget (line 5)']},
    )
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch('app.services.sales_order_service.confirm_order', AsyncMock(side_effect=conflict)):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders/1/confirm')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail']['lines'] == ['Widget (line 5)']


# ── Missing user context ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_without_a_point_of_sale_is_422_distinguishably() -> None:
    """FR-004a — `sales_order.point_sale` is NOT NULL but the user's setting is optional."""
    _auth(point_sale_id=None)
    error = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='No point of sale is configured for your user; set one or supply it explicitly',
    )
    with patch('app.services.sales_order_service.create_order', AsyncMock(side_effect=error)):
        async with await _client() as client:
            response = await client.post('/api/v1/sales-orders', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = response.json()['detail'].lower()
    assert 'point of sale' in detail
    assert 'employee' not in detail


# ── Validation ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_negative_discount_is_rejected_by_the_schema() -> None:
    _auth()
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-orders/1/lines',
                json={'product': 1, 'discount_rate': '-0.5'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_discount_above_one_is_rejected_by_the_schema() -> None:
    _auth()
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-orders/1/lines',
                json={'product': 1, 'discount_rate': '1.5'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_a_line_tax_rate_reaches_the_service() -> None:
    """#135 — the product's rate is the default, and a caller may override it per line."""
    _auth()
    adding = AsyncMock(return_value=_order())
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch('app.services.sales_order_service.add_line', adding):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-orders/1/lines',
                json={'product': 1, 'tax_rate': '0.08'},
            )

    assert response.status_code == status.HTTP_200_OK
    assert adding.await_args.args[2].tax_rate == Decimal('0.08')


@pytest.mark.asyncio
async def test_a_tax_rate_above_one_is_rejected_by_the_schema() -> None:
    """A rate, not a percentage — 16 would mean 1600%."""
    _auth()
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/sales-orders/1/lines',
                json={'product': 1, 'tax_rate': '16'},
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Product lookup ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_product_lookup_returns_price_and_stock() -> None:
    _auth()
    row = {
        'product': 1,
        'code': 'W-1',
        'name': 'Widget',
        'sku': None,
        'brand': None,
        'model': None,
        'bar_code': '1234567890123',
        'price': Decimal('99.00'),
        'tax_rate': Decimal('0.16'),
        'tax_included': False,
        'min_order_qty': 1,
        'stock_required': True,
        'stockable': True,
        'stock': [{'warehouse': 2, 'on_hand': Decimal('5'), 'available': Decimal('2')}],
    }
    with patch(
        'app.services.sales_order_service.lookup_products', AsyncMock(return_value=[row])
    ):
        async with await _client() as client:
            response = await client.get(
                '/api/v1/sales-orders/product-lookup?pattern=widget&customer=2'
            )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body[0]['price'] == '99.00'
    assert body[0]['stock'][0]['on_hand'] == '5'
    # Availability is the figure confirmation checks, so it is the one that predicts a sale:
    # five on the shelf, three already reserved by other orders, two actually sellable.
    assert body[0]['stock'][0]['available'] == '2'


@pytest.mark.asyncio
async def test_product_lookup_requires_a_pattern() -> None:
    _auth()
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders/product-lookup?customer=2')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Point-of-sale scoping and applied payments ────────────────────────────────


@pytest.mark.asyncio
async def test_list_passes_the_point_sale_filter_through() -> None:
    """#136 — a register's own open sales, not every sale this employee touched here."""
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.sales_order_service.list_orders', listing):
        async with await _client() as client:
            await client.get('/api/v1/sales-orders?point_sale=3')

    assert listing.await_args.kwargs['point_sale'] == 3


@pytest.mark.asyncio
async def test_list_leaves_point_sale_unset_when_not_asked_for() -> None:
    _auth()
    listing = AsyncMock(return_value=([], 0))
    with patch('app.services.sales_order_service.list_orders', listing):
        async with await _client() as client:
            await client.get('/api/v1/sales-orders')

    assert listing.await_args.kwargs['point_sale'] is None


@pytest.mark.asyncio
async def test_order_payments_flatten_each_payment_onto_its_application() -> None:
    """#134 — one request renders the applied-payments panel, cancelled rows included."""
    _auth()
    rows = [
        {
            'sales_order_payment_id': 9,
            'sales_order': 1,
            'customer_payment': 4,
            'amount': Decimal('100.00'),
            'amount_change': Decimal('0'),
            'applier': 7,
            'date': '2026-07-26T00:00:00',
            'cancelled': False,
            'method': 4,
            'currency': 0,
            'reference': 'AUTH-771',
            'payment_date': '2026-07-25T00:00:00',
            'payment_type': 1,
            'verifier': None,
        },
        {
            'sales_order_payment_id': 10,
            'sales_order': 1,
            'customer_payment': 5,
            'amount': Decimal('32.00'),
            'amount_change': Decimal('0'),
            'applier': 7,
            'date': '2026-07-26T00:00:00',
            'cancelled': True,
            'method': 1,
            'currency': 0,
            'reference': None,
            'payment_date': '2026-07-26T00:00:00',
            'payment_type': 1,
            'verifier': 8,
        },
    ]
    with patch(
        'app.services.sales_order_service.get_order', AsyncMock(return_value=_order())
    ), patch(
        'app.services.customer_payment_service.list_order_applications',
        AsyncMock(return_value=rows),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/1/payments')

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert [a['cancelled'] for a in body] == [False, True]
    assert body[0]['reference'] == 'AUTH-771'
    # The application's date and its payment's are distinct fields, because they differ.
    assert body[0]['date'] == '2026-07-26T00:00:00'
    assert body[0]['payment_date'] == '2026-07-25T00:00:00'


@pytest.mark.asyncio
async def test_order_payments_404_when_the_order_does_not_exist() -> None:
    _auth()
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/999/payments')

    assert response.status_code == status.HTTP_404_NOT_FOUND
