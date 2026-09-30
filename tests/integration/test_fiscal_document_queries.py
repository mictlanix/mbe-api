"""Fiscal document queries against real tables (#230, spec 020, research R10)."""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal import FiscalDocumentXml
from app.services import fiscal_document_service as svc
from tests.integration.seed import seed_fiscal_document


async def _ids(db: AsyncSession, **filters: object) -> list[int]:
    items, _total = await svc.list_documents(db, **filters)
    return [d.fiscal_document_id for d in items]


@pytest.mark.usefixtures('seeded')
async def test_list_is_newest_first_with_status_and_total(db: AsyncSession) -> None:
    invoice = await seed_fiscal_document(db, 'invoice')
    credit = await seed_fiscal_document(db, 'credit_note')
    draft = await seed_fiscal_document(db, 'invoice', completed=False, serial=None, stamp=False)

    items, total = await svc.list_documents(db)

    assert total == 3
    assert [d.fiscal_document_id for d in items] == [draft, credit, invoice]
    by_id = {d.fiscal_document_id: d for d in items}
    assert by_id[invoice].status == 'issued'
    assert by_id[invoice].total == Decimal('3160.00')
    assert by_id[credit].total == Decimal('47379.96')
    assert by_id[draft].status == 'draft'
    assert by_id[draft].total is None


@pytest.mark.usefixtures('seeded')
async def test_list_reads_the_lowercase_total_of_a_cfdi_32(db: AsyncSession) -> None:
    old = await seed_fiscal_document(
        db,
        'invoice',
        version=Decimal('3.2'),
        xml='<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" total="116.00"/>',
    )

    items, _ = await svc.list_documents(db)

    assert items[0].fiscal_document_id == old
    assert items[0].total == Decimal('116.00')


@pytest.mark.usefixtures('seeded')
async def test_list_search(db: AsyncSession) -> None:
    invoice = await seed_fiscal_document(db, 'invoice')  # RH 11405
    credit = await seed_fiscal_document(db, 'credit_note')  # RHC 6

    assert await _ids(db, search='RH11405') == [invoice]
    assert await _ids(db, search='rh 11405') == [invoice]
    assert await _ids(db, search='6') == [credit]
    assert await _ids(db, search='43cd16d8-ea9e-5adf-97b2-9846fc4ba8a4') == [credit]
    assert await _ids(db, search='XAÑ010101') == [invoice]
    assert await _ids(db, search='cliente demo') == [credit, invoice]


@pytest.mark.usefixtures('seeded')
async def test_list_filters(db: AsyncSession) -> None:
    invoice = await seed_fiscal_document(db, 'invoice', issued=datetime(2026, 9, 29, 18, 28))
    credit = await seed_fiscal_document(db, 'credit_note', issued=datetime(2026, 3, 2, 16, 12))
    cancelled = await seed_fiscal_document(
        db, 'advance', cancelled=True, issued=datetime(2026, 9, 28, 12, 2)
    )
    draft = await seed_fiscal_document(db, 'invoice', completed=False, stamp=False)

    assert await _ids(db, type=100) == [credit]
    assert await _ids(db, issuer='EKU9003173C9') == [draft, cancelled, credit, invoice]
    assert await _ids(db, issuer='NOPE') == []
    assert await _ids(db, status='issued') == [credit, invoice]
    assert await _ids(db, status='cancelled') == [cancelled]
    assert await _ids(db, status='draft') == [draft]
    assert await _ids(db, date_from=datetime(2026, 9, 1), date_to=datetime(2026, 9, 29, 0, 0)) == [
        cancelled
    ]


@pytest.mark.usefixtures('seeded')
async def test_list_pages(db: AsyncSession) -> None:
    ids = [await seed_fiscal_document(db, 'invoice') for _ in range(3)]

    items, total = await svc.list_documents(db, skip=1, limit=1)

    assert total == 3
    assert [d.fiscal_document_id for d in items] == [ids[1]]


@pytest.mark.usefixtures('seeded')
async def test_get_document_with_lines_in_order(db: AsyncSession) -> None:
    invoice = await seed_fiscal_document(db, 'invoice', comments=['uno', None, 'tres'])

    document = await svc.get_document(db, invoice)

    assert document.fiscal_document_id == invoice
    assert document.status == 'issued'
    assert [line.comment for line in document.lines] == ['uno', None, 'tres']
    assert await svc.get_document(db, 999999) is None


@pytest.mark.usefixtures('seeded')
async def test_get_xml(db: AsyncSession) -> None:
    invoice = await seed_fiscal_document(db, 'invoice')

    assert (await svc.get_xml(db, invoice)).startswith('<?xml')

    await db.execute(
        delete(FiscalDocumentXml).where(FiscalDocumentXml.fiscal_document_xml_id == invoice)
    )
    await db.commit()
    assert await svc.get_xml(db, invoice) is None
