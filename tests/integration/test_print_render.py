"""Rendered PDFs: page geometry of both layouts, from bundled assets only (#230)."""

import asyncio
import io
import logging
import re
import socket
import statistics
import tempfile
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import AsyncClient
from jinja2 import ChoiceLoader, DictLoader
from PIL import Image
from pypdf import PageObject, PdfReader
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.config import settings
from app.enums import CashCountType, CurrencyCode, PaymentMethod, PaymentTerms, PaymentType
from app.models.core import Address, CashCount, CashSession, Expense, Facility
from app.models.fiscal import TaxpayerBatch
from app.models.purchases import ExpenseVoucher, ExpenseVoucherDetail
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.rendering import render_pdf
from app.services import cfdi, print_contexts, sales_order_service
from tests.integration.seed import (
    CFDI_FIXTURES,
    ISSUER_RFC,
    POSTAL_CODE,
    seed_fiscal_document,
    seed_sales_order,
)

_HEADER = {
    'name': 'Sucursal Centro',
    'taxpayer_name': 'Mictlanix SA de CV',
    'rfc': 'MIC010101AAA',
    'address': ['Av. Juárez 100, Centro,', '68000 Oaxaca de Juárez, Oaxaca'],
    'logo': None,
    'title': 'Prueba',
    'skip_address': False,
}

_LINES = (
    '{% for i in range(n) %}<p>renglon{{ i }}</p>{% endfor %}'
    '{% if word is defined %}<p>{{ word }}</p>{% endif %}'
)


@pytest.fixture
def children(monkeypatch: pytest.MonkeyPatch) -> None:
    """Minimal children of each layout, served ahead of the real templates."""
    loader = ChoiceLoader(
        [
            DictLoader(
                {
                    '_t.html': '{% extends "_ticket_layout.html" %}{% block main %}'
                    + _LINES
                    + '{% endblock %}',
                    '_l.html': '{% extends "_print_layout.html" %}{% block main %}'
                    + _LINES
                    + '{% endblock %}',
                }
            ),
            rendering.environment.loader,
        ]
    )
    monkeypatch.setattr(rendering.environment, 'loader', loader)


async def _pages(template: str, n: int) -> list:
    pdf = await render_pdf(template, {'header': _HEADER, 'n': n})
    return PdfReader(io.BytesIO(pdf)).pages


@pytest.mark.usefixtures('children')
async def test_layouts_geometry() -> None:
    short = await _pages('_t.html', 1)
    long = await _pages('_t.html', 200)

    for pages in (short, long):
        assert len(pages) == 1
        assert float(pages[0].mediabox.width) == pytest.approx(204.09, abs=0.1)
    short_height = float(short[0].mediabox.height)
    assert short_height < 283.5
    assert float(long[0].mediabox.height) > short_height
    assert 'renglon199' in long[0].extract_text()

    for n in (1, 200):
        pages = await _pages('_l.html', n)
        assert (n == 1) == (len(pages) == 1)
        for page in pages:
            assert (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)



def _font_families(pages: list) -> set[str]:
    return {
        str(font.get_object()['/BaseFont']).split('+')[-1]
        for page in pages
        for font in (page['/Resources'].get('/Font') or {}).values()
    }


@pytest.mark.usefixtures('children')
async def test_a_soft_hyphen_break_uses_a_bundled_glyph(monkeypatch: pytest.MonkeyPatch) -> None:
    """A soft hyphen (U+00AD, present in real product names) that breaks a line makes WeasyPrint
    insert a hyphen glyph. Its default, U+2010, is not in Open Sans, so a host fallback font was
    embedded (found rendering mbe_dev order 173432). Both stylesheets use the ASCII hyphen."""
    word = '\u00ad'.join(['impermeabilizante'] * 12)
    for template in ('_t.html', '_l.html'):
        pdf = await render_pdf(template, {'header': _HEADER, 'n': 0, 'word': word})
        pages = PdfReader(io.BytesIO(pdf)).pages
        assert all(name.startswith(('Open-Sans', 'Roboto')) for name in _font_families(pages))


# ── Sale ticket (US1) ─────────────────────────────────────────────────────────

_NOW = datetime(2026, 8, 1, 10, 0)


async def _set(db: AsyncSession, model: type, key: object, **values: object) -> None:
    await db.execute(update(model).where(model.__mapper__.primary_key[0] == key).values(**values))
    await db.commit()


async def _cash_session(db: AsyncSession) -> int:
    session = CashSession(start=_NOW, cashier=1, cash_drawer=1)
    db.add(session)
    await db.flush()
    return session.cash_session_id


async def _pay(
    db: AsyncSession,
    order_id: int,
    *,
    amount: str,
    applied: str,
    change: str = '0',
    method: PaymentMethod = PaymentMethod.CASH,
    payment_type: PaymentType = PaymentType.IMMEDIATE,
    cash_session: int | None = None,
) -> int:
    payment = CustomerPayment(
        amount=Decimal(amount),
        method=method,
        date=_NOW,
        cash_session=cash_session,
        customer=1,
        facility=1,
        serial=1,
        creator=1,
        updater=1,
        creation_time=_NOW,
        modification_time=_NOW,
        currency=CurrencyCode.MXN,
        payment_type=payment_type,
    )
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order_id,
            customer_payment=payment.customer_payment_id,
            amount=Decimal(applied),
            amount_change=Decimal(change),
            applier=1,
            date=_NOW,
            cancelled=False,
        )
    )
    await db.commit()
    return payment.customer_payment_id


async def _credit_note(db: AsyncSession, order_id: int) -> None:
    """A completed refund of one unit, settled as store credit."""
    refund = CustomerRefund(
        sales_order=order_id,
        customer=1,
        creator=1,
        updater=1,
        sales_person=1,
        creation_time=_NOW,
        modification_time=_NOW,
        completed=True,
        cancelled=False,
        facility=1,
        serial=1,
        date=_NOW,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
    )
    db.add(refund)
    await db.flush()
    line = (
        await db.execute(select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order_id))
    ).scalar_one()
    db.add(
        CustomerRefundDetail(
            customer_refund=refund.customer_refund_id,
            sales_order_detail=line.sales_order_detail_id,
            quantity=Decimal('1'),
            product=1,
            price=Decimal('100'),
            product_code='P1',
            product_name='Producto Uno',
            tax_rate=Decimal('0.16'),
            discount=Decimal('0'),
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    payment = CustomerPayment(
        amount=Decimal('116'),
        method=PaymentMethod.NA,
        date=_NOW,
        customer=1,
        facility=1,
        serial=2,
        creator=1,
        updater=1,
        creation_time=_NOW,
        modification_time=_NOW,
        currency=CurrencyCode.MXN,
        payment_type=PaymentType.CREDIT_NOTE,
    )
    db.add(payment)
    await db.flush()
    db.add(
        CreditNote(
            sales_order=order_id,
            customer_refund=refund.customer_refund_id,
            customer_payment=payment.customer_payment_id,
            customer=1,
            refunded=Decimal('116'),
            date=_NOW,
        )
    )
    await db.commit()


async def _ticket(client: AsyncClient, order_id: int) -> PageObject:
    response = await client.get(f'/api/v1/sales-orders/{order_id}/ticket')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert len(pages) == 1
    assert float(pages[0].mediabox.width) == pytest.approx(204.09, abs=0.1)
    return pages[0]


@pytest.mark.usefixtures('seeded')
async def test_sale_ticket_renders_a_draft(client: AsyncClient, db: AsyncSession) -> None:
    order_id = await seed_sales_order(db)

    text = (await _ticket(client, order_id)).extract_text()

    assert 'Punto de Venta' in text
    assert f'{order_id:08d}' in text
    assert 'Producto Uno' in text
    assert '$1,160.00' in text
    assert 'Este recibo no es un comprobante de pago.' in ' '.join(text.split())
    assert re.search(r'Folio\s*Fecha', text), text


@pytest.mark.usefixtures('seeded')
async def test_sale_ticket_renders_the_receipt_with_change(
    client: AsyncClient, db: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger='weasyprint')
    order_id = await seed_sales_order(db, completed=True, paid=True)
    session = await _cash_session(db)
    await _pay(db, order_id, amount='1200', applied='1160', change='40', cash_session=session)

    text = (await _ticket(client, order_id)).extract_text()

    assert 'Ticket de Venta' in text
    assert 'Efectivo' in text
    assert 'Cambio' in text
    assert '$40.00' in text
    assert 'Pagado' in text
    # Legacy's "Pagado" shows a check mark (`true.png`), not an amount: nothing follows the label.
    assert not re.search(r'Pagado\s*\$', text), text
    # ...and the check mark itself loaded: a missing or refused image logs a WeasyPrint warning.
    assert [r.getMessage() for r in caplog.records if r.name.startswith('weasyprint')] == []
    assert 'Contraentrega' not in text


Arrange = Callable[[AsyncSession, int], Awaitable[None]]


async def _discount(db: AsyncSession, order_id: int) -> None:
    await db.execute(
        update(SalesOrderDetail)
        .where(SalesOrderDetail.sales_order == order_id)
        .values(discount_rate=Decimal('0.1'))
    )
    await db.commit()


async def _on_delivery(db: AsyncSession, order_id: int) -> None:
    await _pay(db, order_id, amount='1160', applied='1160')


async def _card(db: AsyncSession, order_id: int) -> None:
    session = await _cash_session(db)
    await _pay(
        db,
        order_id,
        amount='1160',
        applied='1160',
        method=PaymentMethod.CREDIT_CARD,
        cash_session=session,
    )


async def _credit(db: AsyncSession, order_id: int) -> None:
    await _set(db, SalesOrder, order_id, payment_terms=PaymentTerms.NET_D)


async def _message(db: AsyncSession, _order_id: int) -> None:
    await _set(db, Facility, 1, receipt_message='Gracias por su preferencia')


async def _nothing(db: AsyncSession, _order_id: int) -> None:
    pass


@pytest.mark.usefixtures('seeded')
@pytest.mark.parametrize(
    ('arrange', 'paid', 'expected'),
    [
        (_discount, True, ['Descuento']),
        (_credit_note, True, ['Nota de Crédito', 'Devolución']),
        (_on_delivery, True, ['Contraentrega']),
        (_nothing, False, ['Por Cobrar']),
        (_card, True, ['Cobro con tarjeta de crédito o débito', 'Acepto']),
        (_credit, False, ['PAGARÉ', 'Acepto']),
        (_message, True, ['Gracias por su preferencia']),
    ],
)
async def test_sale_ticket_renders_each_receipt_block(
    client: AsyncClient, db: AsyncSession, arrange: Arrange, paid: bool, expected: list[str]
) -> None:
    order_id = await seed_sales_order(db, completed=True, paid=paid)
    await arrange(db, order_id)

    text = (await _ticket(client, order_id)).extract_text()

    assert 'Ticket de Venta' in text
    for fragment in expected:
        assert fragment in text


@pytest.mark.usefixtures('seeded')
async def test_sale_ticket_cancellation_stamp_is_not_clipped(
    client: AsyncClient, db: AsyncSession
) -> None:
    order_id = await seed_sales_order(db, completed=True, paid=True)
    await _set(db, SalesOrder, order_id, cancelled=True)

    page = await _ticket(client, order_id)
    fragments: list[tuple[str, float, float]] = []

    def visit(text: str, cm: list, tm: list, _font: object, _size: object) -> None:
        if text.strip():
            x, y = tm[4], tm[5]
            fragments.append((text, cm[0] * x + cm[2] * y + cm[4], cm[1] * x + cm[3] * y + cm[5]))

    text = page.extract_text(visitor_text=visit)

    assert 'Cancelado' in text
    box = page.mediabox
    assert fragments
    # Each fragment's origin: its baseline start, which is the leftmost point of the rotated word.
    for fragment, x, y in fragments:
        assert float(box.left) <= x <= float(box.right), (fragment, x, float(box.right))
        assert float(box.bottom) <= y <= float(box.top), (fragment, y, float(box.top))


# ── Cash cut (US2) ────────────────────────────────────────────────────────────


async def seed_closed_session(db: AsyncSession) -> int:
    """The Independent Test of US2, by hand:

    - starting cash 500;
    - sales: cash 1200 tendered on a 1160 order, 40 change → Efectivo 1160; card 300;
    - refunds: an mbe-api cash payout of −100, and a store-credit note of +116 (N/A, no cash);
    - one completed expense voucher of 50 + 30 = 80;
    - counted 1000 + 2×200 + 50 = 1450.

    In drawer 500 + 1160 − 80 − 100 = 1480, so the cut reports Faltante $30.00.
    """
    session = await _cash_session(db)
    order_id = await seed_sales_order(db, completed=True, paid=True)
    await _pay(db, order_id, amount='1200', applied='1160', change='40', cash_session=session)
    await _pay(
        db,
        order_id,
        amount='300',
        applied='0',
        method=PaymentMethod.CREDIT_CARD,
        cash_session=session,
    )
    await _pay(db, order_id, amount='-100', applied='0', cash_session=session)
    await _pay(
        db,
        order_id,
        amount='116',
        applied='0',
        method=PaymentMethod.NA,
        payment_type=PaymentType.CREDIT_NOTE,
        cash_session=session,
    )
    db.add(Expense(expense_id=1, expense='Papelería'))
    voucher = ExpenseVoucher(
        creator=1,
        updater=1,
        facility=1,
        cash_session=session,
        date=_NOW,
        creation_time=_NOW,
        modification_time=_NOW,
        completed=True,
        cancelled=False,
    )
    db.add(voucher)
    await db.flush()
    db.add_all(
        [
            ExpenseVoucherDetail(
                expense_voucher=voucher.expense_voucher_id, expense=1, amount=Decimal(amount)
            )
            for amount in ('50', '30')
        ]
    )
    db.add_all(
        [
            CashCount(session=session, denomination=Decimal(d), quantity=q, type=t)
            for d, q, t in (
                ('500', 1, CashCountType.STARTING_CASH),
                ('1000', 1, CashCountType.COUNTED_CASH),
                ('200', 2, CashCountType.COUNTED_CASH),
                ('50', 1, CashCountType.COUNTED_CASH),
            )
        ]
    )
    await db.commit()
    await _set(db, CashSession, session, end=datetime(2026, 8, 1, 20, 0))
    return session


@pytest.mark.usefixtures('seeded')
async def test_cash_cut_renders(client: AsyncClient, db: AsyncSession) -> None:
    session = await seed_closed_session(db)

    response = await client.get(f'/api/v1/cash-sessions/{session}/ticket')

    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert len(pages) == 1
    assert float(pages[0].mediabox.width) == pytest.approx(204.09, abs=0.1)
    text = ' '.join(pages[0].extract_text().split())
    assert 'Corte de Caja' in text
    assert 'Cajón 1' in text
    assert f'{session:06d}' in text
    assert re.search(r'Ventas en Efectivo :? ?\$1,160\.00', text), text
    assert re.search(r'Efectivo Final :? ?\$1,450\.00', text), text
    assert re.search(r'Faltante :? ?\$30\.00', text), text
    assert 'Sobrante' not in text
    assert 'Efectivo Contado' not in text
    assert 'Av. Reforma' not in text
    assert ISSUER_RFC not in text


# ── Sales order document (US3) ────────────────────────────────────────────────


async def seed_document_order(db: AsyncSession) -> int:
    """A credit order with a contact, a ship-to, a comment and a discounted, commented line.

    10 × 100 + 16% = 1160.00, plus 3 × 116 tax-included less 10% = 313.20: total 1473.20.
    """
    order_id = await seed_sales_order(db)
    db.add(
        Address(
            address_id=2,
            type=1,
            street='Calle 5',
            exterior_number='12',
            postal_code=POSTAL_CODE,
            neighborhood='Del Valle',
            borough='Benito Juárez',
            state='CDMX',
            country='México',
            comment='Portón negro',
        )
    )
    db.add(
        SalesOrderDetail(
            sales_order=order_id,
            product=1,
            quantity=Decimal('3'),
            cost=Decimal('50'),
            price=Decimal('116'),
            discount_rate=Decimal('0.1'),
            tax_rate=Decimal('0.16'),
            product_code='P2',
            product_name='Cemento gris',
            warehouse=1,
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=True,
            comment='bulto de 50 kg',
        )
    )
    await db.commit()
    await _set(
        db,
        SalesOrder,
        order_id,
        contact=1,
        ship_to=2,
        comment='Llamar antes de entregar',
        payment_terms=PaymentTerms.NET_D,
        due_date=datetime(2026, 8, 31),
    )
    return order_id


async def _document(client: AsyncClient, order_id: int) -> str:
    response = await client.get(f'/api/v1/sales-orders/{order_id}/document')
    assert response.status_code == 200
    assert response.headers['content-type'] == 'application/pdf'
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert pages
    for page in pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)
    return ' '.join(' '.join(page.extract_text() for page in pages).split())


@pytest.mark.usefixtures('seeded')
async def test_sales_order_document_renders(client: AsyncClient, db: AsyncSession) -> None:
    order_id = await seed_document_order(db)

    text = await _document(client, order_id)

    for fragment in (
        f'PEDIDO {order_id:08d}',  # `uppercase` in the stylesheet, as in legacy
        'Cliente Uno',
        'Juan Pérez',
        'Calle 5 12',
        'Llamar antes de entregar',
        'P1',
        'Producto Uno',
        'P2',
        'Cemento gris',
        'bulto de 50 kg',
        'Fecha de Vencimiento',
        'Descuento',
        '$1,473.20',
        'MIL CUATROCIENTOS SETENTA Y TRES PESOS 20/100 M. N.',
        '¡Usted ahorró $30.00 en esta compra!',
    ):
        assert fragment in text, fragment


@pytest.mark.usefixtures('seeded')
@pytest.mark.parametrize('cancelled', [False, True])
async def test_sales_order_document_renders_in_any_state(
    client: AsyncClient, db: AsyncSession, cancelled: bool
) -> None:
    order_id = await seed_sales_order(db)
    await _set(db, SalesOrder, order_id, cancelled=cancelled)

    text = await _document(client, order_id)

    assert f'PEDIDO {order_id:08d}' in text
    assert 'MIL CIENTO SESENTA PESOS 00/100 M. N.' in text


@pytest.mark.usefixtures('seeded')
async def test_sales_order_document_paginates_on_letter(
    client: AsyncClient, db: AsyncSession
) -> None:
    order_id = await seed_sales_order(db)
    line = (
        await db.execute(select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order_id))
    ).scalar_one()
    db.add_all(
        [
            SalesOrderDetail(
                **{
                    column.key: getattr(line, column.key)
                    for column in SalesOrderDetail.__table__.columns
                    if column.key != 'sales_order_detail_id'
                }
                | {'product_code': f'X{i:03d}'}
            )
            for i in range(60)
        ]
    )
    await db.commit()

    response = await client.get(f'/api/v1/sales-orders/{order_id}/document')

    pages = PdfReader(io.BytesIO(response.content)).pages
    assert len(pages) > 1
    for page in pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)
    assert 'X059' in pages[-1].extract_text()


# ── CFDI (spec 020) ───────────────────────────────────────────────────────────

_LEGEND = 'Este documento es una representación impresa de un CFDI.'


async def _cfdi_pages(client: AsyncClient, document_id: int) -> list[str]:
    """Each page's text, with whitespace collapsed."""
    response = await client.get(f'/api/v1/fiscal-documents/{document_id}/pdf')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    pages = PdfReader(io.BytesIO(response.content)).pages
    assert pages
    for page in pages:
        assert (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)
    return [' '.join(page.extract_text().split()) for page in pages]


def _squeezed(text: str) -> str:
    """Seals and the original string wrap anywhere, so compare them without whitespace."""
    return ''.join(text.split())


@pytest.mark.usefixtures('seeded')
async def test_cfdi_invoice_renders(client: AsyncClient, db: AsyncSession) -> None:
    doc = cfdi.parse((CFDI_FIXTURES / 'invoice.xml').read_text(encoding='utf-8'))
    document_id = await seed_fiscal_document(db, 'invoice')

    pages = await _cfdi_pages(client, document_id)

    text = ' '.join(pages)
    for expected in (
        'Factura',
        'RH 011405',
        doc.timbre['UUID'],
        doc.comprobante['NoCertificado'],
        doc.timbre['NoCertificadoSAT'],
        'XAÑ010101AB1',
        'EMPRESA DEMO',
        'Gastos en general',
        *(c['Descripcion'] for c in doc.conceptos),
        *(c['ClaveProdServ'] for c in doc.conceptos),
        'Subtotal',
        '$2,896.54',
        'Descuento',
        '$172.41',
        'IVA',
        '$435.87',
        '$3,160.00',
        'TRES MIL CIENTO SESENTA PESOS 00/100 M. N.',
        'Fecha de Certificación',
        'Página 1 de 1',
        _LEGEND,
    ):
        assert expected in text, expected
    squeezed = _squeezed(text)
    assert _squeezed(cfdi.tfd_original_string(doc.timbre)) in squeezed
    assert doc.comprobante['Sello'] in squeezed
    assert doc.timbre['SelloSAT'] in squeezed
    assert 'IVA Retenido' not in text
    assert 'CANCELADO' not in text


@pytest.mark.usefixtures('seeded')
async def test_cfdi_invoice_shows_retentions(client: AsyncClient, db: AsyncSession) -> None:
    document_id = await seed_fiscal_document(db, 'invoice_retention')

    text = ' '.join(await _cfdi_pages(client, document_id))

    assert 'IVA Retenido' in text
    assert '$278.57' in text


def _many_concepts(n: int) -> str:
    xml = (CFDI_FIXTURES / 'invoice.xml').read_text(encoding='utf-8')
    start = xml.index('<cfdi:Concepto ')
    end = xml.index('</cfdi:Concepto>', start) + len('</cfdi:Concepto>')
    return xml[:start] + xml[start:end] * n + xml[end:]


@pytest.mark.usefixtures('seeded')
async def test_cfdi_footer_repeats_on_every_page(client: AsyncClient, db: AsyncSession) -> None:
    document_id = await seed_fiscal_document(db, 'invoice', xml=_many_concepts(80))

    pages = await _cfdi_pages(client, document_id)

    assert len(pages) >= 2
    for number, page in enumerate(pages, 1):
        assert _LEGEND in page
        assert f'Página {number} de {len(pages)}' in page


@pytest.mark.usefixtures('seeded')
async def test_cfdi_cancelled_is_marked_on_every_page(
    client: AsyncClient, db: AsyncSession
) -> None:
    document_id = await seed_fiscal_document(
        db, 'invoice', cancelled=True, xml=_many_concepts(80)
    )

    pages = await _cfdi_pages(client, document_id)

    assert len(pages) >= 2
    for page in pages:
        assert 'CANCELADO' in page
        assert '2026-09-29' in page


@pytest.mark.usefixtures('seeded')
@pytest.mark.parametrize(
    ('fixture', 'title', 'label', 'related'),
    [
        (
            'credit_note',
            'Nota de Crédito',
            '01 : Nota de Crédito de los Documentos Relacionados',
            '5691CD2C-45B6-5A26-97EF-E6209C062EFB',
        ),
        (
            'advance',
            'Aplicación de Anticipos',
            '07 : CFDI por Aplicación de Anticipo',
            'E68D8C69-5706-5E5B-B544-C789F78DF2D8',
        ),
    ],
)
async def test_cfdi_related_documents_render(
    client: AsyncClient, db: AsyncSession, fixture: str, title: str, label: str, related: str
) -> None:
    document_id = await seed_fiscal_document(db, fixture)

    text = ' '.join(await _cfdi_pages(client, document_id))

    for expected in (title, 'CFDI Relacionados', 'Tipo de Relación', label, related):
        assert expected in text, expected


@pytest.mark.usefixtures('seeded')
async def test_cfdi_payment_receipt_renders(client: AsyncClient, db: AsyncSession) -> None:
    doc = cfdi.parse((CFDI_FIXTURES / 'payment.xml').read_text(encoding='utf-8'))
    document_id = await seed_fiscal_document(db, 'payment')

    text = ' '.join(await _cfdi_pages(client, document_id))

    for expected in (
        'Recibo Electrónico de Pago',
        'Fecha del Pago',
        '2026-08-19',
        'Saldo Anterior',
        'Importe Pagado',
        'Saldo Insoluto',
        'Importe Total',
        '$269,206.00',
        'DOSCIENTOS SESENTA Y NUEVE MIL DOSCIENTOS SEIS PESOS 00/100 M. N.',
        *(d['IdDocumento'] for d in doc.pagos[0]['documentos']),
    ):
        assert expected in text, expected
    assert 'CFDI Relacionados' not in text


# ── Non-functional guarantees (US5) ───────────────────────────────────────────


@pytest.fixture
def network_calls(monkeypatch: pytest.MonkeyPatch) -> Callable[[], list[str]]:
    """Arms a socket block on demand and returns the calls it caught (FR-003)."""
    calls: list[str] = []

    def arm() -> list[str]:
        def refuse(name: str):  # noqa: ANN202
            def blocked(*args: object, **kwargs: object) -> None:
                calls.append(name)
                raise AssertionError('network access during render')

            return blocked

        monkeypatch.setattr(socket.socket, 'connect', refuse('connect'))
        monkeypatch.setattr(socket, 'getaddrinfo', refuse('getaddrinfo'))
        return calls

    return arm


def _xobjects(resources: object) -> list:
    """Every XObject reachable from `resources`, through nested form XObjects."""
    found = []
    xobjects = resources.get('/XObject', {}) if resources else {}
    for ref in xobjects.values():
        xobject = ref.get_object()
        found.append(xobject)
        if xobject.get('/Subtype') == '/Form':
            found += _xobjects(xobject.get('/Resources'))
    return found


def _pdf_facts(pdf: bytes) -> tuple[set[str], bool]:
    """(the embedded font names, whether any raster image is drawn)."""
    fonts: set[str] = set()
    image = False
    for page in PdfReader(io.BytesIO(pdf)).pages:
        resources = page['/Resources']
        for ref in resources.get('/Font', {}).values():
            fonts.add(str(ref.get_object()['/BaseFont']))
        image |= any(x.get('/Subtype') == '/Image' for x in _xobjects(resources))
    return fonts, image


def _files(root: Path) -> set[Path]:
    return set(root.rglob('*')) if root.exists() else set()


@pytest.mark.usefixtures('seeded')
async def test_zero_network_and_deterministic(
    client: AsyncClient,
    db: AsyncSession,
    network_calls: Callable[[], list[str]],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    order_id = await seed_sales_order(db, completed=True, paid=True)
    session = await _cash_session(db)
    await _pay(db, order_id, amount='1200', applied='1160', change='40', cash_session=session)
    cut = await seed_closed_session(db)
    document = await seed_document_order(db)
    # The CFDI's logo is its batch's, not the facility's (spec 020, research R5).
    invoice = await seed_fiscal_document(db, 'invoice', template="{'Logo': 'logo.png'}")
    urls = [
        f'/api/v1/sales-orders/{order_id}/ticket',
        f'/api/v1/cash-sessions/{cut}/ticket',
        f'/api/v1/sales-orders/{document}/document',
        f'/api/v1/fiscal-documents/{invoice}/pdf',
    ]
    images = Path(settings.images_dir)
    images.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (60, 30), 'red').save(images / 'logo.png')  # the seeded facility's `logo`
    images_before = _files(images)
    # A temp dir of this test's own: the system one is shared with every process on the host, so
    # asserting it unchanged would fail whenever anything else creates or removes a file there.
    private_temp = tmp_path / 'temp'
    private_temp.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(private_temp))
    caplog.set_level(logging.WARNING, logger='weasyprint')

    async def render_all() -> list[bytes]:
        pdfs = []
        for url in urls:
            response = await client.get(url)
            assert response.status_code == 200, url
            pdfs.append(response.content)
        return pdfs

    first = await render_all()
    # The one write a render makes: the shared `FontConfiguration` copies the bundled @font-face
    # fonts into its own `weasyprint-*` folder on the first render in the process, reuses it after
    # and removes it at exit. It is a font cache, not the document. Only that exact folder may
    # appear, and only once; every later render must create nothing at all.
    # The cache may already exist from an earlier test in this process, outside `private_temp`,
    # so its own contents are checked too.
    font_cache = Path(rendering._FONT_CONFIG._folder)
    assert {p.name for p in private_temp.iterdir()} <= {font_cache.name}
    temp_before = _files(private_temp)
    cache_before = _files(font_cache)
    calls = network_calls()
    second = await render_all()

    assert second == first
    for pdf in first:
        fonts, image = _pdf_facts(pdf)
        # Subset names read like `/ABCDEF+Open-Sans-Bold`. Only the bundled families: a glyph they
        # lack would pull in a host font, and the document would then depend on the host.
        families = {name.split('+', 1)[-1].replace('-', '') for name in fonts}
        assert any(f.startswith('OpenSans') for f in families), fonts
        assert any(f.startswith('Roboto') for f in families), fonts
        assert all(f.startswith(('OpenSans', 'Roboto')) for f in families), fonts
        assert image, 'the logo is not drawn'

    await _set(db, Facility, 1, logo='missing.png')
    await db.execute(update(TaxpayerBatch).values(template="{'Logo': 'missing.png'}"))
    await db.commit()
    for pdf in await render_all():
        assert _pdf_facts(pdf)[1] is False

    assert calls == []
    assert [r for r in caplog.records if r.name.startswith('weasyprint')] == []
    assert _files(images) == images_before
    assert _files(private_temp) == temp_before
    assert _files(font_cache) == cache_before


def _p95(samples: list[float]) -> float:
    return statistics.quantiles(samples, n=20)[18]


async def _health_latencies(client: AsyncClient, n: int = 50) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        response = await client.get('/api/v1/health')
        samples.append(time.perf_counter() - start)
        assert response.status_code == 200
    return samples


@pytest.mark.usefixtures('seeded')
async def test_renders_do_not_block_event_loop(client: AsyncClient, db: AsyncSession) -> None:
    """SC-005: ten renders in flight add under 100 ms to the p95 of an unrelated request."""
    order_id = await seed_sales_order(db, completed=True, paid=True)
    await _pay(db, order_id, amount='1200', applied='1160', change='40')
    order = await sales_order_service.get_order(db, order_id)
    await sales_order_service.attach_derived(db, order)
    template, context = await print_contexts.sale_ticket_context(db, order)
    assert template == 'sale_receipt.html'

    idle = await _health_latencies(client)
    renders = asyncio.ensure_future(
        asyncio.gather(*(render_pdf(template, context) for _ in range(10)))
    )
    await asyncio.sleep(0)
    loaded = await _health_latencies(client)
    in_flight = not renders.done()
    pdfs = await renders

    print(
        f'\nhealth idle p50={statistics.median(idle) * 1000:.2f}ms p95={_p95(idle) * 1000:.2f}ms'
        f' | loaded p50={statistics.median(loaded) * 1000:.2f}ms'
        f' p95={_p95(loaded) * 1000:.2f}ms | renders still running after: {in_flight}'
    )
    assert all(pdf.startswith(b'%PDF') for pdf in pdfs)
    assert _p95(loaded) - _p95(idle) < 0.100
