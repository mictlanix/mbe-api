"""Tests for the print endpoints (#230).

The HTTP contract only: the order, its context and the renderer are patched, so these assert status
codes, headers and the published schema. What goes into a ticket is covered in
tests/unit/test_print_contexts.py, and real renders in tests/integration/test_print_render.py.
"""

from collections.abc import Generator
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app

_PDF = b'%PDF-1.7 fake'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True) -> None:
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )

    async def _noop_db():
        yield None

    app.dependency_overrides[get_db] = _noop_db


def _deny_privileges() -> None:
    """`require_privilege` reads `scalar_one_or_none()`; answering None denies every right."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Sale ticket ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sale_ticket_returns_the_pdf_inline() -> None:
    _auth()
    order = SimpleNamespace(sales_order_id=7)
    context = ('sale_ticket.html', {'header': {}})
    with (
        patch('app.services.sales_order_service.get_order', AsyncMock(return_value=order)),
        patch('app.services.sales_order_service.attach_derived', AsyncMock(return_value=order)),
        patch(
            'app.services.print_contexts.sale_ticket_context', AsyncMock(return_value=context)
        ) as build,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/7/ticket')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="ticket-00000007.pdf"'
    assert response.content == _PDF
    assert build.await_args.args[1] is order
    render.assert_awaited_once_with(*context)


@pytest.mark.asyncio
async def test_sale_ticket_of_a_missing_order_is_404() -> None:
    _auth()
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/7/ticket')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Sales order not found'}


@pytest.mark.asyncio
async def test_sale_ticket_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders/7/ticket')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_sale_ticket_requires_the_sales_orders_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders/7/ticket')

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Sales order document ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sales_order_document_returns_the_pdf_inline() -> None:
    _auth()
    order = SimpleNamespace(sales_order_id=7)
    context = ('sales_order.html', {'header': {}})
    with (
        patch('app.services.sales_order_service.get_order', AsyncMock(return_value=order)),
        patch(
            'app.services.sales_order_service.attach_derived', AsyncMock(return_value=order)
        ) as attach,
        patch(
            'app.services.print_contexts.sales_order_context', AsyncMock(return_value=context)
        ) as build,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/7/document')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="pedido-00000007.pdf"'
    assert response.content == _PDF
    attach.assert_awaited_once()
    assert build.await_args.args[1] is order
    render.assert_awaited_once_with(*context)


@pytest.mark.asyncio
async def test_sales_order_document_of_a_missing_order_is_404() -> None:
    _auth()
    with patch('app.services.sales_order_service.get_order', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/sales-orders/7/document')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Sales order not found'}


@pytest.mark.asyncio
async def test_sales_order_document_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders/7/document')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_sales_order_document_requires_the_sales_orders_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        response = await client.get('/api/v1/sales-orders/7/document')

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── Cash cut ──────────────────────────────────────────────────────────────────


def _cut_patches(session: SimpleNamespace | None):  # noqa: ANN202
    return (
        patch('app.services.cash_session_service.get_session', AsyncMock(return_value=session)),
        patch('app.services.cash_session_service.attach_derived', AsyncMock(return_value=session)),
        patch(
            'app.services.print_contexts.cash_cut_context',
            AsyncMock(return_value=('cash_cut.html', {'header': {}})),
        ),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)),
    )


@pytest.mark.asyncio
async def test_cash_cut_returns_the_pdf_inline() -> None:
    _auth()
    session = SimpleNamespace(cash_session_id=5, end=datetime(2026, 9, 24, 21))
    get, derive, build, render = _cut_patches(session)
    with get, derive as attach, build, render as rendered:
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/5/ticket')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="corte-000005.pdf"'
    assert response.content == _PDF
    attach.assert_awaited_once()
    rendered.assert_awaited_once_with('cash_cut.html', {'header': {}})


@pytest.mark.asyncio
async def test_cash_cut_of_a_missing_session_is_404() -> None:
    _auth()
    get, derive, build, render = _cut_patches(None)
    with get, derive, build, render as rendered:
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/5/ticket')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Cash session not found'}
    rendered.assert_not_awaited()


@pytest.mark.asyncio
async def test_cash_cut_of_an_open_session_is_409() -> None:
    _auth()
    get, derive, build, render = _cut_patches(SimpleNamespace(cash_session_id=5, end=None))
    with get, derive, build, render as rendered:
        async with await _client() as client:
            response = await client.get('/api/v1/cash-sessions/5/ticket')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': 'Cash session is not closed'}
    rendered.assert_not_awaited()


@pytest.mark.asyncio
async def test_cash_cut_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/cash-sessions/5/ticket')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_cash_cut_requires_the_pos_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        response = await client.get('/api/v1/cash-sessions/5/ticket')

    assert response.status_code == status.HTTP_403_FORBIDDEN


# ── OpenAPI (SC-006) ──────────────────────────────────────────────────────────

# Every print route in contracts/print-endpoints.md that exists so far. Hard-coded rather than
# derived, so a route that forgets `responses=PDF_RESPONSE` fails here instead of vanishing from
# the set.
_PDF_ROUTES = {
    '/api/v1/sales-orders/{sales_order_id}/ticket',
    '/api/v1/sales-orders/{sales_order_id}/document',
    '/api/v1/cash-sessions/{cash_session_id}/ticket',
    '/api/v1/fiscal-documents/{fiscal_document_id}/pdf',
}


def test_pdf_routes_declare_binary_schema() -> None:
    declared = {
        route.path
        for route in app.routes
        if isinstance(route, APIRoute)
        and any('application/pdf' in r.get('content', {}) for r in route.responses.values())
    }
    assert declared
    assert _PDF_ROUTES <= declared

    paths = app.openapi()['paths']
    for path in declared | _PDF_ROUTES:
        content = paths[path]['get']['responses']['200']['content']
        assert content == {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}
