"""Tests for the credit note routes (#55, spec 028).

The HTTP contract only: `invoice_service` and the document reload are patched. What the service
does is covered in the unit and integration tests of `invoice_service`.
"""

from collections.abc import Generator
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from app.main import app
from app.schemas.fiscal import CreditNoteCreate, InvoiceLineCopy
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row

_SERVICE = 'app.services.fiscal_document_service'
_INVOICES = 'app.services.invoice_service'
_BASE = '/api/v1/fiscal-documents'

# key: (method, path, body, invoice_service function)
_ROUTES = {
    'create': ('POST', f'{_BASE}/credit-notes', {'invoice': 70}, 'create_credit_note'),
    'line': ('POST', f'{_BASE}/9/invoice-lines', {'line': 5}, 'add_invoice_line'),
}


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _draft(**overrides) -> Any:
    return _row(status='draft', completed=False, stamp_uuid=None, **overrides)


async def _call(client: AsyncClient, key: str):
    method, path, body, _ = _ROUTES[key]
    return await client.request(method, path, json=body)


# ── Create ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_the_new_draft() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.create_credit_note', AsyncMock(return_value=9)) as create,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)) as load,
    ):
        async with await _client() as client:
            response = await _call(client, 'create')

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['fiscal_document_id'] == 9
    assert body['status'] == 'draft'
    assert create.await_args.args[2] == CreditNoteCreate(invoice=70)
    assert load.await_args.args[1] == 9


@pytest.mark.asyncio
async def test_create_passes_through_the_services_refusal() -> None:
    _auth()
    refusal = HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, 'The invoice cannot be credited')
    with patch(f'{_INVOICES}.create_credit_note', AsyncMock(side_effect=refusal)):
        async with await _client() as client:
            response = await _call(client, 'create')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()['detail'] == 'The invoice cannot be credited'


@pytest.mark.asyncio
async def test_create_requires_the_invoice() -> None:
    _auth()
    with patch(f'{_INVOICES}.create_credit_note', AsyncMock()) as create:
        async with await _client() as client:
            response = await client.post(f'{_BASE}/credit-notes', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    create.assert_not_awaited()


# ── Invoice lines ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_a_line_returns_the_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.add_invoice_line', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/9/invoice-lines', json={'line': 5, 'quantity': '2.5'}
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    args = add.await_args.args
    assert args[2] is document
    assert args[3] == InvoiceLineCopy(line=5, quantity=Decimal('2.5'))


@pytest.mark.asyncio
async def test_copy_a_line_without_quantity_leaves_it_to_the_service() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_invoice_line', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'line')

    assert response.status_code == status.HTTP_200_OK
    assert add.await_args.args[3] == InvoiceLineCopy(line=5)
    assert add.await_args.args[3].quantity is None


@pytest.mark.asyncio
async def test_copy_a_line_passes_through_the_services_refusal() -> None:
    _auth()
    refusal = HTTPException(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "Quantity exceeds the invoice line's 35.0000"
    )
    with (
        patch(f'{_INVOICES}.add_invoice_line', AsyncMock(side_effect=refusal)),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, 'line')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()['detail'] == "Quantity exceeds the invoice line's 35.0000"


@pytest.mark.asyncio
async def test_copy_a_line_with_a_zero_quantity_is_422() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_invoice_line', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.post(
                f'{_BASE}/9/invoice-lines', json={'line': 5, 'quantity': 0}
            )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    add.assert_not_awaited()


@pytest.mark.asyncio
async def test_copy_a_line_of_a_missing_document_is_404() -> None:
    _auth()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)),
        patch(f'{_INVOICES}.add_invoice_line', AsyncMock()) as add,
    ):
        async with await _client() as client:
            response = await _call(client, 'line')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Fiscal document not found'
    add.assert_not_awaited()


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
    """Create needs CREATE, the line copy UPDATE; the denied user has neither."""
    _auth(administrator=False)
    _deny_privileges()
    service = _ROUTES[key][3]
    with patch(f'{_INVOICES}.{service}', AsyncMock()) as called:
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    called.assert_not_awaited()
