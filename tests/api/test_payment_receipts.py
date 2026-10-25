"""Tests for the payment receipt routes (#53, spec 028).

The HTTP contract only: `invoice_service` and the document reload are patched. What the service
does is covered in the unit and integration tests of `invoice_service`.
"""

from collections.abc import Generator
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from app.main import app
from app.schemas.fiscal import (
    PaidInvoiceAdd,
    PaidInvoiceResponse,
    PaidInvoiceUpdate,
    PaymentReceiptCreate,
)
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row

_SERVICE = 'app.services.fiscal_document_service'
_INVOICES = 'app.services.invoice_service'
_BASE = '/api/v1/fiscal-documents'

# key: (method, path, body, invoice_service function)
_ROUTES = {
    'create': (
        'POST',
        f'{_BASE}/payment-receipts',
        {'customer': 3, 'recipient': 'XAXX010101000'},
        'create_payment_receipt',
    ),
    'add': ('POST', f'{_BASE}/9/paid-invoices', {'invoice': 70}, 'add_paid_invoice'),
    'update': (
        'PUT',
        f'{_BASE}/9/paid-invoices/6',
        {'amount': '10.00'},
        'update_paid_invoice',
    ),
    'remove': ('DELETE', f'{_BASE}/9/paid-invoices/6', None, 'remove_paid_invoice'),
}
_EXISTING = [key for key in _ROUTES if key != 'create']


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _draft(**overrides) -> Any:
    return _row(status='draft', completed=False, stamp_uuid=None, **overrides)


def _paid_invoice() -> PaidInvoiceResponse:
    return PaidInvoiceResponse(
        fiscal_document_relation_id=6,
        invoice=70,
        batch='GR',
        serial=4776,
        stamp_uuid='BDCAD3DC-5428-5E2D-89AE-588872E59495',
        installment=2,
        previous_balance=Decimal('1160.50'),
        amount=Decimal('500.25'),
        remaining=Decimal('660.25'),
    )


async def _call(client: AsyncClient, key: str):
    method, path, body, _ = _ROUTES[key]
    return await client.request(method, path, json=body)


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_the_new_draft() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.create_payment_receipt', AsyncMock(return_value=9)) as create,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)) as load,
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/payment-receipts',
                json={'customer': 3, 'recipient': 'XAXX010101000', 'batch': 'GR'},
            )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['fiscal_document_id'] == 9
    assert body['status'] == 'draft'
    assert create.await_args.args[2] == PaymentReceiptCreate(
        customer=3, recipient='XAXX010101000', batch='GR'
    )
    assert load.await_args.args[1] == 9


@pytest.mark.asyncio
async def test_create_passes_the_customer_payment_to_the_service() -> None:
    _auth()
    document = _draft(
        payment_date=date(2026, 7, 1),
        payment_amount=Decimal('1000.00'),
        payment_reference='REF-1',
        customer_payment=44,
    )
    with (
        patch(f'{_INVOICES}.create_payment_receipt', AsyncMock(return_value=9)) as create,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/payment-receipts',
                json={'customer': 3, 'recipient': 'XAXX010101000', 'customer_payment': 44},
            )

    assert response.status_code == status.HTTP_201_CREATED
    assert create.await_args.args[2].customer_payment == 44
    body = response.json()
    assert body['customer_payment'] == 44
    assert body['payment_date'] == '2026-07-01'
    assert Decimal(str(body['payment_amount'])) == Decimal('1000.00')
    assert body['payment_reference'] == 'REF-1'


@pytest.mark.asyncio
async def test_create_serializes_the_paid_invoices() -> None:
    _auth()
    document = _draft(paid_invoices=[_paid_invoice()])
    with (
        patch(f'{_INVOICES}.create_payment_receipt', AsyncMock(return_value=9)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await _call(client, 'create')

    assert response.status_code == status.HTTP_201_CREATED
    paid = response.json()['paid_invoices']
    assert len(paid) == 1
    assert paid[0]['fiscal_document_relation_id'] == 6
    assert paid[0]['invoice'] == 70
    assert paid[0]['batch'] == 'GR'
    assert paid[0]['serial'] == 4776
    assert paid[0]['stamp_uuid'] == 'BDCAD3DC-5428-5E2D-89AE-588872E59495'
    assert paid[0]['installment'] == 2
    assert Decimal(str(paid[0]['previous_balance'])) == Decimal('1160.50')
    assert Decimal(str(paid[0]['amount'])) == Decimal('500.25')
    assert Decimal(str(paid[0]['remaining'])) == Decimal('660.25')


@pytest.mark.asyncio
async def test_create_without_a_recipient_is_422() -> None:
    _auth()
    with patch(f'{_INVOICES}.create_payment_receipt', AsyncMock()) as create:
        async with await _client() as client:
            response = await client.post(f'{_BASE}/payment-receipts', json={'customer': 3})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    create.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_for_a_paid_payment_is_409() -> None:
    _auth()
    refusal = HTTPException(status.HTTP_409_CONFLICT, 'This payment already has receipt P1')
    with patch(f'{_INVOICES}.create_payment_receipt', AsyncMock(side_effect=refusal)):
        async with await _client() as client:
            response = await _call(client, 'create')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': 'This payment already has receipt P1'}


# ── Add a paid invoice ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_paid_invoice_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.add_paid_invoice', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/9/paid-invoices', json={'invoice': 70, 'amount': '25.50'}
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = add.await_args.args
    assert args[2] is document
    assert args[3] == PaidInvoiceAdd(invoice=70, amount=Decimal('25.50'))


@pytest.mark.asyncio
async def test_add_paid_invoice_without_an_amount_leaves_it_to_the_service() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_paid_invoice', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'add')

    assert response.status_code == status.HTTP_200_OK
    assert add.await_args.args[3] == PaidInvoiceAdd(invoice=70)
    assert add.await_args.args[3].amount is None


@pytest.mark.asyncio
async def test_add_paid_invoice_serializes_the_paid_invoices() -> None:
    _auth()
    document = _draft(paid_invoices=[_paid_invoice()])
    with (
        patch(f'{_INVOICES}.add_paid_invoice', AsyncMock()),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await _call(client, 'add')

    paid = response.json()['paid_invoices']
    assert len(paid) == 1
    assert paid[0]['fiscal_document_relation_id'] == 6
    assert paid[0]['invoice'] == 70
    assert paid[0]['batch'] == 'GR'
    assert paid[0]['serial'] == 4776
    assert paid[0]['stamp_uuid'] == 'BDCAD3DC-5428-5E2D-89AE-588872E59495'
    assert paid[0]['installment'] == 2
    assert Decimal(str(paid[0]['previous_balance'])) == Decimal('1160.50')
    assert Decimal(str(paid[0]['amount'])) == Decimal('500.25')
    assert Decimal(str(paid[0]['remaining'])) == Decimal('660.25')


@pytest.mark.asyncio
@pytest.mark.parametrize('amount', ['0', '1.001'], ids=['zero', 'three-decimals'])
async def test_add_paid_invoice_with_an_invalid_amount_is_422(amount: str) -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_paid_invoice', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/9/paid-invoices', json={'invoice': 70, 'amount': amount}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_paid_invoice_over_the_balance_is_422() -> None:
    _auth()
    refusal = HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, 'Amount exceeds the invoice balance of 0.00'
    )
    with (
        patch(f'{_INVOICES}.add_paid_invoice', AsyncMock(side_effect=refusal)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'add')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {'detail': 'Amount exceeds the invoice balance of 0.00'}


# ── Update a paid invoice ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_paid_invoice_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.update_paid_invoice', AsyncMock()) as update,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.put(f'{_BASE}/9/paid-invoices/6', json={'amount': '10.50'})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = update.await_args.args
    assert args[2] is document
    assert args[3] == 6
    assert args[4] == PaidInvoiceUpdate(amount=Decimal('10.50'))


@pytest.mark.asyncio
async def test_update_paid_invoice_without_the_amount_is_422() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.update_paid_invoice', AsyncMock()) as update,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.put(f'{_BASE}/9/paid-invoices/6', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_paid_invoice_over_the_payment_is_422() -> None:
    _auth()
    refusal = HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, 'The amounts paid exceed the payment amount'
    )
    with (
        patch(f'{_INVOICES}.update_paid_invoice', AsyncMock(side_effect=refusal)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'update')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json() == {'detail': 'The amounts paid exceed the payment amount'}


# ── Remove a paid invoice ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_paid_invoice_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.remove_paid_invoice', AsyncMock()) as remove,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.delete(f'{_BASE}/9/paid-invoices/6')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = remove.await_args.args
    assert args[2] is document
    assert args[3] == 6


@pytest.mark.asyncio
async def test_remove_an_unknown_paid_invoice_is_404() -> None:
    _auth()
    refusal = HTTPException(status.HTTP_404_NOT_FOUND, 'Paid invoice not found')
    with (
        patch(f'{_INVOICES}.remove_paid_invoice', AsyncMock(side_effect=refusal)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'remove')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Paid invoice not found'}


# ── Every route ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_every_route_requires_authentication(key: str) -> None:
    async with await _client() as client:
        response = await _call(client, key)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_every_route_requires_its_privilege(key: str) -> None:
    """Create needs CREATE, the rest UPDATE; the denied user has neither."""
    _auth(administrator=False)
    _deny_privileges()
    service = _ROUTES[key][3]
    with patch(f'{_INVOICES}.{service}', AsyncMock()) as called:
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    called.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', _EXISTING)
async def test_a_missing_document_is_404(key: str) -> None:
    _auth()
    service = _ROUTES[key][3]
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)),
        patch(f'{_INVOICES}.{service}', AsyncMock()) as called,
    ):
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Fiscal document not found'}
    called.assert_not_awaited()
