"""Tests for the invoice editor routes (#47, spec 027).

The HTTP contract only: `invoice_service` and the document reload are patched. What the service
does is covered in tests/unit/test_invoice_service.py and tests/integration/test_invoice_flow.py.
"""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status
from httpx import AsyncClient

from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.main import app
from app.schemas.fiscal import InvoiceCreate, InvoiceLineUpdate
from tests.api.test_fiscal_documents import _auth, _client, _deny_privileges, _row

_SERVICE = 'app.services.fiscal_document_service'
_INVOICES = 'app.services.invoice_service'
_BASE = '/api/v1/fiscal-documents'

# key: (method, path, body, invoice_service function)
_ROUTES = {
    'create': ('POST', _BASE, {'customer': 3, 'recipient': 'XAXX010101000'}, 'create_invoice'),
    'sales-orders': ('POST', f'{_BASE}/9/sales-orders', {'sales_order': 5}, 'add_sales_order'),
    'update-line': ('PUT', f'{_BASE}/9/lines/4', {'quantity': '2'}, 'update_line'),
    'remove-line': ('DELETE', f'{_BASE}/9/lines/4', None, 'remove_line'),
    'confirm': ('POST', f'{_BASE}/9/confirm', None, 'confirm'),
    'add-line': ('POST', f'{_BASE}/9/lines', {'product': 5}, 'add_manual_line'),
}
_EXISTING = [key for key in _ROUTES if key != 'create']
# Add-line is gated by Create on standalone documents (56), not by the rights of the others.
_PRIVILEGED = [key for key in _ROUTES if key != 'add-line']


def _grant_update_on_fiscal_documents_only() -> None:
    """The user holds Update on 23 and nothing on 56: the query's `system_object` is answered."""

    async def _execute(statement):
        params = statement.compile().params
        row = SimpleNamespace(privileges=int(AccessRight.UPDATE))
        result = MagicMock()
        granted = int(SystemObject.FISCAL_DOCUMENTS) in params.values()
        result.scalar_one_or_none.return_value = row if granted else None
        return result

    async def _db():
        yield SimpleNamespace(execute=_execute)

    app.dependency_overrides[get_db] = _db


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
        patch(f'{_INVOICES}.create_invoice', AsyncMock(return_value=9)) as create,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)) as load,
    ):
        async with await _client() as client:
            response = await client.post(
                _BASE, json={'customer': 3, 'recipient': 'XAXX010101000', 'batch': 'GR'}
            )

    assert response.status_code == status.HTTP_201_CREATED
    body = response.json()
    assert body['fiscal_document_id'] == 9
    assert body['status'] == 'draft'
    data = create.await_args.args[2]
    assert data == InvoiceCreate(customer=3, recipient='XAXX010101000', batch='GR')
    assert load.await_args.args[1] == 9


@pytest.mark.asyncio
async def test_create_without_a_recipient_is_422() -> None:
    _auth()
    with patch(f'{_INVOICES}.create_invoice', AsyncMock()) as create:
        async with await _client() as client:
            response = await client.post(_BASE, json={'customer': 3})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    create.assert_not_awaited()


# ── Add a sales order ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_sales_order_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.add_sales_order', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/sales-orders', json={'sales_order': 5})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = add.await_args.args
    assert args[2] is document
    assert args[3] == 5


@pytest.mark.asyncio
async def test_add_sales_order_without_the_order_is_422() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_sales_order', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/sales-orders', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    add.assert_not_awaited()


# ── Add a line ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_line_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.add_manual_line', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/lines', json={'product': 5})

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = add.await_args.args
    assert args[2] is document
    assert args[3] == 5


@pytest.mark.asyncio
async def test_add_line_without_the_product_is_422() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.add_manual_line', AsyncMock()) as add,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/lines', json={})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    add.assert_not_awaited()


# ── Update a line ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_line_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.update_line', AsyncMock()) as update,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.put(
                f'{_BASE}/9/lines/4', json={'quantity': '2', 'tax_rate': '0.16'}
            )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = update.await_args.args
    assert args[2] is document
    assert args[3] == 4
    assert args[4] == InvoiceLineUpdate(quantity=Decimal('2'), tax_rate=Decimal('0.16'))
    assert args[4].model_fields_set == {'quantity', 'tax_rate'}


@pytest.mark.asyncio
async def test_update_line_with_an_unknown_tax_rate_is_422() -> None:
    _auth()
    with (
        patch(f'{_INVOICES}.update_line', AsyncMock()) as update,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await client.put(f'{_BASE}/9/lines/4', json={'tax_rate': '0.08'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    update.assert_not_awaited()


# ── Remove a line ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_line_returns_the_reloaded_document() -> None:
    _auth()
    document = _draft()
    with (
        patch(f'{_INVOICES}.remove_line', AsyncMock()) as remove,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.delete(f'{_BASE}/9/lines/4')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'draft'
    args = remove.await_args.args
    assert args[2] is document
    assert args[3] == 4


# ── Confirm ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_confirm_returns_the_sealed_document() -> None:
    _auth()
    document = _row(status='sealed', completed=False)
    with (
        patch(f'{_INVOICES}.confirm', AsyncMock()) as confirm,
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
    ):
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/confirm')

    assert response.status_code == status.HTTP_200_OK
    assert response.json()['fiscal_document_id'] == 9
    assert response.json()['status'] == 'sealed'
    assert confirm.await_args.args[2] is document


# ── Every route ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_every_route_requires_authentication(key: str) -> None:
    async with await _client() as client:
        response = await _call(client, key)

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
@pytest.mark.parametrize('key', _PRIVILEGED)
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
async def test_add_line_needs_create_on_standalone_documents() -> None:
    _auth(administrator=False)
    _grant_update_on_fiscal_documents_only()
    with patch(f'{_INVOICES}.add_manual_line', AsyncMock()) as add:
        async with await _client() as client:
            response = await client.post(f'{_BASE}/9/lines', json={'product': 5})

    assert response.status_code == status.HTTP_403_FORBIDDEN
    add.assert_not_awaited()


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('code', 'detail'),
    [
        (status.HTTP_409_CONFLICT, 'Fiscal document is not a draft'),
        (status.HTTP_422_UNPROCESSABLE_CONTENT, 'No invoiceable lines were found'),
    ],
    ids=['conflict', 'unprocessable'],
)
@pytest.mark.parametrize('key', list(_ROUTES))
async def test_a_service_refusal_is_returned_as_is(key: str, code: int, detail: str) -> None:
    _auth()
    service = _ROUTES[key][3]
    with (
        patch(f'{_INVOICES}.{service}', AsyncMock(side_effect=HTTPException(code, detail))),
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_draft())),
    ):
        async with await _client() as client:
            response = await _call(client, key)

    assert response.status_code == code
    assert response.json() == {'detail': detail}
