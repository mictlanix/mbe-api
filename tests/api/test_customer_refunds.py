"""Tests for the /customer-refunds endpoints.

The distinguishable 409s in `test_opening_against_*` are the point of the lifecycle
clarification — a clerk has to learn whether to confirm the order or cancel it.
"""

from collections.abc import Generator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, employee_id: int | None = 7) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _refund(**overrides) -> SimpleNamespace:
    base = dict(
        customer_refund_id=1,
        sales_order=5,
        customer=2,
        sales_person=7,
        facility=1,
        serial=None,
        date=None,
        currency=0,
        exchange_rate=Decimal('1'),
        status='draft',
        lines=[],
        subtotal=Decimal('0.00'),
        tax_total=Decimal('0.00'),
        total=Decimal('0.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── Authentication ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/customer-refunds')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_confirm_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.post(
            '/api/v1/customer-refunds/1/confirm', json={'payout': 'cash'}
        )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


# ── Opening ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_open_against_a_paid_order_prepopulates_lines() -> None:
    _auth()
    line = SimpleNamespace(
        customer_refund_detail_id=1, sales_order_detail=11, product=3, product_code='W-1',
        product_name='Widget', quantity=Decimal('0'), price=Decimal('100'),
        discount=Decimal('0'), tax_rate=Decimal('0.16'), tax_included=False, currency=0,
        warehouse=2, refundable_quantity=Decimal('10'), subtotal=Decimal('0.00'),
        tax_total=Decimal('0.00'), total=Decimal('0.00'),
    )
    with patch(
        'app.services.customer_refund_service.open_refund',
        AsyncMock(return_value=_refund(lines=[line])),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-refunds', json={'sales_order': 5}
            )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['lines'][0]['quantity'] == '0'
    assert body['lines'][0]['refundable_quantity'] == '10'


@pytest.mark.asyncio
async def test_opening_against_an_unpaid_order_is_409_saying_not_paid() -> None:
    """FR-060 — and the message points at cancelling, which is the right route."""
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            'Order is not paid; an unpaid or partly-paid order is unwound by cancelling it, '
            'not by refunding it'
        ),
    )
    with patch(
        'app.services.customer_refund_service.open_refund', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds', json={'sales_order': 5})

    assert response.status_code == status.HTTP_409_CONFLICT
    detail = response.json()['detail'].lower()
    assert 'not paid' in detail
    assert 'cancelling' in detail


@pytest.mark.asyncio
async def test_opening_against_a_draft_order_is_409_saying_not_completed() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='Order is not completed; only a completed, paid order can be refunded',
    )
    with patch(
        'app.services.customer_refund_service.open_refund', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds', json={'sales_order': 5})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'not completed' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_opening_with_nothing_refundable_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='No refundable items remain on this order',
    )
    with patch(
        'app.services.customer_refund_service.open_refund', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds', json={'sales_order': 5})

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'refundable' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_opening_against_an_unknown_order_is_404() -> None:
    _auth()
    error = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')
    with patch(
        'app.services.customer_refund_service.open_refund', AsyncMock(side_effect=error)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds', json={'sales_order': 999})

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Line quantities ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_quantity_above_refundable_is_422() -> None:
    _auth()
    error = HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='Return quantity 12 exceeds the refundable quantity 10',
    )
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.get_line',
        AsyncMock(return_value=SimpleNamespace()),
    ), patch(
        'app.services.customer_refund_service.update_line', AsyncMock(side_effect=error)
    ):
        async with await _client() as client:
            response = await client.put(
                '/api/v1/customer-refunds/1/lines/1', json={'quantity': '12'}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_negative_quantity_is_rejected_by_the_schema() -> None:
    _auth()
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.get_line',
        AsyncMock(return_value=SimpleNamespace()),
    ):
        async with await _client() as client:
            response = await client.put(
                '/api/v1/customer-refunds/1/lines/1', json={'quantity': '-1'}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_unknown_line_is_404() -> None:
    _auth()
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch('app.services.customer_refund_service.get_line', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.put(
                '/api/v1/customer-refunds/1/lines/999', json={'quantity': '1'}
            )

    assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Confirmation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_with_credit_note_payout() -> None:
    _auth()
    confirmed = _refund(serial=31, date=datetime(2026, 7, 25), status='completed',
                        total=Decimal('116.00'))
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.confirm_refund',
        AsyncMock(return_value=confirmed),
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-refunds/1/confirm', json={'payout': 'credit_note'}
            )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['serial'] == 31
    assert body['status'] == 'completed'


@pytest.mark.asyncio
async def test_confirm_passes_the_chosen_payout_through() -> None:
    """FR-065 — the cashier chooses cash or store credit at confirmation."""
    _auth()
    confirm = AsyncMock(return_value=_refund(status='completed'))
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch('app.services.customer_refund_service.confirm_refund', confirm):
        async with await _client() as client:
            await client.post('/api/v1/customer-refunds/1/confirm', json={'payout': 'cash'})

    assert confirm.await_args.kwargs['payout'].value == 'cash'


@pytest.mark.asyncio
async def test_confirm_without_an_open_cash_session_is_409() -> None:
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail='An open cash session is required to confirm a refund',
    )
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.confirm_refund', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-refunds/1/confirm', json={'payout': 'cash'}
            )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert 'cash session' in response.json()['detail'].lower()


@pytest.mark.asyncio
async def test_an_invalid_payout_is_rejected() -> None:
    _auth()
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ):
        async with await _client() as client:
            response = await client.post(
                '/api/v1/customer-refunds/1/confirm', json={'payout': 'bitcoin'}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_payout_is_required() -> None:
    _auth()
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds/1/confirm', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


# ── Cancellation ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_a_draft_refund() -> None:
    _auth()
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.cancel_refund',
        AsyncMock(return_value=_refund(status='cancelled')),
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds/1/cancel')

    assert response.json()['status'] == 'cancelled'


@pytest.mark.asyncio
async def test_cancelling_a_completed_refund_is_409() -> None:
    """FR-066 — once the stock is back and the money returned, the refund is final."""
    _auth()
    conflict = HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail='A completed refund cannot be cancelled'
    )
    with patch(
        'app.services.customer_refund_service.get_refund', AsyncMock(return_value=_refund())
    ), patch(
        'app.services.customer_refund_service.cancel_refund', AsyncMock(side_effect=conflict)
    ):
        async with await _client() as client:
            response = await client.post('/api/v1/customer-refunds/1/cancel')

    assert response.status_code == status.HTTP_409_CONFLICT


@pytest.mark.asyncio
async def test_unknown_refund_is_404() -> None:
    _auth()
    with patch('app.services.customer_refund_service.get_refund', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/customer-refunds/999')

    assert response.status_code == status.HTTP_404_NOT_FOUND
