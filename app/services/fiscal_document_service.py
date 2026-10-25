"""Fiscal documents, read only: list, detail and the stored XML (#230, spec 020)."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentRelation,
    FiscalDocumentXml,
)

DocumentStatus = Literal['draft', 'sealed', 'issued', 'cancelled']

_BATCH_SERIAL = re.compile(r'([A-Za-z]+)\s*(\d+)')
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)


def document_status(document: FiscalDocument, *, has_xml: bool = False) -> DocumentStatus:
    """Legacy's own rule: `completed` decides whether a document was issued (research R10).

    An uncompleted document with its XML stored has been sealed and waits to be stamped
    (spec 027, research R1); legacy never stores XML before completing a document.
    """
    if document.cancelled:
        return 'cancelled'
    if document.completed:
        return 'issued'
    return 'sealed' if has_xml else 'draft'


def classify_search(search: str) -> tuple[str, object]:
    text = search.strip()
    if match := _BATCH_SERIAL.fullmatch(text):
        return 'batch_serial', (match.group(1).upper(), int(match.group(2)))
    if text.isdigit():
        return 'serial', int(text)
    if _UUID.fullmatch(text):
        return 'uuid', text.upper()
    return 'text', text


def xml_total(data: str | None) -> Decimal | None:
    """The root's `Total`, or `total` in CFD 2.0/2.2 and CFDI 3.2 (research R10)."""
    if not data:
        return None
    try:
        root = ET.fromstring(data.encode('utf-8'))
        value = root.get('Total') or root.get('total')
        return Decimal(value) if value is not None else None
    except (ET.ParseError, InvalidOperation):
        return None


async def get_xml(db: AsyncSession, fiscal_document_id: int) -> str | None:
    query = select(FiscalDocumentXml.data).where(
        FiscalDocumentXml.fiscal_document_xml_id == fiscal_document_id
    )
    return (await db.execute(query)).scalar_one_or_none()


async def get_relations(db: AsyncSession, fiscal_document_id: int) -> list[dict[str, object]]:
    """The documents this one relates, with the UUID, series and folio each is known by."""
    related = aliased(FiscalDocument)
    rows = await db.execute(
        select(
            FiscalDocumentRelation.fiscal_document_relation_id,
            FiscalDocumentRelation.relation,
            FiscalDocumentRelation.type,
            related.stamp_uuid,
            related.batch,
            related.serial,
        )
        .join(related, related.fiscal_document_id == FiscalDocumentRelation.relation)
        .where(FiscalDocumentRelation.document == fiscal_document_id)
        .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
    )
    return [dict(row._mapping) for row in rows]


# ── Balances of a paid invoice (spec 028, research R2) ───────────────────────

#: `fiscal_document.type` of the documents that settle or reduce an invoice.
PAYMENT_RECEIPT = 200
CREDIT_NOTE = 100


def live(document=FiscalDocument):  # noqa: ANN001, ANN201 — an entity or an alias of it
    """Sealed or issued, and not cancelled: a document that counts (spec 027, research R1)."""
    # Its own alias, so a query that already reads `fiscal_document_xml` does not swallow it.
    xml = aliased(FiscalDocumentXml)
    return and_(
        document.cancelled.is_(False),
        or_(
            document.completed.is_(True),
            exists().where(xml.fiscal_document_xml_id == document.fiscal_document_id),
        ),
    )


async def invoice_position(
    db: AsyncSession, invoice: int, *, exclude_document: int | None = None
) -> tuple[int, Decimal]:
    """The next installment of `invoice` and its balance before it.

    The balance is the invoice's stored total, less what live payment receipts paid of it and
    less the totals of its live credit notes. `exclude_document` leaves one receipt or credit
    note out, the one being edited or confirmed.
    """
    total = xml_total(await get_xml(db, invoice)) or Decimal(0)
    receipt = aliased(FiscalDocument)
    paid = await db.execute(
        select(func.count(), func.coalesce(func.sum(FiscalDocumentRelation.amount), 0))
        .join(receipt, receipt.fiscal_document_id == FiscalDocumentRelation.document)
        .where(
            FiscalDocumentRelation.relation == invoice,
            receipt.type == PAYMENT_RECEIPT,
            live(receipt),
            receipt.fiscal_document_id != (exclude_document or 0),
        )
    )
    earlier, paid_amount = paid.one()
    note = aliased(FiscalDocument)
    notes = await db.execute(
        select(FiscalDocumentXml.data)
        .join(note, note.fiscal_document_id == FiscalDocumentXml.fiscal_document_xml_id)
        .join(FiscalDocumentRelation, FiscalDocumentRelation.document == note.fiscal_document_id)
        .where(
            FiscalDocumentRelation.relation == invoice,
            note.type == CREDIT_NOTE,
            live(note),
            note.fiscal_document_id != (exclude_document or 0),
        )
    )
    credited = sum((xml_total(data) or Decimal(0) for data in notes.scalars()), Decimal(0))
    return earlier + 1, total - Decimal(paid_amount) - credited


async def get_paid_invoices(
    db: AsyncSession, document: FiscalDocument, *, live_values: bool
) -> list[dict[str, object]]:
    """A payment receipt's paid invoices. Stored values once sealed; recomputed on a draft."""
    invoice = aliased(FiscalDocument)
    rows = await db.execute(
        select(FiscalDocumentRelation, invoice.batch, invoice.serial, invoice.stamp_uuid)
        .join(invoice, invoice.fiscal_document_id == FiscalDocumentRelation.relation)
        .where(FiscalDocumentRelation.document == document.fiscal_document_id)
        .order_by(FiscalDocumentRelation.fiscal_document_relation_id)
    )
    paid = []
    for relation, batch, serial, stamp_uuid in rows.all():
        installment, balance = relation.installment, relation.previous_balance
        if live_values:
            installment, balance = await invoice_position(
                db, relation.relation, exclude_document=document.fiscal_document_id
            )
        paid.append(
            {
                'fiscal_document_relation_id': relation.fiscal_document_relation_id,
                'invoice': relation.relation,
                'batch': batch,
                'serial': serial,
                'stamp_uuid': stamp_uuid,
                'installment': installment,
                'previous_balance': balance,
                'amount': relation.amount,
                'remaining': balance - relation.amount,
            }
        )
    return paid


async def get_document(db: AsyncSession, fiscal_document_id: int) -> FiscalDocument | None:
    """The document with `lines`, `relations`, `status` and `total` attached, and a payment
    receipt's `paid_invoices`."""
    document = await db.get(FiscalDocument, fiscal_document_id)
    if document is None:
        return None
    lines = await db.execute(
        select(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.document == fiscal_document_id)
        .order_by(FiscalDocumentDetail.fiscal_document_detail_id)
    )
    document.lines = list(lines.scalars())
    document.relations = await get_relations(db, fiscal_document_id)
    xml = await get_xml(db, fiscal_document_id)
    document.status = document_status(document, has_xml=xml is not None)
    document.total = xml_total(xml)
    document.paid_invoices = (
        await get_paid_invoices(db, document, live_values=document.status == 'draft')
        if document.type == PAYMENT_RECEIPT
        else []
    )
    return document


async def list_documents(
    db: AsyncSession,
    *,
    search: str | None = None,
    issuer: str | None = None,
    type: int | None = None,  # noqa: A002 — the query parameter's name
    status: DocumentStatus | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[FiscalDocument], int]:
    """Newest first. No facility scope: fiscal documents belong to an issuer (research R10)."""
    base = select(FiscalDocument)
    count_q = select(func.count()).select_from(FiscalDocument)

    def both(clause):  # noqa: ANN001, ANN202 — local helper, mirrors existing services
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    if issuer is not None:
        both(FiscalDocument.issuer == issuer)
    if type is not None:
        both(FiscalDocument.type == type)
    if status == 'cancelled':
        both(FiscalDocument.cancelled.is_(True))
    elif status is not None:
        both(FiscalDocument.cancelled.is_(False))
        both(FiscalDocument.completed.is_(status == 'issued'))
        if status != 'issued':
            sealed = exists().where(
                FiscalDocumentXml.fiscal_document_xml_id == FiscalDocument.fiscal_document_id
            )
            both(sealed if status == 'sealed' else ~sealed)
    if date_from is not None:
        both(FiscalDocument.issued >= date_from)
    if date_to is not None:
        both(FiscalDocument.issued <= date_to)
    if search and search.strip():
        kind, value = classify_search(search)
        if kind == 'batch_serial':
            batch, serial = value
            both(func.upper(FiscalDocument.batch) == batch)
            both(FiscalDocument.serial == serial)
        elif kind == 'serial':
            both(FiscalDocument.serial == value)
        elif kind == 'uuid':
            both(func.upper(FiscalDocument.stamp_uuid) == value)
        else:
            like = f'%{value}%'
            both(
                or_(FiscalDocument.recipient.ilike(like), FiscalDocument.recipient_name.ilike(like))
            )

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(FiscalDocument.fiscal_document_id.desc()).offset(skip).limit(limit)
    items = list((await db.execute(page)).scalars())

    # The only totals are in the XML, so read them for this page's rows alone.
    xmls: dict[int, str] = {}
    if items:
        rows = await db.execute(
            select(FiscalDocumentXml.fiscal_document_xml_id, FiscalDocumentXml.data).where(
                FiscalDocumentXml.fiscal_document_xml_id.in_([d.fiscal_document_id for d in items])
            )
        )
        xmls = dict(rows.all())
    for document in items:
        document.status = document_status(document, has_xml=document.fiscal_document_id in xmls)
        document.total = xml_total(xmls.get(document.fiscal_document_id))
    return items, total
