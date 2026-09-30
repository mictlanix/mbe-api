"""Tests for the fiscal document routes (#230, spec 020).

The HTTP contract only: the service, the context builder and the renderer are patched. What goes
into the PDF is covered in tests/unit/test_print_contexts.py, real renders in
tests/integration/test_print_render.py, and the queries in
tests/integration/test_fiscal_document_queries.py.
"""

from collections.abc import Generator
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import status
from httpx import ASGITransport, AsyncClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.main import app
from app.services.cfdi import CfdiError

_PDF = b'%PDF-1.7 fake'
_XML = '<?xml version="1.0" encoding="utf-8"?><cfdi:Comprobante Total="1.00"/>'
_SERVICE = 'app.services.fiscal_document_service'


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


def _document(**overrides) -> SimpleNamespace:
    base = dict(
        fiscal_document_id=9,
        issuer='CAG190523ES5',
        batch='GR',
        serial=4776,
        completed=True,
        cancelled=False,
        version=Decimal('4.0'),
        stamp_uuid='BDCAD3DC-5428-5E2D-89AE-588872E59495',
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# ── PDF ───────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pdf_is_returned_inline() -> None:
    _auth()
    document = _document()
    context = ('fiscal_document.html', {'title': 'Factura'})
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value=_XML)),
        patch(
            'app.services.print_contexts.fiscal_document_context',
            AsyncMock(return_value=context),
        ) as build,
        patch('app.rendering.render_pdf', AsyncMock(return_value=_PDF)) as render,
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'] == 'application/pdf'
    assert response.headers['content-disposition'] == 'inline; filename="CAG190523ES5-GR004776.pdf"'
    assert response.content == _PDF
    assert build.await_args.args[1:] == (document, _XML)
    render.assert_awaited_once_with(*context)


@pytest.mark.asyncio
async def test_pdf_of_a_missing_document_is_404() -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Fiscal document not found'}


@pytest.mark.asyncio
async def test_pdf_requires_authentication() -> None:
    async with await _client() as client:
        response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_pdf_requires_the_fiscal_documents_read_privilege() -> None:
    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('document', 'xml', 'detail'),
    [
        # Checked in this order (contracts/fiscal-documents.md): a draft is refused even when
        # it is also an old version with no stamp.
        (
            _document(completed=False, version=Decimal('3.3'), stamp_uuid=None),
            None,
            'Fiscal document has not been issued',
        ),
        (
            _document(version=Decimal('3.3'), stamp_uuid=None),
            None,
            'Only CFDI 4.0 documents can be printed',
        ),
        (_document(stamp_uuid=None), None, 'Fiscal document is not stamped'),
        (_document(), None, 'Stamped XML is missing or unreadable'),
    ],
    ids=['not-issued', 'version-3.3', 'no-uuid', 'no-xml'],
)
async def test_pdf_conflicts(document: SimpleNamespace, xml: str | None, detail: str) -> None:
    _auth()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value=xml)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': detail}


@pytest.mark.asyncio
async def test_pdf_of_unreadable_xml_is_409() -> None:
    _auth()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_document())),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value='not xml')),
        patch(
            'app.services.print_contexts.fiscal_document_context',
            AsyncMock(side_effect=CfdiError('bad')),
        ),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/pdf')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': 'Stamped XML is missing or unreadable'}


# ── List, detail and XML (US2) ────────────────────────────────────────────────


def _row(**overrides) -> SimpleNamespace:
    """What the service returns: a document with `status`, `total` and, for the detail, lines."""
    base = dict(
        fiscal_document_id=9,
        type=0,
        version=Decimal('4.0'),
        batch='GR',
        serial=4776,
        issuer='CAG190523ES5',
        issuer_name='EMPRESA DEMO',
        recipient='XAÑ010101AB1',
        recipient_name='CLIENTE DEMO',
        issued=None,
        stamp_uuid='BDCAD3DC-5428-5E2D-89AE-588872E59495',
        status='issued',
        total=Decimal('3160.00'),
        issuer_regime='601',
        issuer_regime_name='General de Ley Personas Morales',
        taxpayer_regime='601',
        taxpayer_postal_code='06000',
        usage='G03',
        payment_method=3,
        payment_terms=0,
        currency=0,
        exchange_rate=Decimal('1'),
        issued_location='06000',
        reference=None,
        comment=None,
        stamped=None,
        cancellation_date=None,
        cancellation_reason=None,
        completed=True,
        lines=[
            SimpleNamespace(
                fiscal_document_detail_id=1,
                product_service='11111700',
                product_code='ARENA',
                product_name='ARENA TRITURADA',
                unit_of_measurement='MTQ',
                unit_of_measurement_name='Metro cúbico',
                quantity=Decimal('3'),
                price=Decimal('474.137931'),
                discount=Decimal('0'),
                tax_rate=Decimal('0.16'),
                tax_included=False,
                comment=None,
            )
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_list_passes_every_parameter_to_the_service() -> None:
    _auth()
    with patch(f'{_SERVICE}.list_documents', AsyncMock(return_value=([_row()], 1))) as listed:
        async with await _client() as client:
            response = await client.get(
                '/api/v1/fiscal-documents',
                params={
                    'search': 'GR4776',
                    'issuer': 'CAG190523ES5',
                    'type': 0,
                    'status': 'issued',
                    'date_from': '2026-09-01T00:00:00',
                    'date_to': '2026-09-30T23:59:59',
                    'skip': 20,
                    'limit': 10,
                },
            )

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['total'] == 1
    assert body['items'][0]['fiscal_document_id'] == 9
    assert body['items'][0]['status'] == 'issued'
    assert body['items'][0]['total'] == '3160.00'
    assert 'lines' not in body['items'][0]
    kwargs = listed.await_args.kwargs
    assert kwargs['search'] == 'GR4776'
    assert kwargs['issuer'] == 'CAG190523ES5'
    assert kwargs['type'] == 0
    assert kwargs['status'] == 'issued'
    assert kwargs['date_from'].isoformat() == '2026-09-01T00:00:00'
    assert kwargs['date_to'].isoformat() == '2026-09-30T23:59:59'
    assert (kwargs['skip'], kwargs['limit']) == (20, 10)


@pytest.mark.asyncio
async def test_list_rejects_an_unknown_status() -> None:
    _auth()
    async with await _client() as client:
        response = await client.get('/api/v1/fiscal-documents', params={'status': 'bogus'})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.asyncio
async def test_detail_returns_the_document_with_its_lines() -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_row())):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9')

    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body['usage'] == 'G03'
    assert [line['product_code'] for line in body['lines']] == ['ARENA']


@pytest.mark.asyncio
async def test_detail_of_a_missing_document_is_404() -> None:
    _auth()
    with patch(f'{_SERVICE}.get_document', AsyncMock(return_value=None)):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9')

    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json() == {'detail': 'Fiscal document not found'}


@pytest.mark.asyncio
async def test_xml_is_the_stored_text_as_an_attachment() -> None:
    _auth()
    xml = '<?xml version="1.0" encoding="utf-8"?><cfdi:Comprobante Nombre="ÑANDÚ &amp; CIA"/>'
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=_row(version=Decimal('3.2')))),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value=xml)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/xml')

    assert response.status_code == status.HTTP_200_OK
    assert response.headers['content-type'].startswith('application/xml')
    assert (
        response.headers['content-disposition']
        == 'attachment; filename="CAG190523ES5-GR004776.xml"'
    )
    assert response.content == xml.encode('utf-8')


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('document', 'detail'),
    [
        (_row(completed=False), 'Fiscal document has not been issued'),
        (_row(), 'Stamped XML is missing or unreadable'),
    ],
    ids=['not-issued', 'no-xml'],
)
async def test_xml_conflicts(document: SimpleNamespace, detail: str) -> None:
    _auth()
    with (
        patch(f'{_SERVICE}.get_document', AsyncMock(return_value=document)),
        patch(f'{_SERVICE}.get_xml', AsyncMock(return_value=None)),
    ):
        async with await _client() as client:
            response = await client.get('/api/v1/fiscal-documents/9/xml')

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {'detail': detail}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'path',
    ['/api/v1/fiscal-documents', '/api/v1/fiscal-documents/9', '/api/v1/fiscal-documents/9/xml'],
)
async def test_every_route_requires_authentication_and_the_privilege(path: str) -> None:
    async with await _client() as client:
        assert (await client.get(path)).status_code == status.HTTP_401_UNAUTHORIZED

    _auth(administrator=False)
    _deny_privileges()
    async with await _client() as client:
        assert (await client.get(path)).status_code == status.HTTP_403_FORBIDDEN


def test_xml_route_declares_a_binary_schema() -> None:
    content = app.openapi()['paths']['/api/v1/fiscal-documents/{fiscal_document_id}/xml']['get'][
        'responses'
    ]['200']['content']

    assert content == {'application/xml': {'schema': {'type': 'string', 'format': 'binary'}}}
