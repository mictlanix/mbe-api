"""Fiscal documents, read only: list, detail and the stored XML (#230, spec 020)."""

import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal import FiscalDocument, FiscalDocumentDetail, FiscalDocumentXml

DocumentStatus = Literal['draft', 'issued', 'cancelled']

_BATCH_SERIAL = re.compile(r'([A-Za-z]+)\s*(\d+)')
_UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE)


def document_status(document: FiscalDocument) -> DocumentStatus:
    """Legacy's own rule: `completed` decides whether a document was issued (research R10)."""
    if document.cancelled:
        return 'cancelled'
    return 'issued' if document.completed else 'draft'


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


async def get_document(db: AsyncSession, fiscal_document_id: int) -> FiscalDocument | None:
    """The document with `lines`, `status` and `total` attached."""
    document = await db.get(FiscalDocument, fiscal_document_id)
    if document is None:
        return None
    lines = await db.execute(
        select(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.document == fiscal_document_id)
        .order_by(FiscalDocumentDetail.fiscal_document_detail_id)
    )
    document.lines = list(lines.scalars())
    document.status = document_status(document)
    document.total = xml_total(await get_xml(db, fiscal_document_id))
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
        document.status = document_status(document)
        document.total = xml_total(xmls.get(document.fiscal_document_id))
    return items, total
