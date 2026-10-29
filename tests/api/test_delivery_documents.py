"""Tests for the delivery document endpoints (spec 026).

The HTTP contract only: the order, its context and the renderer are patched, so these assert
status codes, headers and error details. What goes into each document is covered in
tests/integration/test_delivery_documents_render.py.
"""

from collections.abc import Generator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import DeliveryOrderStatus, ItineraryStatus
from app.main import app

_PDF = b'%PDF-1.7 fake'


@pytest.fixture(autouse=True)
def _clear_overrides() -> Generator[None, None, None]:
    yield
    app.dependency_overrides.clear()


def _auth(*, administrator: bool = True, warehouse: object = None) -> None:
    """`warehouse` is what `db.get(Warehouse, …)` answers."""
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=1,
        employee_id=7,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=warehouse))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


def _deny_privileges() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    async def _db():
        yield session

    app.dependency_overrides[get_db] = _db


async def _client() -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


def _order(order_status: int = DeliveryOrderStatus.APPROVED) -> SimpleNamespace:
    return SimpleNamespace(delivery_order_id=42, status=order_status)


def _patches(order: SimpleNamespace | None, *, lines: list | None = None):  # noqa: ANN202
    return (
        patch('app.services.delivery_order_service.get_order', AsyncMock(return_value=order)),
        patch('app.services.delivery_order_service.lines_of', AsyncMock(return_value=lines or [])),
        patch(
            'app.services.print_contexts.delivery_ticket_context',
            AsyncMock(return_value=('delivery_ticket.html', {'header': {}})),
        ),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)),
    )


async def _get(path: str):  # noqa: ANN202
    async with await _client() as client:
        return await client.get(path)


@pytest.mark.asyncio
async def test_delivery_ticket_returns_the_pdf_inline() -> None:
    _auth()
    order = _order()
    get_order, lines_of, build, render = _patches(order)
    with get_order, lines_of, build as build_mock, render as render_mock:
        response = await _get('/api/v1/delivery-orders/42/ticket')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="entrega-00000042.pdf"'
    assert response.content == _PDF
    assert build_mock.await_args.args[1] is order
    assert build_mock.await_args.kwargs == {'warehouse': None}
    render_mock.assert_awaited_once_with('delivery_ticket.html', {'header': {}})


@pytest.mark.asyncio
async def test_delivery_ticket_passes_a_known_warehouse_to_the_context() -> None:
    _auth(warehouse=SimpleNamespace(warehouse_id=3))
    get_order, lines_of, build, render = _patches(
        _order(), lines=[SimpleNamespace(warehouse=3), SimpleNamespace(warehouse=4)]
    )
    with get_order, lines_of, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/ticket?warehouse=3')

    assert response.status_code == status.HTTP_200_OK
    assert build_mock.await_args.kwargs == {'warehouse': 3}


@pytest.mark.asyncio
async def test_delivery_ticket_requires_authentication() -> None:
    assert (await _get('/api/v1/delivery-orders/42/ticket')).status_code == 401


@pytest.mark.asyncio
async def test_delivery_ticket_requires_the_delivery_orders_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    response = await _get('/api/v1/delivery-orders/42/ticket')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
async def test_delivery_ticket_of_a_missing_order_is_404() -> None:
    _auth()
    get_order, lines_of, build, render = _patches(None)
    with get_order, lines_of, build, render:
        response = await _get('/api/v1/delivery-orders/42/ticket')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Delivery order not found'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'order_status', [DeliveryOrderStatus.DRAFT, DeliveryOrderStatus.PENDING_APPROVAL]
)
async def test_delivery_ticket_of_an_unapproved_order_is_409(order_status: int) -> None:
    _auth()
    get_order, lines_of, build, render = _patches(_order(order_status))
    with get_order, lines_of, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/ticket')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == 'Delivery order is not approved'
    build_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_ticket_of_a_cancelled_order_still_renders() -> None:
    _auth()
    get_order, lines_of, build, render = _patches(_order(DeliveryOrderStatus.CANCELLED))
    with get_order, lines_of, build, render:
        response = await _get('/api/v1/delivery-orders/42/ticket')

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_delivery_ticket_warehouse_must_be_an_integer() -> None:
    _auth()
    get_order, lines_of, build, render = _patches(_order())
    with get_order, lines_of, build, render:
        response = await _get('/api/v1/delivery-orders/42/ticket?warehouse=abc')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_delivery_ticket_of_an_unknown_warehouse_is_422() -> None:
    _auth(warehouse=None)
    get_order, lines_of, build, render = _patches(_order(), lines=[SimpleNamespace(warehouse=3)])
    with get_order, lines_of, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/ticket?warehouse=99')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()['detail'] == 'Unknown warehouse'
    build_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_ticket_of_a_warehouse_with_no_line_is_422() -> None:
    _auth(warehouse=SimpleNamespace(warehouse_id=5))
    get_order, lines_of, build, render = _patches(_order(), lines=[SimpleNamespace(warehouse=3)])
    with get_order, lines_of, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/ticket?warehouse=5')

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
    assert response.json()['detail'] == 'No line of this order comes from that warehouse'
    build_mock.assert_not_awaited()


# ── Itinerary documents ───────────────────────────────────────────────────────

_ITINERARY_ROUTES = [
    ('ticket', 'itinerary_ticket_context', 'itinerary_ticket.html', 'itinerario-000042.pdf'),
    ('delivery-notes', 'delivery_notes_context', 'delivery_notes.html', 'acuses-000042.pdf'),
]


def _itinerary(itinerary_status: int = ItineraryStatus.DEPARTED) -> SimpleNamespace:
    return SimpleNamespace(deliveries_itinerary_id=42, status=itinerary_status)


def _itinerary_patches(itinerary: SimpleNamespace | None, context: str, template: str):  # noqa: ANN202
    return (
        patch(
            'app.services.delivery_itinerary_service.get_itinerary',
            AsyncMock(return_value=itinerary),
        ),
        patch(
            f'app.services.print_contexts.{context}',
            AsyncMock(return_value=(template, {'header': {}})),
        ),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(('route', 'context', 'template', 'filename'), _ITINERARY_ROUTES)
@pytest.mark.parametrize('itinerary_status', [ItineraryStatus.DEPARTED, ItineraryStatus.CLOSED])
async def test_itinerary_documents_return_the_pdf_inline(
    route: str, context: str, template: str, filename: str, itinerary_status: int
) -> None:
    _auth()
    itinerary = _itinerary(itinerary_status)
    get, build, render = _itinerary_patches(itinerary, context, template)
    with get, build as build_mock, render as render_mock:
        response = await _get(f'/api/v1/delivery-itineraries/42/{route}')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == f'inline; filename="{filename}"'
    assert response.content == _PDF
    assert build_mock.await_args.args[1] is itinerary
    render_mock.assert_awaited_once_with(template, {'header': {}})


@pytest.mark.asyncio
@pytest.mark.parametrize(('route', 'context', 'template', 'filename'), _ITINERARY_ROUTES)
async def test_itinerary_documents_require_authentication(
    route: str, context: str, template: str, filename: str
) -> None:
    assert (await _get(f'/api/v1/delivery-itineraries/42/{route}')).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(('route', 'context', 'template', 'filename'), _ITINERARY_ROUTES)
async def test_itinerary_documents_require_the_itineraries_read_privilege(
    route: str, context: str, template: str, filename: str
) -> None:
    _auth(administrator=False)
    _deny_privileges()

    response = await _get(f'/api/v1/delivery-itineraries/42/{route}')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize(('route', 'context', 'template', 'filename'), _ITINERARY_ROUTES)
async def test_itinerary_documents_of_a_missing_itinerary_are_404(
    route: str, context: str, template: str, filename: str
) -> None:
    _auth()
    get, build, render = _itinerary_patches(None, context, template)
    with get, build, render:
        response = await _get(f'/api/v1/delivery-itineraries/42/{route}')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Itinerary not found'


@pytest.mark.asyncio
@pytest.mark.parametrize(('route', 'context', 'template', 'filename'), _ITINERARY_ROUTES)
@pytest.mark.parametrize(
    ('itinerary_status', 'detail'),
    [
        (ItineraryStatus.OPEN, 'Itinerary has not departed'),
        (ItineraryStatus.CANCELLED, 'Itinerary is cancelled'),
    ],
)
async def test_itinerary_documents_refuse_an_open_or_cancelled_trip(
    route: str, context: str, template: str, filename: str, itinerary_status: int, detail: str
) -> None:
    _auth()
    get, build, render = _itinerary_patches(_itinerary(itinerary_status), context, template)
    with get, build as build_mock, render:
        response = await _get(f'/api/v1/delivery-itineraries/42/{route}')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == detail
    build_mock.assert_not_awaited()


# ── Letter document ───────────────────────────────────────────────────────────


def _document_patches(order: SimpleNamespace | None):  # noqa: ANN202
    return (
        patch('app.services.delivery_order_service.get_order', AsyncMock(return_value=order)),
        patch(
            'app.services.print_contexts.delivery_order_document_context',
            AsyncMock(return_value=('delivery_order.html', {'header': {}})),
        ),
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)),
    )


@pytest.mark.asyncio
async def test_delivery_document_returns_the_pdf_inline() -> None:
    _auth()
    order = _order()
    get_order, build, render = _document_patches(order)
    with get_order, build as build_mock, render as render_mock:
        response = await _get('/api/v1/delivery-orders/42/document')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == (
        'inline; filename="orden-entrega-00000042.pdf"'
    )
    assert response.content == _PDF
    assert build_mock.await_args.args[1] is order
    render_mock.assert_awaited_once_with('delivery_order.html', {'header': {}})


@pytest.mark.asyncio
async def test_delivery_document_ignores_the_warehouse_filter() -> None:
    _auth()
    get_order, build, render = _document_patches(_order())
    with get_order, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/document?warehouse=3')

    assert response.status_code == status.HTTP_200_OK
    assert build_mock.await_args.kwargs == {}


@pytest.mark.asyncio
async def test_delivery_document_requires_authentication() -> None:
    assert (await _get('/api/v1/delivery-orders/42/document')).status_code == 401


@pytest.mark.asyncio
async def test_delivery_document_requires_the_delivery_orders_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()

    assert (await _get('/api/v1/delivery-orders/42/document')).status_code == 403


@pytest.mark.asyncio
async def test_delivery_document_of_a_missing_order_is_404() -> None:
    _auth()
    get_order, build, render = _document_patches(None)
    with get_order, build, render:
        response = await _get('/api/v1/delivery-orders/42/document')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()['detail'] == 'Delivery order not found'


@pytest.mark.asyncio
async def test_delivery_document_of_a_draft_is_409() -> None:
    _auth()
    get_order, build, render = _document_patches(_order(DeliveryOrderStatus.DRAFT))
    with get_order, build as build_mock, render:
        response = await _get('/api/v1/delivery-orders/42/document')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json()['detail'] == 'Delivery order is not approved'
    build_mock.assert_not_awaited()
