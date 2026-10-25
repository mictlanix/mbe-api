"""Fiscal documents: list, detail, the stored XML and the PDF (#230, spec 020), and the invoice
editor up to a sealed CFDI (#47, spec 027)."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, has_any_privilege, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.fiscal import FiscalDocument
from app.schemas import ListResponse, LocalDateTime
from app.schemas.fiscal import (
    FiscalDocumentResponse,
    FiscalDocumentSummary,
    InvoiceCreate,
    InvoiceLineAdd,
    InvoiceLineUpdate,
    InvoiceRelationAdd,
    InvoiceSalesOrderAdd,
    InvoiceUpdate,
)
from app.services import fiscal_document_service, invoice_service, print_contexts
from app.services.cfdi import CfdiError
from app.services.fiscal_document_service import DocumentStatus

router = APIRouter()

_READ = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.UPDATE)
# A line typed by hand, not drawn from a sales order: legacy's own privilege (FR-007).
_STANDALONE = require_privilege(SystemObject.STANDALONE_FISCAL_DOCUMENTS, AccessRight.CREATE)
_CFDI_40 = Decimal('4.0')
# Declared so the generated client keeps the bytes, as for the PDFs (FR-020).
_XML_RESPONSE = {
    200: {'content': {'application/xml': {'schema': {'type': 'string', 'format': 'binary'}}}}
}


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


async def _document_or_404(db: AsyncSession, fiscal_document_id: int) -> FiscalDocument:
    document = await fiscal_document_service.get_document(db, fiscal_document_id)
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Fiscal document not found'
        )
    return document


def _file_name(document: FiscalDocument, extension: str) -> str:
    """Legacy's `{RFC}-{Batch}{Serial:D6}` (`Resources.resx:1316`)."""
    return f'{document.issuer}-{document.batch or ""}{document.serial or 0:06d}.{extension}'


@router.get('', response_model=ListResponse[FiscalDocumentSummary])
async def list_fiscal_documents(
    search: str | None = Query(None),
    issuer: str | None = Query(None),
    type: int | None = Query(None),  # noqa: A002 — the contract's parameter name
    document_status: DocumentStatus | None = Query(None, alias='status'),
    date_from: LocalDateTime | None = Query(None),
    date_to: LocalDateTime | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[FiscalDocumentSummary]:
    """Newest first. `search` takes a series and folio (`GR4776`), a folio, a UUID, or part of the
    recipient's RFC or name. `total` is the stamped XML's."""
    items, total = await fiscal_document_service.list_documents(
        db,
        search=search,
        issuer=issuer,
        type=type,
        status=document_status,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[FiscalDocumentSummary.model_validate(d) for d in items], total=total)


@router.get('/{fiscal_document_id}', response_model=FiscalDocumentResponse)
async def get_fiscal_document(
    fiscal_document_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    return FiscalDocumentResponse.model_validate(await _document_or_404(db, fiscal_document_id))


@router.get('/{fiscal_document_id}/xml', response_class=Response, responses=_XML_RESPONSE)
async def download_fiscal_document_xml(
    fiscal_document_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The XML exactly as stored, for any version. It is the legally binding document; the PDF
    only represents it (FR-021)."""
    document = await _document_or_404(db, fiscal_document_id)
    if not document.completed:
        raise _conflict('Fiscal document has not been issued')
    xml = await fiscal_document_service.get_xml(db, fiscal_document_id)
    if xml is None:
        raise _conflict('Stamped XML is missing or unreadable')
    return Response(
        xml.encode('utf-8'),
        media_type='application/xml',
        headers={'Content-Disposition': f'attachment; filename="{_file_name(document, "xml")}"'},
    )


@router.get('/{fiscal_document_id}/pdf', response_class=Response, responses=rendering.PDF_RESPONSE)
async def print_fiscal_document(
    fiscal_document_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The letter-size printed representation of a stamped CFDI 4.0, with its SAT QR code."""
    document = await _document_or_404(db, fiscal_document_id)
    if not document.completed:
        raise _conflict('Fiscal document has not been issued')
    if document.version != _CFDI_40:
        raise _conflict('Only CFDI 4.0 documents can be printed')
    if not document.stamp_uuid:
        raise _conflict('Fiscal document is not stamped')
    xml = await fiscal_document_service.get_xml(db, fiscal_document_id)
    if xml is None:
        raise _conflict('Stamped XML is missing or unreadable')
    try:
        template, context = await print_contexts.fiscal_document_context(db, document, xml)
    except CfdiError:
        raise _conflict('Stamped XML is missing or unreadable') from None
    pdf = await rendering.render_pdf(template, context)
    return rendering.pdf_response(pdf, _file_name(document, 'pdf'))


# ── Invoice editor (#47, spec 027) ────────────────────────────────────────────


async def _response(db: AsyncSession, fiscal_document_id: int) -> FiscalDocumentResponse:
    return FiscalDocumentResponse.model_validate(await _document_or_404(db, fiscal_document_id))


@router.post('', response_model=FiscalDocumentResponse, status_code=status.HTTP_201_CREATED)
async def create_invoice(
    data: InvoiceCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """An invoice draft for the caller's store, with the current system's defaults."""
    fiscal_document_id = await invoice_service.create_invoice(db, current, data)
    return await _response(db, fiscal_document_id)


@router.post('/{fiscal_document_id}/sales-orders', response_model=FiscalDocumentResponse)
async def add_sales_order(
    fiscal_document_id: int,
    data: InvoiceSalesOrderAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """The order's invoiceable lines at their remaining quantity; lines already here are kept."""
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.add_sales_order(db, current, document, data.sales_order)
    return await _response(db, fiscal_document_id)


@router.post('/{fiscal_document_id}/lines', response_model=FiscalDocumentResponse)
async def add_invoice_line(
    fiscal_document_id: int,
    data: InvoiceLineAdd,
    current: CurrentUser = Depends(_STANDALONE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """A product by hand at quantity 1 and the customer's price-list price."""
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.add_manual_line(db, current, document, data.product)
    return await _response(db, fiscal_document_id)


@router.put('/{fiscal_document_id}/lines/{line_id}', response_model=FiscalDocumentResponse)
async def update_invoice_line(
    fiscal_document_id: int,
    line_id: int,
    data: InvoiceLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.update_line(db, current, document, line_id, data)
    return await _response(db, fiscal_document_id)


@router.delete('/{fiscal_document_id}/lines/{line_id}', response_model=FiscalDocumentResponse)
async def remove_invoice_line(
    fiscal_document_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.remove_line(db, current, document, line_id)
    return await _response(db, fiscal_document_id)


@router.post('/{fiscal_document_id}/confirm', response_model=FiscalDocumentResponse)
async def confirm_invoice(
    fiscal_document_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """Assign the serial, build and seal the CFDI 4.0 with the issuer's CSD. The invoice is left
    sealed, ready to stamp; no PAC is contacted."""
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.confirm(db, current, document)
    return await _response(db, fiscal_document_id)


@router.put('/{fiscal_document_id}', response_model=FiscalDocumentResponse)
async def update_invoice(
    fiscal_document_id: int,
    data: InvoiceUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """The draft's header; only the fields sent change. Moving it to another store also needs
    Update on IssuedLocationId (103), legacy's separate privilege for it."""
    if 'facility' in data.model_fields_set and not current.administrator:
        grant = ((SystemObject.ISSUED_LOCATION_ID, AccessRight.UPDATE),)
        if not await has_any_privilege(db, current.user_id, grant):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail='Insufficient privileges'
            )
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.update_header(db, current, document, data)
    return await _response(db, fiscal_document_id)


@router.post('/{fiscal_document_id}/relations', response_model=FiscalDocumentResponse)
async def add_invoice_relation(
    fiscal_document_id: int,
    data: InvoiceRelationAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """Relate a stamped document of the same issuer and recipient: 04 substitutes a cancelled
    invoice, 07 applies an advance."""
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.add_relation(db, current, document, data)
    return await _response(db, fiscal_document_id)


@router.delete(
    '/{fiscal_document_id}/relations/{relation_id}', response_model=FiscalDocumentResponse
)
async def remove_invoice_relation(
    fiscal_document_id: int,
    relation_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.remove_relation(db, current, document, relation_id)
    return await _response(db, fiscal_document_id)


@router.post('/{fiscal_document_id}/reopen', response_model=FiscalDocumentResponse)
async def reopen_invoice(
    fiscal_document_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> FiscalDocumentResponse:
    """A sealed, unstamped invoice back to draft. It keeps its serial; the sealed XML is
    discarded and the next confirm seals it again."""
    document = await _document_or_404(db, fiscal_document_id)
    await invoice_service.reopen(db, current, document)
    return await _response(db, fiscal_document_id)
