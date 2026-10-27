"""The invoice editor: create, lines, relations, confirm and reopen (#47, spec 027).

Legacy's `FiscalDocumentsController` is the reference, with the defects the spec lists fixed. A
confirm builds and seals the CFDI with the issuer's CSD and stops there: the document is *sealed*
(research R1) and waits for the stamping library (#46). Nothing here talks to a PAC.

Only drafts change. Every function takes a document loaded by
`fiscal_document_service.get_document`, whose `status` it trusts, and commits its own work.
"""

from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, EntityStatus, FiscalDocumentType, PaymentMethod, PaymentTerms
from app.models.core import Facility
from app.models.customer import Customer, TaxpayerRecipient, customer_taxpayer
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentRelation,
    FiscalDocumentXml,
    TaxpayerBatch,
    TaxpayerCertificate,
    TaxpayerIssuer,
)
from app.models.product import Product, ProductPrice
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.models.sat_catalog import SatCfdiUsage, SatTaxRegime, SatUnitOfMeasurement
from app.schemas.fiscal import (
    CreditNoteCreate,
    InvoiceCreate,
    InvoiceLineCopy,
    InvoiceLineUpdate,
    InvoiceRelationAdd,
    InvoiceUpdate,
    PaidInvoiceAdd,
    PaidInvoiceUpdate,
    PaymentReceiptCreate,
    relation_code,
)
from app.services import cfdi_invoice, cfdi_payment, customer_payment_service, sales_order_service
from app.services import fiscal_document_service as fds

#: The general public RFC: its postal code is the issuing store's (FR-004).
GENERAL_PUBLIC = 'XAXX010101000'
DEFAULT_USAGE = 'G01'
CFDI_VERSION = Decimal('4.0')
ISSUE_WINDOW = timedelta(hours=72)

#: Seals the prepared document with the chosen certificate; returns the XML.
SealBuilder = Callable[[TaxpayerCertificate], Awaitable[str]]

INVOICE: Literal['invoice'] = 'invoice'
REFUND: Literal['refund'] = 'refund'


@dataclass(frozen=True)
class LineEvent:
    """Something that took units of an order line: an invoice or a refund (research R6)."""

    at: datetime
    kind: Literal['invoice', 'refund']
    quantity: Decimal


def _error(code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=code, detail=detail)


def _unprocessable(detail: str) -> HTTPException:
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


# ── Pure rules ────────────────────────────────────────────────────────────────


def remaining_quantity(quantity: Decimal, events: Iterable[LineEvent]) -> Decimal:
    """What is still invoiceable of an order line (FR-006, clarified 2026-10-25).

    Events count in time order. A refund only takes the units not yet invoiced when it was
    recorded; returning goods that were already invoiced needs a credit note, not a smaller
    invoice. At equal times a refund goes first, which never overstates what can be invoiced.
    """
    invoiced = refunded = Decimal(0)
    for event in sorted(events, key=lambda e: (e.at, e.kind != REFUND)):
        if event.kind == INVOICE:
            invoiced += event.quantity
        else:
            refunded += min(event.quantity, max(Decimal(0), quantity - invoiced - refunded))
    return max(Decimal(0), quantity - invoiced - refunded)


def default_batch(
    store_default: str | None, invoice_batches: Sequence[TaxpayerBatch]
) -> str | None:
    """The store's default series when it is an invoice series of the issuer, else the first."""
    series = [b.batch for b in invoice_batches]
    if store_default and store_default in series:
        return store_default
    return series[0] if series else None


def default_terms(customer: Customer) -> PaymentTerms:
    """Legacy's `Customer.HasCredit`: credit days *and* a credit limit (`Customer.cs:79`)."""
    has_credit = customer.credit_days > 0 and (customer.credit_limit or 0) > 0
    return PaymentTerms.NET_D if has_credit else PaymentTerms.IMMEDIATE


def issue_date_ok(issued: datetime, *, now: datetime) -> bool:
    """Within the last 72 hours and not in the future (FR-009)."""
    return now - ISSUE_WINDOW < issued <= now


# ── Loading ───────────────────────────────────────────────────────────────────


def _require_draft(document: FiscalDocument) -> None:
    if document.status != 'draft':
        raise _error(status.HTTP_409_CONFLICT, 'Fiscal document is not a draft')


def _require_type(document: FiscalDocument, *types: FiscalDocumentType) -> None:
    if document.type not in types:
        raise _error(status.HTTP_409_CONFLICT, 'Not available for this document type')


#: Header fields each document type takes (spec 028, research R7). A receipt keeps its ordering
#: bank in `reference`, as legacy does, so it does not take `reference` itself.
_HEADER_FIELDS = {
    FiscalDocumentType.INVOICE: {
        'recipient',
        'batch',
        'usage',
        'payment_terms',
        'payment_method',
        'issued',
        'facility',
        'reference',
        'comment',
    },
    FiscalDocumentType.CREDIT_NOTE: {'payment_method', 'usage', 'issued', 'reference', 'comment'},
    FiscalDocumentType.PAYMENT_RECEIPT: {
        'payment_date',
        'payment_amount',
        'payment_method',
        'payment_reference',
        'ordering_bank',
        'issued',
        'comment',
    },
}

_SERIES_NAMES = {
    FiscalDocumentType.INVOICE: 'invoice',
    FiscalDocumentType.CREDIT_NOTE: 'credit note',
    FiscalDocumentType.PAYMENT_RECEIPT: 'payment receipt',
}


def _touch(document: FiscalDocument, current: CurrentUser) -> None:
    document.updater = current.employee_id
    document.modification_time = local_now()


async def _batches_of(
    db: AsyncSession, issuer: str, type_: FiscalDocumentType
) -> list[TaxpayerBatch]:
    query = (
        select(TaxpayerBatch)
        .where(TaxpayerBatch.taxpayer == issuer, TaxpayerBatch.type == type_)
        .order_by(TaxpayerBatch.taxpayer_batch_id)
    )
    return list((await db.execute(query)).scalars())


async def _invoice_batches(db: AsyncSession, issuer: str) -> list[TaxpayerBatch]:
    return await _batches_of(db, issuer, FiscalDocumentType.INVOICE)


async def default_batch_for(
    db: AsyncSession, issuer: str, facility: Facility, type_: FiscalDocumentType
) -> str:
    """The store's default series when it is one of the issuer's of this type, else the first."""
    batch = default_batch(facility.default_batch, await _batches_of(db, issuer, type_))
    if batch is None:
        raise _unprocessable(f'The issuer has no {_SERIES_NAMES[type_]} series')
    return batch


async def _linked_recipient(db: AsyncSession, customer: int, rfc: str) -> TaxpayerRecipient:
    linked = await db.execute(
        select(customer_taxpayer.c.taxpayer).where(
            customer_taxpayer.c.customer == customer, customer_taxpayer.c.taxpayer == rfc
        )
    )
    recipient = await db.get(TaxpayerRecipient, rfc) if linked.first() else None
    if recipient is None:
        raise _unprocessable("The recipient is not one of the customer's taxpayers")
    return recipient


def _copy_recipient(
    document: FiscalDocument, recipient: TaxpayerRecipient, facility: Facility
) -> None:
    document.recipient = recipient.taxpayer_recipient_id
    document.recipient_name = recipient.name
    document.taxpayer_regime = recipient.regime
    document.taxpayer_postal_code = (
        facility.location
        if recipient.taxpayer_recipient_id == GENERAL_PUBLIC
        else recipient.postal_code
    )


async def _lines(db: AsyncSession, document_id: int) -> list[FiscalDocumentDetail]:
    query = (
        select(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.document == document_id)
        .order_by(FiscalDocumentDetail.fiscal_document_detail_id)
    )
    return list((await db.execute(query)).scalars())


async def _line_or_404(
    db: AsyncSession, document: FiscalDocument, line_id: int
) -> FiscalDocumentDetail:
    line = await db.get(FiscalDocumentDetail, line_id)
    if line is None or line.document != document.fiscal_document_id:
        raise _error(status.HTTP_404_NOT_FOUND, 'Line not found')
    return line


# ── Create (FR-003, FR-004; research R7) ──────────────────────────────────────


async def _new_document(
    db: AsyncSession,
    current: CurrentUser,
    *,
    type_: FiscalDocumentType,
    customer_id: int,
    recipient_rfc: str,
    issuer_rfc: str | None,
    batch: str | None,
) -> tuple[FiscalDocument, Customer]:
    """A draft of any type with the store, issuer, series and recipient copied (research R7).

    Not added to the session: the caller sets its type's defaults first.
    """
    if current.facility_id is None:
        raise _unprocessable('No facility is configured for your user')
    facility = await db.get(Facility, current.facility_id)
    if facility is None:
        raise _unprocessable('No facility is configured for your user')
    issuer = await db.get(TaxpayerIssuer, issuer_rfc or facility.taxpayer)
    if issuer is None:
        raise _unprocessable('Taxpayer issuer not found')
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Customer not found')
    recipient = await _linked_recipient(db, customer.customer_id, recipient_rfc)

    if batch is not None:
        if batch not in {b.batch for b in await _batches_of(db, issuer.taxpayer_issuer_id, type_)}:
            name = _SERIES_NAMES[type_]
            article = 'an' if name[0] in 'aeiou' else 'a'
            raise _unprocessable(f'The series is not {article} {name} series of the issuer')
    else:
        batch = await default_batch_for(db, issuer.taxpayer_issuer_id, facility, type_)
    regime = await db.get(SatTaxRegime, issuer.regime)

    now = local_now()
    document = FiscalDocument(
        creation_time=now,
        modification_time=now,
        creator=current.employee_id,
        updater=current.employee_id,
        issuer=issuer.taxpayer_issuer_id,
        issuer_name=issuer.name,
        issuer_regime=issuer.regime,
        issuer_regime_name=regime.description if regime else None,
        customer=customer.customer_id,
        type=type_,
        facility=facility.facility_id,
        batch=batch,
        issued_at=facility.address,
        issued_location=facility.location,
        completed=False,
        cancelled=False,
        payment_method=PaymentMethod.TO_BE_DEFINED,
        exchange_rate=Decimal(1),
        currency=CurrencyCode.MXN,
        payment_terms=PaymentTerms.IMMEDIATE,
        usage=DEFAULT_USAGE,
        version=Decimal(0),
        provider=0,
        retention_rate=Decimal(0),
        local_retention_rate=Decimal(0),
    )
    _copy_recipient(document, recipient, facility)
    return document, customer


async def create_invoice(db: AsyncSession, current: CurrentUser, data: InvoiceCreate) -> int:
    document, customer = await _new_document(
        db,
        current,
        type_=FiscalDocumentType.INVOICE,
        customer_id=data.customer,
        recipient_rfc=data.recipient,
        issuer_rfc=data.issuer,
        batch=data.batch,
    )
    document.payment_terms = default_terms(customer)
    db.add(document)
    await db.commit()
    return document.fiscal_document_id


# ── Remaining quantity (FR-006; research R6) ──────────────────────────────────


async def remaining_by_order_line(
    db: AsyncSession, details: Sequence[SalesOrderDetail], *, exclude_document: int
) -> dict[int, Decimal]:
    """Each order line's invoiceable quantity, leaving `exclude_document`'s own lines out.

    Invoices count when sealed or issued and not cancelled; drafts reserve nothing. Refunds count
    when completed and not cancelled.
    """
    ids = [d.sales_order_detail_id for d in details]
    if not ids:
        return {}
    events: dict[int, list[LineEvent]] = defaultdict(list)

    sealed = exists().where(
        FiscalDocumentXml.fiscal_document_xml_id == FiscalDocument.fiscal_document_id
    )
    invoiced = await db.execute(
        select(
            FiscalDocumentDetail.order_detail,
            FiscalDocumentDetail.quantity,
            func.coalesce(FiscalDocument.issued, FiscalDocument.creation_time),
        )
        .join(FiscalDocument, FiscalDocument.fiscal_document_id == FiscalDocumentDetail.document)
        .where(
            FiscalDocumentDetail.order_detail.in_(ids),
            # Only invoices take units: a credit note's copied lines keep their order line too.
            FiscalDocument.type == FiscalDocumentType.INVOICE,
            FiscalDocument.fiscal_document_id != exclude_document,
            FiscalDocument.cancelled.is_(False),
            or_(FiscalDocument.completed.is_(True), sealed),
        )
    )
    for detail, quantity, at in invoiced.all():
        events[detail].append(LineEvent(at, INVOICE, quantity))

    refunded = await db.execute(
        select(
            CustomerRefundDetail.sales_order_detail,
            CustomerRefundDetail.quantity,
            func.coalesce(CustomerRefund.date, CustomerRefund.modification_time),
        )
        .join(
            CustomerRefund,
            CustomerRefund.customer_refund_id == CustomerRefundDetail.customer_refund,
        )
        .where(
            CustomerRefundDetail.sales_order_detail.in_(ids),
            CustomerRefund.completed.is_(True),
            CustomerRefund.cancelled.is_(False),
        )
    )
    for detail, quantity, at in refunded.all():
        events[detail].append(LineEvent(at, REFUND, quantity))

    return {
        d.sales_order_detail_id: remaining_quantity(d.quantity, events[d.sales_order_detail_id])
        for d in details
    }


# ── Lines (FR-005, FR-007, FR-011; research R8) ───────────────────────────────


async def add_sales_order(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, sales_order_id: int
) -> None:
    _require_draft(document)
    _require_type(document, FiscalDocumentType.INVOICE)
    order = await db.get(SalesOrder, sales_order_id)
    if order is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Sales order not found')
    if not order.completed or order.cancelled:
        raise _unprocessable('The sales order is not completed or is cancelled')

    rows = await db.execute(
        select(SalesOrderDetail, Product, SatUnitOfMeasurement.name)
        .join(Product, Product.product_id == SalesOrderDetail.product)
        .outerjoin(
            SatUnitOfMeasurement,
            SatUnitOfMeasurement.sat_unit_of_measurement_id == Product.unit_of_measurement,
        )
        .where(SalesOrderDetail.sales_order == sales_order_id, Product.invoiceable.is_(True))
        .order_by(SalesOrderDetail.sales_order_detail_id)
    )
    candidates = rows.all()
    on_draft = {line.order_detail for line in await _lines(db, document.fiscal_document_id)}
    new = [row for row in candidates if row[0].sales_order_detail_id not in on_draft]
    remaining = await remaining_by_order_line(
        db, [row[0] for row in new], exclude_document=document.fiscal_document_id
    )
    added = 0
    for detail, product, unit_name in new:
        quantity = remaining[detail.sales_order_detail_id]
        if quantity <= 0:
            continue
        db.add(
            FiscalDocumentDetail(
                document=document.fiscal_document_id,
                product=product.product_id,
                order_detail=detail.sales_order_detail_id,
                product_service=product.key,
                product_code=detail.product_code,
                product_name=detail.product_name,
                unit_of_measurement=product.unit_of_measurement,
                unit_of_measurement_name=unit_name,
                quantity=quantity,
                price=detail.price,
                discount=detail.discount_rate,
                tax_rate=detail.tax_rate,
                exchange_rate=document.exchange_rate,
                currency=document.currency,
                tax_included=detail.tax_included,
                comment=detail.comment,
            )
        )
        added += 1
    already_there = len(candidates) > len(new)
    if not added and not already_there:
        raise _unprocessable('No invoiceable lines were found')
    _touch(document, current)
    await db.commit()


async def add_manual_line(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, product_id: int
) -> None:
    """A product by hand, at quantity 1 and the customer's price-list price (legacy `AddItem`).

    The route requires Create on StandaloneFiscalDocuments (56). Invoices are MXN only, so the
    price is not converted as legacy's multi-currency branch did.
    """
    _require_draft(document)
    product = await db.get(Product, product_id)
    if product is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Product not found')
    customer = await db.get(Customer, document.customer)
    price = (
        await db.execute(
            select(ProductPrice.price).where(
                ProductPrice.product == product_id,
                ProductPrice.price_list == customer.price_list,
            )
        )
    ).scalar_one_or_none()
    if price is None:
        raise _unprocessable("The product has no price in the customer's price list")
    unit = await db.get(SatUnitOfMeasurement, product.unit_of_measurement)
    db.add(
        FiscalDocumentDetail(
            document=document.fiscal_document_id,
            product=product.product_id,
            product_service=product.key,
            product_code=product.code,
            product_name=product.name,
            unit_of_measurement=product.unit_of_measurement,
            unit_of_measurement_name=unit.name if unit else None,
            quantity=Decimal(1),
            price=price,
            discount=Decimal(0),
            tax_rate=product.tax_rate,
            exchange_rate=document.exchange_rate,
            currency=document.currency,
            tax_included=product.tax_included,
        )
    )
    _touch(document, current)
    await db.commit()


async def update_line(
    db: AsyncSession,
    current: CurrentUser,
    document: FiscalDocument,
    line_id: int,
    data: InvoiceLineUpdate,
) -> None:
    _require_draft(document)
    line = await _line_or_404(db, document, line_id)
    changes = data.model_dump(include=data.model_fields_set)
    credit_note = document.type == FiscalDocumentType.CREDIT_NOTE
    if changes.get('quantity') is not None and credit_note:
        await _check_credited_quantity(db, document, line.product, changes['quantity'])
    elif changes.get('quantity') is not None and line.order_detail is not None:
        detail = await db.get(SalesOrderDetail, line.order_detail)
        remaining = (
            await remaining_by_order_line(
                db, [detail], exclude_document=document.fiscal_document_id
            )
        )[line.order_detail]
        if changes['quantity'] > remaining:
            raise _unprocessable(f'Line quantity exceeds the {remaining:f} still invoiceable')
    if 'product_name' in changes and not (changes['product_name'] or '').strip():
        product = await db.get(Product, line.product)
        changes['product_name'] = product.name
    for field, value in changes.items():
        if value is None and field != 'comment':
            continue
        setattr(line, field, value)
    _touch(document, current)
    await db.commit()


async def remove_line(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, line_id: int
) -> None:
    _require_draft(document)
    line = await _line_or_404(db, document, line_id)
    await db.delete(line)
    _touch(document, current)
    await db.commit()


# ── Confirm (FR-013, FR-014; research R2, R4, R10) ────────────────────────────


async def _validate_for_confirm(
    db: AsyncSession, document: FiscalDocument, lines: Sequence[FiscalDocumentDetail]
) -> None:
    if not lines:
        raise _unprocessable('The invoice has no lines')
    for number, line in enumerate(lines, start=1):
        if not line.product_service or not line.unit_of_measurement:
            raise _unprocessable(f'Line {number}: missing SAT product key or unit')
        if line.quantity <= 0:
            raise _unprocessable(f'Line {number}: the quantity must be positive')
    order_lines = [line for line in lines if line.order_detail is not None]
    if order_lines:
        details = (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order_detail_id.in_(
                        [line.order_detail for line in order_lines]
                    )
                )
            )
        ).scalars()
        remaining = await remaining_by_order_line(
            db, list(details), exclude_document=document.fiscal_document_id
        )
        for number, line in enumerate(lines, start=1):
            if line.order_detail is not None and line.quantity > remaining[line.order_detail]:
                raise _unprocessable(
                    f'Line {number}: quantity exceeds the '
                    f'{remaining[line.order_detail]:f} still invoiceable'
                )
    if not document.taxpayer_regime or not document.taxpayer_postal_code:
        raise _unprocessable('The recipient has no tax regime or postal code')


async def _certificate(db: AsyncSession, issuer: str, issued: datetime) -> TaxpayerCertificate:
    """The issuer's active CSD valid at the issue date; the latest to expire if several (R4)."""
    query = (
        select(TaxpayerCertificate)
        .where(
            TaxpayerCertificate.taxpayer == issuer,
            TaxpayerCertificate.status == EntityStatus.ACTIVE,
            TaxpayerCertificate.valid_from <= issued,
            TaxpayerCertificate.valid_to >= issued,
        )
        .order_by(TaxpayerCertificate.valid_to.desc())
        .limit(1)
    )
    certificate = (await db.execute(query)).scalar_one_or_none()
    if certificate is None:
        raise _unprocessable('The issuer has no active certificate valid at the issue date')
    return certificate


async def _next_serial(db: AsyncSession, issuer: str, batch: str | None) -> int:
    """MAX + 1 for the issuer and series, safe under concurrent confirms (research R2).

    The batch row lock admits one confirm per series at a time. The maximum must then be a
    *locking* read: under REPEATABLE READ a plain SELECT reads the snapshot this request's earlier
    reads opened, and would miss the serial the previous lock holder committed while this request
    waited — the mictlanix/mbe#45 duplicate.
    """
    locked = await db.execute(
        select(TaxpayerBatch.taxpayer_batch_id)
        .where(TaxpayerBatch.taxpayer == issuer, TaxpayerBatch.batch == batch)
        .with_for_update()
    )
    if locked.first() is None:
        raise _unprocessable('The invoice series no longer exists')
    highest = await db.execute(
        select(func.max(FiscalDocument.serial))
        .where(FiscalDocument.issuer == issuer, FiscalDocument.batch == batch)
        .with_for_update(read=True)
    )
    return (highest.scalar_one() or 0) + 1


async def _relations(db: AsyncSession, document_id: int) -> tuple[str | None, list[str]]:
    rows = await db.execute(
        select(FiscalDocumentRelation.type, FiscalDocument.stamp_uuid)
        .join(FiscalDocument, FiscalDocument.fiscal_document_id == FiscalDocumentRelation.relation)
        .where(FiscalDocumentRelation.document == document_id)
        .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
    )
    found = rows.all()
    if not found:
        return None, []
    return relation_code(found[0][0]), [uuid for _, uuid in found]


def _invoice_data(
    document: FiscalDocument,
    lines: Sequence[FiscalDocumentDetail],
    relation_type: str | None,
    relation_uuids: Sequence[str],
) -> cfdi_invoice.InvoiceData:
    return cfdi_invoice.InvoiceData(
        batch=document.batch,
        serial=document.serial,
        issued=document.issued,
        payment_method=document.payment_method,
        payment_terms=PaymentTerms(document.payment_terms),
        issued_location=document.issued_location,
        issuer=document.issuer,
        issuer_name=document.issuer_name,
        issuer_regime=document.issuer_regime,
        recipient=document.recipient,
        recipient_name=document.recipient_name,
        recipient_postal_code=document.taxpayer_postal_code,
        recipient_regime=document.taxpayer_regime,
        usage=document.usage or DEFAULT_USAGE,
        lines=[
            cfdi_invoice.InvoiceLineData(
                product_service=line.product_service,
                product_code=line.product_code,
                quantity=line.quantity,
                unit=line.unit_of_measurement,
                unit_name=line.unit_of_measurement_name,
                description=line.product_name,
                price=line.price,
                discount_rate=line.discount,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
            for line in lines
        ],
        relation_type=relation_type,
        relation_uuids=relation_uuids,
    )


async def _prepare_invoice(db: AsyncSession, document: FiscalDocument) -> SealBuilder:
    lines = await _lines(db, document.fiscal_document_id)
    await _validate_for_confirm(db, document, lines)
    relation_type, relation_uuids = await _relations(db, document.fiscal_document_id)

    def build(certificate: TaxpayerCertificate) -> Awaitable[str]:
        return cfdi_invoice.seal_async(
            _invoice_data(document, lines, relation_type, relation_uuids),
            certificate_number=certificate.taxpayer_certificate_id,
            certificate_data=certificate.certificate_data,
            key_data=certificate.key_data,
            key_password=certificate.key_password,
        )

    return build


async def confirm(db: AsyncSession, current: CurrentUser, document: FiscalDocument) -> None:
    """Validate, assign the serial, seal and store the XML, in one transaction (FR-013).

    Each type prepares inside the transaction, so a receipt's or credit note's row locks on its
    invoices are held until the commit (spec 028, research R2).
    """
    _require_draft(document)
    prepare = {
        FiscalDocumentType.INVOICE: _prepare_invoice,
        FiscalDocumentType.CREDIT_NOTE: _prepare_credit_note,
        FiscalDocumentType.PAYMENT_RECEIPT: _prepare_receipt,
    }[FiscalDocumentType(document.type)]
    try:
        build = await prepare(db, document)
        now = local_now()
        issued = document.issued or now.replace(microsecond=0)
        if not issue_date_ok(issued, now=now):
            raise _unprocessable(
                'The issue date must be within the last 72 hours and not in the future'
            )
        certificate = await _certificate(db, document.issuer, issued)
        issuer = await db.get(TaxpayerIssuer, document.issuer)
        if document.serial is None:
            document.serial = await _next_serial(db, document.issuer, document.batch)
        document.issued = issued
        try:
            xml = await build(certificate)
        except ValueError as error:
            raise _unprocessable(f"The issuer's certificate cannot sign: {error}") from error
        db.add(FiscalDocumentXml(fiscal_document_xml_id=document.fiscal_document_id, data=xml))
        document.version = CFDI_VERSION
        document.provider = issuer.provider
        _touch(document, current)
        if document.type == FiscalDocumentType.CREDIT_NOTE:
            # Sealed, the note counts: it lowers the balance of the orders on its invoice.
            await db.flush()
            orders = await _credited_orders(db, document)
            balances = await sales_order_service.order_balances(db, orders)
            for order in orders:
                customer_payment_service.settle(order, balances[order.sales_order_id].balance)
        await db.commit()
    except BaseException:
        await db.rollback()
        raise


# ── Header (FR-008, FR-009; research R10) ─────────────────────────────────────


async def update_header(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, data: InvoiceUpdate
) -> None:
    """Only the fields sent change. The route checks the store privilege (103) for `facility`.

    Each document type takes its own fields (`_HEADER_FIELDS`); a receipt's payment form must be
    the actual one, never 99.
    """
    _require_draft(document)
    sent = data.model_fields_set
    foreign = sorted(sent - _HEADER_FIELDS[FiscalDocumentType(document.type)])
    if foreign:
        raise _unprocessable(f'Not a field of this document type: {", ".join(foreign)}')
    receipt = document.type == FiscalDocumentType.PAYMENT_RECEIPT
    if receipt and data.payment_method == PaymentMethod.TO_BE_DEFINED:
        raise _unprocessable('A payment receipt needs the actual payment form')

    if 'facility' in sent:
        facility = await db.get(Facility, data.facility) if data.facility is not None else None
        if facility is None:
            raise _unprocessable('Store not found')
        document.facility = facility.facility_id
        document.issued_at = facility.address
        document.issued_location = facility.location
    facility = await db.get(Facility, document.facility)
    if 'recipient' in sent and data.recipient is not None:
        _copy_recipient(
            document, await _linked_recipient(db, document.customer, data.recipient), facility
        )
    elif 'facility' in sent and document.recipient == GENERAL_PUBLIC:
        document.taxpayer_postal_code = facility.location

    if 'batch' in sent and data.batch is not None and data.batch != document.batch:
        if document.serial is not None:
            raise _error(
                status.HTTP_409_CONFLICT,
                'The series cannot change once the invoice has a serial',
            )
        if data.batch not in {b.batch for b in await _invoice_batches(db, document.issuer)}:
            raise _unprocessable('The series is not an invoice series of the issuer')
        document.batch = data.batch
    if 'usage' in sent and data.usage is not None:
        if await db.get(SatCfdiUsage, data.usage) is None:
            raise _unprocessable('Unknown CFDI usage')
        document.usage = data.usage
    if 'payment_terms' in sent and data.payment_terms is not None:
        document.payment_terms = data.payment_terms
    if 'payment_method' in sent and data.payment_method is not None:
        document.payment_method = data.payment_method
    if document.type == FiscalDocumentType.INVOICE and document.payment_terms == PaymentTerms.NET_D:
        # PPD: the form of payment is not known yet (SAT's rule, and legacy's).
        document.payment_method = PaymentMethod.TO_BE_DEFINED
    if 'issued' in sent:
        if data.issued is not None and not issue_date_ok(data.issued, now=local_now()):
            raise _unprocessable(
                'The issue date must be within the last 72 hours and not in the future'
            )
        document.issued = data.issued
    if 'reference' in sent:
        document.reference = data.reference
    if 'comment' in sent:
        document.comment = data.comment
    if 'payment_date' in sent:
        document.payment_date = data.payment_date
    if 'payment_amount' in sent:
        document.payment_amount = data.payment_amount
    if 'payment_reference' in sent:
        document.payment_reference = data.payment_reference
    if 'ordering_bank' in sent:
        document.reference = data.ordering_bank
    if receipt:
        await _check_receipt_header(db, document)
    _touch(document, current)
    await db.commit()


# ── Relations (FR-010; research R9) ───────────────────────────────────────────


def _qualifies(target: FiscalDocument, code: str) -> bool:
    """Legacy's relation search: 04 substitutes a cancelled invoice, 07 applies an advance."""
    if not target.completed or not target.stamp_uuid:
        return False
    if code == '04':
        return target.type < FiscalDocumentType.CREDIT_NOTE and target.cancelled
    return not target.cancelled and target.type not in (
        FiscalDocumentType.CREDIT_NOTE,
        FiscalDocumentType.PAYMENT_RECEIPT,
    )


async def add_relation(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, data: InvoiceRelationAdd
) -> None:
    _require_draft(document)
    _require_type(document, FiscalDocumentType.INVOICE)
    target = await db.get(FiscalDocument, data.relation)
    if target is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Related document not found')
    same_parties = target.issuer == document.issuer and target.recipient == document.recipient
    if not same_parties or not _qualifies(target, data.type):
        raise _unprocessable(f'The related document does not qualify for relation type {data.type}')
    existing = (
        (
            await db.execute(
                select(FiscalDocumentRelation).where(
                    FiscalDocumentRelation.document == document.fiscal_document_id
                )
            )
        )
        .scalars()
        .all()
    )
    if any(r.relation == target.fiscal_document_id for r in existing):
        raise _error(status.HTTP_409_CONFLICT, 'The document is already related')
    if any(relation_code(r.type) != data.type for r in existing):
        raise _unprocessable('All relations must share one type')
    db.add(
        FiscalDocumentRelation(
            document=document.fiscal_document_id,
            relation=target.fiscal_document_id,
            # Stored as cfdlib's enum number, as legacy does: '4', '7' (research R5).
            type=str(int(data.type)),
            exchange_rate=Decimal(1),
            installment=1,
            previous_balance=Decimal(0),
            amount=Decimal(0),
        )
    )
    _touch(document, current)
    await db.commit()


async def remove_relation(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, relation_id: int
) -> None:
    _require_draft(document)
    relation = await db.get(FiscalDocumentRelation, relation_id)
    if relation is None or relation.document != document.fiscal_document_id:
        raise _error(status.HTTP_404_NOT_FOUND, 'Relation not found')
    await db.delete(relation)
    _touch(document, current)
    await db.commit()


# ── Reopen (FR-015) ───────────────────────────────────────────────────────────


async def reopen(db: AsyncSession, current: CurrentUser, document: FiscalDocument) -> None:
    """Back to draft: the sealed XML goes, the serial stays for the next confirm to reuse.

    A credit note stops counting once its XML goes, so the orders on its invoice owe again, and one
    it had settled is flagged unpaid (spec 033, FR-011).
    """
    if document.status != 'sealed':
        raise _error(status.HTTP_409_CONFLICT, 'Fiscal document is not sealed')
    credit_note = document.type == FiscalDocumentType.CREDIT_NOTE
    orders = await _credited_orders(db, document) if credit_note else []
    before = await sales_order_service.order_balances(db, orders)
    xml = await db.get(FiscalDocumentXml, document.fiscal_document_id)
    await db.delete(xml)
    _touch(document, current)
    if orders:
        await db.flush()
        after = await sales_order_service.order_balances(db, orders)
        for order in orders:
            oid = order.sales_order_id
            customer_payment_service.reopen(order, before[oid].balance, after[oid].balance)
    await db.commit()


async def _credited_orders(db: AsyncSession, note: FiscalDocument) -> list[SalesOrder]:
    """The orders on a credit note's invoice, locked in ascending id after the invoice (spec 033,
    research R4): invoices → orders, the order a payment's application never takes in reverse."""
    invoice = (
        await db.execute(
            select(FiscalDocumentRelation.relation)
            .where(FiscalDocumentRelation.document == note.fiscal_document_id)
            .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if invoice is None:
        return []
    await db.execute(
        select(FiscalDocument.fiscal_document_id)
        .where(FiscalDocument.fiscal_document_id == invoice)
        .with_for_update()
    )
    order_ids = (
        select(SalesOrderDetail.sales_order)
        .join(
            FiscalDocumentDetail,
            FiscalDocumentDetail.order_detail == SalesOrderDetail.sales_order_detail_id,
        )
        .where(FiscalDocumentDetail.document == invoice)
    )
    return list(
        (
            await db.execute(
                select(SalesOrder)
                .where(SalesOrder.sales_order_id.in_(order_ids))
                .order_by(SalesOrder.sales_order_id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


# ── Payment receipts (#53, spec 028; research R2, R7) ─────────────────────────

RECEIPT_USAGE = 'CP01'
_RECEIPT_REFUSED = 'The invoice cannot be paid by this receipt'
_OVER_PAYMENT = 'The amounts paid exceed the payment amount'
_PAYMENT_DATE = "The payment date must be between the invoices' dates and today"


def installment(earlier: int) -> int:
    return earlier + 1


def previous_balance(total: Decimal, paid: Decimal, credited: Decimal) -> Decimal:
    return total - paid - credited


def default_amount(balance: Decimal, unassigned: Decimal | None) -> Decimal:
    """What a newly paid invoice receives: its balance, up to what the payment has left."""
    amount = balance if unassigned is None else min(balance, unassigned)
    return amount if amount > 0 else Decimal(0)


def _payable(receipt: FiscalDocument, invoice: FiscalDocument) -> bool:
    """A stamped, live PPD invoice of the receipt's issuer and recipient (FR-003)."""
    return (
        invoice.type == FiscalDocumentType.INVOICE
        and invoice.completed
        and not invoice.cancelled
        and bool(invoice.stamp_uuid)
        and invoice.payment_terms == PaymentTerms.NET_D
        and invoice.issuer == receipt.issuer
        and invoice.recipient == receipt.recipient
    )


def _check_payment_date(receipt: FiscalDocument, invoices: Iterable[FiscalDocument]) -> None:
    if receipt.payment_date is None:
        return
    if any(i.issued and receipt.payment_date < i.issued.date() for i in invoices):
        raise _unprocessable(_PAYMENT_DATE)


def _check_amount(amount: Decimal, balance: Decimal, unassigned: Decimal | None) -> None:
    if amount <= 0 or amount > balance:
        raise _unprocessable(f'Amount exceeds the invoice balance of {balance:.2f}')
    if unassigned is not None and amount > unassigned:
        raise _unprocessable(_OVER_PAYMENT)


async def _paid_relations(db: AsyncSession, receipt: int) -> list[FiscalDocumentRelation]:
    # In the order billing added them. Legacy reads them unordered, so its XML follows whatever
    # plan MariaDB picks: by row on RHP 12 and RHP 23, by invoice on CGR 10 (SC-002).
    query = (
        select(FiscalDocumentRelation)
        .where(FiscalDocumentRelation.document == receipt)
        .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
    )
    return list((await db.execute(query)).scalars())


def _unassigned(
    receipt: FiscalDocument, others: Iterable[FiscalDocumentRelation]
) -> Decimal | None:
    if receipt.payment_amount is None:
        return None
    return receipt.payment_amount - sum((r.amount for r in others), Decimal(0))


async def _pay_invoice(
    db: AsyncSession,
    receipt: FiscalDocument,
    invoice: FiscalDocument,
    amount: Decimal | None,
    others: Sequence[FiscalDocumentRelation],
) -> None:
    """Relate `invoice` to `receipt` for `amount`, or for the default when it is None."""
    if not _payable(receipt, invoice):
        raise _unprocessable(_RECEIPT_REFUSED)
    if any(r.relation == invoice.fiscal_document_id for r in others):
        raise _error(status.HTTP_409_CONFLICT, 'The invoice is already on this receipt')
    _check_payment_date(receipt, [invoice])
    number, balance = await fds.invoice_position(
        db, invoice.fiscal_document_id, exclude_document=receipt.fiscal_document_id
    )
    unassigned = _unassigned(receipt, others)
    amount = default_amount(balance, unassigned) if amount is None else amount
    _check_amount(amount, balance, unassigned)
    db.add(
        FiscalDocumentRelation(
            document=receipt.fiscal_document_id,
            relation=invoice.fiscal_document_id,
            exchange_rate=Decimal(1),
            installment=number,
            previous_balance=balance,
            amount=amount,
        )
    )


async def create_payment_receipt(
    db: AsyncSession, current: CurrentUser, data: PaymentReceiptCreate
) -> int:
    """A receipt draft by hand, or from a customer payment with its invoices proposed.

    FR-001, FR-002 and research R7.
    """
    document, _customer = await _new_document(
        db,
        current,
        type_=FiscalDocumentType.PAYMENT_RECEIPT,
        customer_id=data.customer,
        recipient_rfc=data.recipient,
        issuer_rfc=data.issuer,
        batch=data.batch,
    )
    document.usage = RECEIPT_USAGE
    document.payment_method = PaymentMethod.CASH
    payment = None
    if data.customer_payment is not None:
        payment = await db.get(CustomerPayment, data.customer_payment)
        if payment is None:
            raise _error(status.HTTP_404_NOT_FOUND, 'Customer payment not found')
        if payment.customer != data.customer:
            raise _unprocessable('The payment belongs to another customer')
        backed = (
            await db.execute(
                select(FiscalDocument.batch, FiscalDocument.serial).where(
                    FiscalDocument.customer_payment == payment.customer_payment_id,
                    FiscalDocument.type == FiscalDocumentType.PAYMENT_RECEIPT,
                    FiscalDocument.cancelled.is_(False),
                )
            )
        ).first()
        if backed is not None:
            folio = f'{backed.batch or ""}{backed.serial}' if backed.serial else 'draft'
            raise _error(status.HTTP_409_CONFLICT, f'This payment already has receipt {folio}')
        document.customer_payment = payment.customer_payment_id
        document.payment_date = payment.date.date()
        document.payment_amount = payment.amount.quantize(Decimal('0.01'))
        if 1 <= payment.method <= 31:
            document.payment_method = payment.method
        document.payment_reference = (payment.reference or '')[:50] or None
    db.add(document)
    await db.flush()
    if payment is not None:
        await _propose_paid_invoices(db, document, payment.customer_payment_id)
    await db.commit()
    return document.fiscal_document_id


async def _propose_paid_invoices(db: AsyncSession, receipt: FiscalDocument, payment: int) -> None:
    """The PPD invoices holding lines of the orders `payment` was applied to (research R7)."""
    invoices = (
        await db.execute(
            select(FiscalDocument)
            .join(
                FiscalDocumentDetail,
                FiscalDocumentDetail.document == FiscalDocument.fiscal_document_id,
            )
            .join(
                SalesOrderDetail,
                SalesOrderDetail.sales_order_detail_id == FiscalDocumentDetail.order_detail,
            )
            .join(SalesOrderPayment, SalesOrderPayment.sales_order == SalesOrderDetail.sales_order)
            .where(
                SalesOrderPayment.customer_payment == payment,
                SalesOrderPayment.cancelled.is_(False),
            )
            .distinct()
            .order_by(FiscalDocument.issued, FiscalDocument.fiscal_document_id)
        )
    ).scalars()
    others: list[FiscalDocumentRelation] = []
    for invoice in invoices:
        if not _payable(receipt, invoice):
            continue
        if receipt.payment_date and invoice.issued and receipt.payment_date < invoice.issued.date():
            continue
        _number, balance = await fds.invoice_position(
            db, invoice.fiscal_document_id, exclude_document=receipt.fiscal_document_id
        )
        if default_amount(balance, _unassigned(receipt, others)) <= 0:
            continue
        await _pay_invoice(db, receipt, invoice, None, others)
        await db.flush()
        others = await _paid_relations(db, receipt.fiscal_document_id)


async def add_paid_invoice(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, data: PaidInvoiceAdd
) -> None:
    _require_draft(document)
    _require_type(document, FiscalDocumentType.PAYMENT_RECEIPT)
    invoice = await db.get(FiscalDocument, data.invoice)
    if invoice is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Invoice not found')
    others = await _paid_relations(db, document.fiscal_document_id)
    await _pay_invoice(db, document, invoice, data.amount, others)
    _touch(document, current)
    await db.commit()


async def _paid_relation_or_404(
    db: AsyncSession, document: FiscalDocument, relation_id: int
) -> FiscalDocumentRelation:
    relation = await db.get(FiscalDocumentRelation, relation_id)
    if relation is None or relation.document != document.fiscal_document_id:
        raise _error(status.HTTP_404_NOT_FOUND, 'Paid invoice not found')
    return relation


async def update_paid_invoice(
    db: AsyncSession,
    current: CurrentUser,
    document: FiscalDocument,
    relation_id: int,
    data: PaidInvoiceUpdate,
) -> None:
    _require_draft(document)
    _require_type(document, FiscalDocumentType.PAYMENT_RECEIPT)
    relation = await _paid_relation_or_404(db, document, relation_id)
    others = [
        r for r in await _paid_relations(db, document.fiscal_document_id) if r is not relation
    ]
    number, balance = await fds.invoice_position(
        db, relation.relation, exclude_document=document.fiscal_document_id
    )
    _check_amount(data.amount, balance, _unassigned(document, others))
    relation.amount, relation.installment, relation.previous_balance = data.amount, number, balance
    _touch(document, current)
    await db.commit()


async def remove_paid_invoice(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, relation_id: int
) -> None:
    _require_draft(document)
    _require_type(document, FiscalDocumentType.PAYMENT_RECEIPT)
    await db.delete(await _paid_relation_or_404(db, document, relation_id))
    _touch(document, current)
    await db.commit()


async def _check_receipt_header(db: AsyncSession, document: FiscalDocument) -> None:
    """After a header edit: the amounts already paid and the invoices' dates still fit."""
    relations = await _paid_relations(db, document.fiscal_document_id)
    if not relations:
        return
    unassigned = _unassigned(document, relations)
    if unassigned is not None and unassigned < 0:
        raise _unprocessable(_OVER_PAYMENT)
    invoices = [await db.get(FiscalDocument, r.relation) for r in relations]
    _check_payment_date(document, invoices)


async def _prepare_receipt(db: AsyncSession, document: FiscalDocument) -> SealBuilder:
    """Lock the paid invoices, recompute their installments and balances, and check the sums."""
    relations = await _paid_relations(db, document.fiscal_document_id)
    if not relations:
        raise _unprocessable('The receipt pays no invoice')
    if document.payment_date is None or document.payment_amount is None:
        raise _unprocessable('The receipt needs its payment date and amount')
    if document.payment_method == PaymentMethod.TO_BE_DEFINED:
        raise _unprocessable('A payment receipt needs the actual payment form')
    if document.payment_date > local_now().date():
        raise _unprocessable(_PAYMENT_DATE)
    ids = sorted(r.relation for r in relations)
    invoices = {
        i.fiscal_document_id: i
        for i in (
            await db.execute(
                select(FiscalDocument)
                .where(FiscalDocument.fiscal_document_id.in_(ids))
                .order_by(FiscalDocument.fiscal_document_id)
                .with_for_update()
            )
        ).scalars()
    }
    _check_payment_date(document, invoices.values())
    paid: list[cfdi_payment.PaidInvoiceData] = []
    for relation in relations:
        invoice = invoices[relation.relation]
        if not _payable(document, invoice):
            raise _unprocessable(_RECEIPT_REFUSED)
        number, balance = await fds.invoice_position(
            db, invoice.fiscal_document_id, exclude_document=document.fiscal_document_id
        )
        _check_amount(relation.amount, balance, None)
        relation.installment, relation.previous_balance = number, balance
        xml = await fds.get_xml(db, invoice.fiscal_document_id)
        paid.append(
            cfdi_payment.PaidInvoiceData(
                uuid=invoice.stamp_uuid,
                batch=invoice.batch,
                serial=invoice.serial,
                installment=number,
                previous_balance=balance,
                amount=relation.amount,
                transfers=cfdi_payment.invoice_transfers(xml) if xml else [],
            )
        )
    if sum((r.amount for r in relations), Decimal(0)) != document.payment_amount:
        raise _unprocessable('The amounts paid must equal the payment amount')

    def build(certificate: TaxpayerCertificate) -> Awaitable[str]:
        data = cfdi_payment.PaymentData(
            batch=document.batch,
            serial=document.serial,
            issued=document.issued,
            issued_location=document.issued_location,
            issuer=document.issuer,
            issuer_name=document.issuer_name,
            issuer_regime=document.issuer_regime,
            recipient=document.recipient,
            recipient_name=document.recipient_name,
            recipient_postal_code=document.taxpayer_postal_code,
            recipient_regime=document.taxpayer_regime,
            payment_date=document.payment_date,
            payment_form=document.payment_method,
            amount=document.payment_amount,
            operation_number=document.payment_reference,
            ordering_bank=document.reference,
            paid=paid,
        )
        return cfdi_payment.seal_async(
            data,
            certificate_number=certificate.taxpayer_certificate_id,
            certificate_data=certificate.certificate_data,
            key_data=certificate.key_data,
            key_password=certificate.key_password,
        )

    return build


# ── Credit notes (#55, spec 028; research R4, R8) ─────────────────────────────

#: cfdlib's enum number for SAT relation type 01, as `fiscal_document_relation.type` stores it.
_CREDIT_NOTE_RELATION = '1'


def _creditable(invoice: FiscalDocument) -> bool:
    return (
        invoice.type == FiscalDocumentType.INVOICE
        and invoice.completed
        and not invoice.cancelled
        and bool(invoice.stamp_uuid)
    )


async def _credited_invoice(db: AsyncSession, document: FiscalDocument) -> FiscalDocument:
    """The one invoice a credit note relates (FR-008)."""
    relation = (
        await db.execute(
            select(FiscalDocumentRelation.relation).where(
                FiscalDocumentRelation.document == document.fiscal_document_id
            )
        )
    ).scalar_one()
    return await db.get(FiscalDocument, relation)


async def create_credit_note(db: AsyncSession, current: CurrentUser, data: CreditNoteCreate) -> int:
    """A credit note draft for one stamped invoice, copying its parties, form and usage."""
    invoice = await db.get(FiscalDocument, data.invoice)
    if invoice is None:
        raise _error(status.HTTP_404_NOT_FOUND, 'Invoice not found')
    if not _creditable(invoice):
        raise _unprocessable('The invoice cannot be credited')
    facility = await db.get(Facility, invoice.facility)
    batch = await default_batch_for(db, invoice.issuer, facility, FiscalDocumentType.CREDIT_NOTE)

    now = local_now()
    document = FiscalDocument(
        creation_time=now,
        modification_time=now,
        creator=current.employee_id,
        updater=current.employee_id,
        type=FiscalDocumentType.CREDIT_NOTE,
        batch=batch,
        completed=False,
        cancelled=False,
        payment_terms=PaymentTerms.IMMEDIATE,
        version=Decimal(0),
        provider=0,
        retention_rate=Decimal(0),
        local_retention_rate=Decimal(0),
        exchange_rate=Decimal(1),
        currency=CurrencyCode.MXN,
        **{
            column: getattr(invoice, column)
            for column in (
                'issuer',
                'issuer_name',
                'issuer_regime',
                'issuer_regime_name',
                'customer',
                'recipient',
                'recipient_name',
                'taxpayer_regime',
                'taxpayer_postal_code',
                'facility',
                'issued_at',
                'issued_location',
                'payment_method',
                'usage',
            )
        },
    )
    db.add(document)
    await db.flush()
    db.add(
        FiscalDocumentRelation(
            document=document.fiscal_document_id,
            relation=invoice.fiscal_document_id,
            type=_CREDIT_NOTE_RELATION,
            exchange_rate=Decimal(1),
            installment=1,
            previous_balance=Decimal(0),
            amount=Decimal(0),
        )
    )
    await db.commit()
    return document.fiscal_document_id


async def _check_credited_quantity(
    db: AsyncSession, document: FiscalDocument, product: int, quantity: Decimal
) -> None:
    """A credit note line may not exceed what its invoice billed of that product, per line."""
    invoice = await _credited_invoice(db, document)
    billed = [
        line.quantity
        for line in await _lines(db, invoice.fiscal_document_id)
        if line.product == product
    ]
    if billed and quantity > max(billed):
        raise _unprocessable(f"Quantity exceeds the invoice line's {max(billed):f}")


async def add_invoice_line(
    db: AsyncSession, current: CurrentUser, document: FiscalDocument, data: InvoiceLineCopy
) -> None:
    """Copy a line of the credit note's invoice, at its quantity or a smaller one (FR-009)."""
    _require_draft(document)
    _require_type(document, FiscalDocumentType.CREDIT_NOTE)
    invoice = await _credited_invoice(db, document)
    source = await db.get(FiscalDocumentDetail, data.line)
    if source is None or source.document != invoice.fiscal_document_id:
        raise _error(status.HTTP_404_NOT_FOUND, 'Invoice line not found')
    quantity = data.quantity if data.quantity is not None else source.quantity
    if quantity > source.quantity:
        raise _unprocessable(f"Quantity exceeds the invoice line's {source.quantity:f}")
    db.add(
        FiscalDocumentDetail(
            document=document.fiscal_document_id,
            quantity=quantity,
            **{
                column: getattr(source, column)
                for column in (
                    'product',
                    'order_detail',
                    'product_service',
                    'product_code',
                    'product_name',
                    'unit_of_measurement',
                    'unit_of_measurement_name',
                    'price',
                    'discount',
                    'tax_rate',
                    'exchange_rate',
                    'currency',
                    'tax_included',
                    'comment',
                )
            },
        )
    )
    _touch(document, current)
    await db.commit()


async def _prepare_credit_note(db: AsyncSession, document: FiscalDocument) -> SealBuilder:
    """Check the lines and the cap against the invoice, locked, then build an egreso (R4)."""
    lines = await _lines(db, document.fiscal_document_id)
    if not lines:
        raise _unprocessable('The invoice has no lines')
    for number, line in enumerate(lines, start=1):
        if not line.product_service or not line.unit_of_measurement:
            raise _unprocessable(f'Line {number}: missing SAT product key or unit')
    if not document.taxpayer_regime or not document.taxpayer_postal_code:
        raise _unprocessable('The recipient has no tax regime or postal code')
    if document.payment_method == PaymentMethod.TO_BE_DEFINED:
        raise _unprocessable('A credit note needs the actual payment form')
    relation = (
        await db.execute(
            select(FiscalDocumentRelation.relation).where(
                FiscalDocumentRelation.document == document.fiscal_document_id
            )
        )
    ).scalar_one()
    invoice = (
        await db.execute(
            select(FiscalDocument)
            .where(FiscalDocument.fiscal_document_id == relation)
            .with_for_update()
        )
    ).scalar_one()
    if not _creditable(invoice):
        raise _unprocessable('The invoice cannot be credited')

    data = replace(_invoice_data(document, lines, '01', [invoice.stamp_uuid]), voucher_type='E')
    total = sum((cfdi_invoice.line_amounts(line).total for line in data.lines), Decimal(0))
    # Receipts do not limit a credit: only the invoice's total and its other credit notes do.
    invoice_total, _receipts, _paid, credited = await fds.invoice_settlements(
        db, invoice.fiscal_document_id, exclude_document=document.fiscal_document_id
    )
    creditable = invoice_total - credited
    if total > creditable:
        folio = f'{invoice.batch or ""}{invoice.serial}'
        raise _unprocessable(
            f'The credit note exceeds what remains of invoice {folio}: {creditable:.2f}'
        )

    def build(certificate: TaxpayerCertificate) -> Awaitable[str]:
        return cfdi_invoice.seal_async(
            replace(data, batch=document.batch, serial=document.serial, issued=document.issued),
            certificate_number=certificate.taxpayer_certificate_id,
            certificate_data=certificate.certificate_data,
            key_data=certificate.key_data,
            key_password=certificate.key_password,
        )

    return build
