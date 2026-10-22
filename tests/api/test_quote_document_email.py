"""The quote document and quote email routes (spec 024).

The HTTP contract only: the quote, its print context and the renderer are patched, so these assert
status codes, headers and the published schema. What the document says is covered by real renders
in tests/integration/test_quote_document_render.py.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
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


def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


# ── Document ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_document_is_the_pdf_inline() -> None:
    _auth()
    quote = SimpleNamespace(sales_quote_id=7)
    context = ('sales_quote.html', {'header': {}})
    with (
        patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=quote)),
        patch('app.services.sales_quote_service.attach_derived', AsyncMock(return_value=quote)),
        patch(
            'app.services.print_contexts.sales_quote_context', AsyncMock(return_value=context)
        ) as build,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with _client() as client:
            response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="cotizacion-00000007.pdf"'
    assert response.content == _PDF
    assert build.await_args.args[1] is quote
    render.assert_awaited_once_with(*context)


@pytest.mark.asyncio
async def test_the_document_of_a_missing_quote_is_404() -> None:
    _auth()
    with patch('app.services.sales_quote_service.get_quote', AsyncMock(return_value=None)):
        async with _client() as client:
            response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Sales quote not found'}


@pytest.mark.asyncio
async def test_the_document_requires_authentication() -> None:
    async with _client() as client:
        response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_the_document_requires_the_sales_quotes_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with _client() as client:
        response = await client.get('/api/v1/sales-quotes/7/document')

    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_the_document_publishes_a_binary_pdf_response() -> None:
    """mbe-ui's generated client reads bytes only when this is the sole 200 content (#230)."""
    responses = app.openapi()['paths']['/api/v1/sales-quotes/{sales_quote_id}/document']['get'][
        'responses'
    ]

    assert responses['200']['content'] == {
        'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}
    }
