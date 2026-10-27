"""Fiscal credit notes on real tables: create, lines, cap and confirm (spec 028, US2)."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from lxml import etree
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import EntityStatus
from app.models.customer import TaxpayerRecipient, customer_taxpayer
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    TaxpayerBatch,
    TaxpayerCertificate,
)
from app.models.product import Product
from app.models.sales import SalesOrder, SalesOrderDetail
from app.schemas.fiscal import CreditNoteCreate, InvoiceLineCopy, InvoiceLineUpdate, InvoiceUpdate
from app.services import fiscal_document_service as fds
from app.services import invoice_service as svc
from tests.cfdi_schema import assert_valid_cfdi40
from tests.integration.seed import seed_fiscal_document, seed_sales_order
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

ISSUER = 'EKU9003173C9'
RECIPIENT = 'XAÑ010101AB1'
_NS = {'cfdi': 'http://www.sat.gob.mx/cfd/4'}
pytestmark = pytest.mark.usefixtures('seeded')


class CreditNotes:
    def __init__(self, db: AsyncSession, current: CurrentUser) -> None:
        self.db = db
        self.current = current

    async def load(self, document: int | FiscalDocument) -> FiscalDocument:
        if isinstance(document, FiscalDocument):
            document = inspect(document).identity[0]
        self.db.expire_all()
        loaded = await fds.get_document(self.db, document)
        assert loaded is not None
        return loaded

    async def create(self, invoice: int) -> FiscalDocument:
        data = CreditNoteCreate(invoice=invoice)
        return await self.load(await svc.create_credit_note(self.db, self.current, data))

    async def copy(self, document: FiscalDocument, line: int, **body: object) -> FiscalDocument:
        document = await self.load(document)
        await svc.add_invoice_line(
            self.db,
            self.current,
            document,
            InvoiceLineCopy.model_validate({'line': line, **body}),
        )
        return await self.load(document)

    async def confirm(self, document: FiscalDocument) -> FiscalDocument:
        document = await self.load(document)
        await svc.confirm(self.db, self.current, document)
        return await self.load(document)


@pytest.fixture
async def notes(db: AsyncSession, current_user: CurrentUser) -> CreditNotes:
    """Fixture invoice RH 11405's issuer with a credit-note series and a valid CSD."""
    certificate_data, key_data = _make_csd(rfc=ISSUER)
    await seed_fiscal_document(db, 'invoice')  # the issuer and catalogs
    await db.execute(update(FiscalDocument).values(cancelled=True))
    await db.execute(update(Product).where(Product.product_id == 1).values(key='01010101'))
    db.add_all(
        [
            TaxpayerBatch(taxpayer=ISSUER, batch='NC', type=100, template='{}'),
            TaxpayerCertificate(
                taxpayer_certificate_id=_CERTIFICATE_NUMBER,
                taxpayer=ISSUER,
                certificate_data=certificate_data,
                key_data=key_data,
                key_password=_PASSWORD,
                valid_from=datetime(2024, 1, 1),
                valid_to=datetime(2027, 12, 31),
                status=EntityStatus.ACTIVE,
            ),
            TaxpayerRecipient(
                taxpayer_recipient_id=RECIPIENT,
                name='CLIENTE DEMO',
                email='c@example.com',
                postal_code='06000',
                regime='601',
            ),
        ]
    )
    await db.flush()
    await db.execute(insert(customer_taxpayer).values(customer=1, taxpayer=RECIPIENT))
    await db.commit()
    return CreditNotes(db, current_user)


async def _invoice(db: AsyncSession, **overrides: object) -> int:
    """Stamped invoice RH 11405: three lines, total 3,160.00, paid by transfer (03), usage G03."""
    return await seed_fiscal_document(db, 'invoice', **overrides)


async def _lines(db: AsyncSession, invoice: int) -> list[SimpleNamespace]:
    """Snapshots: the helpers expire the session, and an expired row would lazy-load."""
    rows = await db.execute(
        select(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.document == invoice)
        .order_by(FiscalDocumentDetail.fiscal_document_detail_id)
    )
    columns = FiscalDocumentDetail.__table__.columns.keys()
    return [SimpleNamespace(**{c: getattr(row, c) for c in columns}) for row in rows.scalars()]


async def _status(code: int, call) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == code, caught.value.detail
    return caught.value


# ── Create ────────────────────────────────────────────────────────────────────


async def test_create_copies_the_invoice(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db, usage='G03', payment_method=3)

    document = await notes.create(invoice)

    assert (document.type, document.batch, document.status) == (100, 'NC', 'draft')
    assert (document.issuer, document.customer, document.recipient) == (ISSUER, 1, RECIPIENT)
    assert (document.taxpayer_regime, document.taxpayer_postal_code) == ('601', '06000')
    assert (document.payment_terms, document.payment_method, document.usage) == (0, 3, 'G03')
    [relation] = document.relations
    assert (relation['relation'], relation['type']) == (invoice, '1')


@pytest.mark.parametrize(
    'overrides',
    [{'stamp': False, 'completed': False}, {'cancelled': True}, {'type': 200}],
)
async def test_only_a_stamped_live_invoice_can_be_credited(
    notes: CreditNotes, overrides: dict
) -> None:
    invoice = await _invoice(notes.db, **overrides)

    error = await _status(422, notes.create(invoice))
    assert error.detail == 'The invoice cannot be credited'


async def test_create_needs_a_credit_note_series(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    await notes.db.execute(update(TaxpayerBatch).where(TaxpayerBatch.type == 100).values(type=1))
    await notes.db.commit()

    error = await _status(422, notes.create(invoice))
    assert error.detail == 'The issuer has no credit note series'


# ── Lines ─────────────────────────────────────────────────────────────────────


async def test_a_copied_line_keeps_the_invoice_lines_values(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    source = (await _lines(notes.db, invoice))[1]

    document = await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)

    [line] = document.lines
    copied = ('product', 'product_service', 'product_code', 'product_name', 'unit_of_measurement')
    for name in (*copied, 'price', 'discount', 'tax_rate', 'tax_included', 'quantity'):
        assert getattr(line, name) == getattr(source, name), name


async def test_a_copied_line_is_capped_at_the_invoice_lines_quantity(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    source = (await _lines(notes.db, invoice))[1]  # 35 units
    document = await notes.create(invoice)

    error = await _status(
        422, notes.copy(document, source.fiscal_document_detail_id, quantity='35.0001')
    )
    assert error.detail == "Quantity exceeds the invoice line's 35.0000"
    document = await notes.copy(document, source.fiscal_document_detail_id, quantity='10')
    line = document.lines[0].fiscal_document_detail_id
    await _status(
        422,
        svc.update_line(
            # Every fixture line bills product 1; its largest line is 65.
            notes.db,
            notes.current,
            document,
            line,
            InvoiceLineUpdate(quantity=Decimal('66')),
        ),
    )


async def test_a_line_of_another_invoice_is_not_found(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    other = await _invoice(notes.db)
    foreign = (await _lines(notes.db, other))[0]

    await _status(404, notes.copy(await notes.create(invoice), foreign.fiscal_document_detail_id))


async def test_a_product_can_be_added_by_hand(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    document = await notes.create(invoice)

    await svc.add_manual_line(notes.db, notes.current, document, 1)

    assert [line.product for line in (await notes.load(document)).lines] == [1]


# ── Confirm ───────────────────────────────────────────────────────────────────


async def test_confirm_seals_an_egreso_related_with_01(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    source = (await _lines(notes.db, invoice))[0]
    document = await notes.copy(
        await notes.create(invoice), source.fiscal_document_detail_id, quantity='1'
    )

    document = await notes.confirm(document)

    assert (document.status, document.batch, document.serial) == ('sealed', 'NC', 1)
    xml = await fds.get_xml(notes.db, document.fiscal_document_id)
    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    assert (root.get('TipoDeComprobante'), root.get('MetodoPago'), root.get('FormaPago')) == (
        'E',
        'PUE',
        '03',
    )
    related = root.find('cfdi:CfdiRelacionados', _NS)
    assert related.get('TipoRelacion') == '01'
    stamp = (await notes.db.get(FiscalDocument, invoice)).stamp_uuid
    assert [r.get('UUID') for r in related] == [stamp]


async def test_confirm_refuses_more_than_the_invoice_still_holds(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    # The seeded lines carry no discount: 360.00 + 1,050.00 + 1,950.00 against a 3,160.00 total.
    lines = await _lines(notes.db, invoice)
    first = await notes.create(invoice)
    for line in lines[:2]:
        first = await notes.copy(first, line.fiscal_document_detail_id)
    await notes.confirm(first)

    second = await notes.copy(await notes.create(invoice), lines[2].fiscal_document_detail_id)

    error = await _status(422, notes.confirm(second))
    assert error.detail.startswith('The credit note exceeds what remains of invoice RH11405: ')


async def test_confirm_needs_the_actual_payment_form(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db, payment_method=99)
    source = (await _lines(notes.db, invoice))[0]
    document = await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)

    error = await _status(422, notes.confirm(document))
    assert error.detail == 'A credit note needs the actual payment form'
    await svc.update_header(
        notes.db, notes.current, await notes.load(document), InvoiceUpdate(payment_method=3)
    )
    assert (await notes.confirm(document)).status == 'sealed'


async def test_a_credit_note_can_be_reopened(notes: CreditNotes) -> None:
    invoice = await _invoice(notes.db)
    source = (await _lines(notes.db, invoice))[0]
    sealed = await notes.confirm(
        await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)
    )

    await svc.reopen(notes.db, notes.current, sealed)

    assert (await notes.load(sealed)).status == 'draft'


async def test_credit_note_lines_do_not_count_as_invoiced(notes: CreditNotes) -> None:
    """A copied line keeps its order line; only invoices take units of it (spec 027, FR-006)."""
    order = await seed_sales_order(notes.db, completed=True)  # one line of 10
    order_line_id = (
        await notes.db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == order
            )
        )
    ).scalar_one()
    invoice = await _invoice(notes.db)
    source = (await _lines(notes.db, invoice))[0]
    await notes.db.execute(
        update(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.fiscal_document_detail_id == source.fiscal_document_detail_id)
        .values(order_detail=order_line_id, quantity=Decimal(4))
    )
    await notes.db.commit()

    document = await notes.copy(await notes.create(invoice), source.fiscal_document_detail_id)
    await notes.confirm(document)

    order_line = await notes.db.get(SalesOrderDetail, order_line_id)
    remaining = await svc.remaining_by_order_line(notes.db, [order_line], exclude_document=0)
    assert remaining[order_line.sales_order_detail_id] == Decimal(6)


# ── Orders settled by a credit note (spec 033, FR-011) ──────────────────────────


async def _invoiced_orders(notes: CreditNotes) -> tuple[int, int, int, list[SimpleNamespace]]:
    """Two unpaid orders of 1,160.00 (ten at 100 plus 16%) on one invoice: its first line is the
    first order's, its second line the second order's, each at ten at 100."""
    db = notes.db
    first = await seed_sales_order(db, completed=True)
    second = await seed_sales_order(db, completed=True)
    order_lines = (
        (
            await db.execute(
                select(SalesOrderDetail.sales_order_detail_id)
                .where(SalesOrderDetail.sales_order.in_([first, second]))
                .order_by(SalesOrderDetail.sales_order)
            )
        )
        .scalars()
        .all()
    )
    invoice = await _invoice(db)
    lines = await _lines(db, invoice)
    for source, order_line in zip(lines[:2], order_lines, strict=True):
        line_id = FiscalDocumentDetail.fiscal_document_detail_id
        await db.execute(
            update(FiscalDocumentDetail)
            .where(line_id == source.fiscal_document_detail_id)
            .values(order_detail=order_line, quantity=Decimal(10), price=Decimal(100))
        )
    await db.commit()
    return first, second, invoice, await _lines(db, invoice)


async def _paid(db: AsyncSession, order: int) -> bool:
    db.expire_all()
    return (await db.get(SalesOrder, order)).paid


async def test_confirming_a_credit_note_settles_the_order_it_covers(notes: CreditNotes) -> None:
    first, second, invoice, lines = await _invoiced_orders(notes)

    await notes.confirm(
        await notes.copy(await notes.create(invoice), lines[0].fiscal_document_detail_id)
    )

    assert await _paid(notes.db, first) is True, 'its line, copied whole, covers 1,160.00'
    assert await _paid(notes.db, second) is False, 'nothing of the note is left for it'


async def test_reopening_the_credit_note_reopens_the_order(notes: CreditNotes) -> None:
    """Back to draft, the note stops counting, so the order owes again (FR-011)."""
    first, _second, invoice, lines = await _invoiced_orders(notes)
    sealed = await notes.confirm(
        await notes.copy(await notes.create(invoice), lines[0].fiscal_document_detail_id)
    )
    assert await _paid(notes.db, first) is True

    await svc.reopen(notes.db, notes.current, await notes.load(sealed))

    assert await _paid(notes.db, first) is False
