"""The invoice editor against real tables: create, lines, confirm and the serial (spec 027, US1)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from lxml import etree
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.enums import (
    CurrencyCode,
    EntityStatus,
    FacilityType,
    FiscalCertificationProvider,
    PaymentTerms,
)
from app.models.core import Facility
from app.models.customer import Customer, TaxpayerRecipient, customer_taxpayer
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentXml,
    TaxpayerBatch,
    TaxpayerCertificate,
    TaxpayerIssuer,
)
from app.models.product import PriceList, Product
from app.models.sales import CustomerRefund, CustomerRefundDetail, SalesOrder, SalesOrderDetail
from app.models.sat_catalog import SatCfdiUsage, SatPostalCode, SatTaxRegime
from app.schemas.fiscal import (
    FiscalDocumentResponse,
    InvoiceCreate,
    InvoiceLineUpdate,
    InvoiceRelationAdd,
    InvoiceUpdate,
)
from app.services import fiscal_document_service as fds
from app.services import invoice_service as svc
from tests.cfdi_schema import assert_valid_cfdi40
from tests.integration.seed import ISSUER_RFC, RFC, seed_sales_order
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

XAXX = 'XAXX010101000'
pytestmark = pytest.mark.usefixtures('seeded')


@dataclass
class Invoicing:
    db: AsyncSession
    current: CurrentUser

    async def load(self, document: int | FiscalDocument) -> FiscalDocument:
        """A fresh read, as each request makes. Takes an id or a (possibly expired) document."""
        if isinstance(document, FiscalDocument):
            document = inspect(document).identity[0]
        self.db.expire_all()
        loaded = await fds.get_document(self.db, document)
        assert loaded is not None
        return loaded

    async def create(self, **body: object) -> FiscalDocument:
        data = InvoiceCreate.model_validate({'customer': 1, 'recipient': RFC, **body})
        return await self.load(await svc.create_invoice(self.db, self.current, data))

    async def add_order(self, document: FiscalDocument, order: int) -> FiscalDocument:
        document = await self.load(document)
        await svc.add_sales_order(self.db, self.current, document, order)
        return await self.load(document)

    async def confirm(self, document: FiscalDocument) -> FiscalDocument:
        document = await self.load(document)
        await svc.confirm(self.db, self.current, document)
        return await self.load(document)


@pytest.fixture
async def invoicing(db: AsyncSession, current_user: CurrentUser) -> Invoicing:
    """The baseline issuer with an invoice series and a valid CSD, and a billable recipient."""
    certificate_data, key_data = _make_csd(rfc=ISSUER_RFC)
    db.add_all(
        [
            SatPostalCode(sat_postal_code_id='99999', state='X'),
            SatTaxRegime(sat_tax_regime_id='616', description='Sin obligaciones fiscales'),
            TaxpayerIssuer(
                taxpayer_issuer_id='EKU9003173C9',
                name='Otro',
                regime='601',
                provider=FiscalCertificationProvider.NONE,
            ),
        ]
    )
    await db.flush()
    db.add_all(
        [
            SatCfdiUsage(sat_cfdi_usage_id='G01', description='Adquisición de mercancías'),
            SatCfdiUsage(sat_cfdi_usage_id='G03', description='Gastos en general'),
            TaxpayerBatch(taxpayer=ISSUER_RFC, batch='A', type=0, template='{}'),
            TaxpayerBatch(taxpayer=ISSUER_RFC, batch='P', type=200, template='{}'),
            TaxpayerCertificate(
                taxpayer_certificate_id=_CERTIFICATE_NUMBER,
                taxpayer=ISSUER_RFC,
                certificate_data=certificate_data,
                key_data=key_data,
                key_password=_PASSWORD,
                valid_from=datetime(2024, 1, 1),
                valid_to=datetime(2027, 12, 31),
                status=EntityStatus.ACTIVE,
            ),
            TaxpayerRecipient(
                taxpayer_recipient_id=XAXX,
                name='PÚBLICO EN GENERAL',
                email='x@example.com',
                postal_code='99999',
                regime='616',
            ),
        ]
    )
    await db.execute(
        update(TaxpayerRecipient)
        .where(TaxpayerRecipient.taxpayer_recipient_id == RFC)
        .values(postal_code='06000', regime='601')
    )
    await db.execute(update(Product).where(Product.product_id == 1).values(key='01010101'))
    await db.execute(
        insert(customer_taxpayer).values(
            [{'customer': 1, 'taxpayer': RFC}, {'customer': 1, 'taxpayer': XAXX}]
        )
    )
    await db.commit()
    return Invoicing(db, current_user)


async def _status(code: int, call) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == code, caught.value.detail
    return caught.value


# ── Create (R7) ───────────────────────────────────────────────────────────────


async def test_create_applies_the_defaults(invoicing: Invoicing) -> None:
    document = await invoicing.create()

    assert document.status == 'draft'
    assert (document.issuer, document.batch, document.type) == (ISSUER_RFC, 'A', 0)
    assert document.issuer_name == 'Mictlanix SA de CV'
    assert (document.issuer_regime, document.issuer_regime_name) == (
        '601',
        'General de Ley Personas Morales',
    )
    assert (document.recipient, document.recipient_name) == (RFC, 'Acme')
    assert (document.taxpayer_regime, document.taxpayer_postal_code) == ('601', '06000')
    assert (document.facility, document.issued_at, document.issued_location) == (1, 1, '06000')
    # Customer 1 has 30 credit days and a 1,000 limit: legacy's HasCredit.
    assert document.payment_terms == PaymentTerms.NET_D
    assert (document.payment_method, document.usage) == (99, 'G01')
    assert (document.currency, document.exchange_rate) == (CurrencyCode.MXN, Decimal('1'))
    assert (document.serial, document.issued, document.completed, document.cancelled) == (
        None,
        None,
        False,
        False,
    )
    assert (document.creator, document.updater) == (1, 1)


async def test_create_without_credit_days_is_immediate(invoicing: Invoicing) -> None:
    await invoicing.db.execute(
        update(Customer).where(Customer.customer_id == 1).values(credit_days=0)
    )
    await invoicing.db.commit()

    assert (await invoicing.create()).payment_terms == PaymentTerms.IMMEDIATE


async def test_general_public_takes_the_stores_postal_code(invoicing: Invoicing) -> None:
    document = await invoicing.create(recipient=XAXX)

    assert (document.recipient_name, document.taxpayer_postal_code) == (
        'PÚBLICO EN GENERAL',
        '06000',
    )


async def test_create_refuses_a_recipient_not_linked_to_the_customer(invoicing: Invoicing) -> None:
    invoicing.db.add(TaxpayerRecipient(taxpayer_recipient_id='CCC010101CC1', name='C', email='c@x'))
    await invoicing.db.commit()

    await _status(422, invoicing.create(recipient='CCC010101CC1'))


async def test_create_refuses_an_issuer_without_invoice_series(invoicing: Invoicing) -> None:
    await invoicing.db.execute(
        update(TaxpayerBatch).where(TaxpayerBatch.type == 0).values(type=100)
    )
    await invoicing.db.commit()

    error = await _status(422, invoicing.create())
    assert error.detail == 'The issuer has no invoice series'


async def test_create_uses_the_stores_default_series_of_the_issuer(invoicing: Invoicing) -> None:
    invoicing.db.add(TaxpayerBatch(taxpayer=ISSUER_RFC, batch='B', type=0, template='{}'))
    await invoicing.db.execute(update(Facility).values(default_batch='B'))
    await invoicing.db.commit()

    assert (await invoicing.create()).batch == 'B'
    # A store default that is not an invoice series of the issuer is ignored.
    await invoicing.db.execute(update(Facility).values(default_batch='P'))
    await invoicing.db.commit()
    assert (await invoicing.create()).batch == 'A'


async def test_create_refuses_a_series_that_is_not_an_invoice_series(invoicing: Invoicing) -> None:
    await _status(422, invoicing.create(batch='P'))


# ── Lines from sales orders (FR-005, FR-006) ──────────────────────────────────


async def test_add_order_copies_its_invoiceable_lines(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    document = await invoicing.create()

    document = await invoicing.add_order(document, order)

    [line] = document.lines
    assert line.order_detail is not None
    assert (line.product, line.product_code, line.product_name) == (1, 'P1', 'Producto Uno')
    assert (line.quantity, line.price, line.discount) == (
        Decimal('10'),
        Decimal('100'),
        Decimal('0'),
    )
    assert (line.tax_rate, line.tax_included) == (Decimal('0.16'), False)
    assert (line.product_service, line.unit_of_measurement, line.unit_of_measurement_name) == (
        '01010101',
        'H87',
        'Pieza',
    )


async def test_adding_the_same_order_twice_does_not_duplicate(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    document = await invoicing.add_order(await invoicing.create(), order)

    document = await invoicing.add_order(document, order)

    assert len(document.lines) == 1


async def test_add_order_refuses_drafts_cancelled_and_missing_orders(invoicing: Invoicing) -> None:
    document = await invoicing.create()
    draft = await seed_sales_order(invoicing.db, completed=False)
    cancelled = await seed_sales_order(invoicing.db, completed=True)
    await invoicing.db.execute(
        update(SalesOrder).where(SalesOrder.sales_order_id == cancelled).values(cancelled=True)
    )
    await invoicing.db.commit()

    await _status(422, svc.add_sales_order(invoicing.db, invoicing.current, document, draft))
    await _status(422, svc.add_sales_order(invoicing.db, invoicing.current, document, cancelled))
    await _status(404, svc.add_sales_order(invoicing.db, invoicing.current, document, 999999))


async def test_add_order_skips_products_that_are_not_invoiceable(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    await invoicing.db.execute(update(Product).values(invoiceable=False))
    await invoicing.db.commit()
    document = await invoicing.create()

    error = await _status(
        422, svc.add_sales_order(invoicing.db, invoicing.current, document, order)
    )
    assert error.detail == 'No invoiceable lines were found'


async def test_sealed_invoices_reserve_their_quantity(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    first = await invoicing.add_order(await invoicing.create(), order)
    await svc.update_line(
        invoicing.db,
        invoicing.current,
        first,
        first.lines[0].fiscal_document_detail_id,
        InvoiceLineUpdate(quantity=Decimal('6')),
    )
    await invoicing.confirm(await invoicing.load(first))

    second = await invoicing.add_order(await invoicing.create(), order)

    assert [line.quantity for line in second.lines] == [Decimal('4')]


async def test_drafts_do_not_reserve(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    await invoicing.add_order(await invoicing.create(), order)

    second = await invoicing.add_order(await invoicing.create(), order)

    assert [line.quantity for line in second.lines] == [Decimal('10')]


async def test_a_refund_before_invoicing_reduces_the_quantity(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True, paid=True)
    [detail] = (
        await invoicing.db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == order
            )
        )
    ).scalars()
    when = datetime(2026, 8, 2, 10, 0)
    refund = CustomerRefund(
        sales_order=order,
        customer=1,
        creator=1,
        updater=1,
        sales_person=1,
        creation_time=when,
        modification_time=when,
        completed=True,
        cancelled=False,
        facility=1,
        serial=1,
        date=when,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
    )
    invoicing.db.add(refund)
    await invoicing.db.flush()
    invoicing.db.add(
        CustomerRefundDetail(
            customer_refund=refund.customer_refund_id,
            sales_order_detail=detail,
            quantity=Decimal('3'),
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
    await invoicing.db.commit()

    document = await invoicing.add_order(await invoicing.create(), order)

    assert [line.quantity for line in document.lines] == [Decimal('7')]


# ── Lines: edit and remove (FR-011) ───────────────────────────────────────────


async def test_an_order_line_cannot_exceed_its_remaining_quantity(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    document = await invoicing.add_order(await invoicing.create(), order)
    line = document.lines[0].fiscal_document_detail_id

    await _status(
        422,
        svc.update_line(
            invoicing.db,
            invoicing.current,
            document,
            line,
            InvoiceLineUpdate(quantity=Decimal('11')),
        ),
    )
    await svc.update_line(
        invoicing.db,
        invoicing.current,
        document,
        line,
        InvoiceLineUpdate(price=Decimal('80'), discount=Decimal('0.1'), comment='nota'),
    )
    document = await invoicing.load(document)
    assert (document.lines[0].price, document.lines[0].discount, document.lines[0].comment) == (
        Decimal('80'),
        Decimal('0.1'),
        'nota',
    )


async def test_remove_a_line(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    document = await invoicing.add_order(await invoicing.create(), order)

    await svc.remove_line(
        invoicing.db, invoicing.current, document, document.lines[0].fiscal_document_detail_id
    )

    assert (await invoicing.load(document)).lines == []
    await _status(404, svc.remove_line(invoicing.db, invoicing.current, document, 999999))


# ── Confirm (FR-013, FR-014) ──────────────────────────────────────────────────


async def _ready(invoicing: Invoicing) -> FiscalDocument:
    order = await seed_sales_order(invoicing.db, completed=True)
    return await invoicing.add_order(await invoicing.create(), order)


async def test_confirm_seals_with_the_next_serial(invoicing: Invoicing) -> None:
    # A legacy invoice already holds serial 41 in series A; another issuer's 99 does not count.
    for issuer, serial in ((ISSUER_RFC, 41), ('EKU9003173C9', 99)):
        invoicing.db.add(_legacy_invoice(issuer=issuer, serial=serial))
    await invoicing.db.commit()
    document = await _ready(invoicing)
    before = local_now()

    document = await invoicing.confirm(document)

    assert document.status == 'sealed'
    assert document.serial == 42
    assert before.replace(microsecond=0) <= document.issued <= local_now()
    assert document.issued.microsecond == 0
    assert (document.version, document.completed) == (Decimal('4.0'), False)
    xml = await fds.get_xml(invoicing.db, document.fiscal_document_id)
    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    assert (root.get('Serie'), root.get('Folio'), root.get('MetodoPago')) == ('A', '42', 'PPD')
    assert root.get('Total') == '1160.00'


def _legacy_invoice(*, issuer: str, serial: int) -> FiscalDocument:
    when = datetime(2026, 1, 5, 10, 0)
    return FiscalDocument(
        creation_time=when,
        modification_time=when,
        creator=1,
        updater=1,
        issuer=issuer,
        customer=1,
        recipient=RFC,
        type=0,
        facility=1,
        batch='A',
        serial=serial,
        issued=when,
        issued_location='06000',
        completed=True,
        cancelled=False,
        payment_method=99,
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        payment_terms=0,
        version=Decimal('4.0'),
        provider=0,
        retention_rate=Decimal('0'),
        local_retention_rate=Decimal('0'),
    )


async def test_a_sealed_invoice_cannot_be_confirmed_or_edited(invoicing: Invoicing) -> None:
    document = await invoicing.confirm(await _ready(invoicing))
    line = document.lines[0].fiscal_document_detail_id

    await _status(409, svc.confirm(invoicing.db, invoicing.current, document))
    await _status(
        409,
        svc.update_line(
            invoicing.db, invoicing.current, document, line, InvoiceLineUpdate(price=Decimal('1'))
        ),
    )
    await _status(409, svc.remove_line(invoicing.db, invoicing.current, document, line))
    await _status(409, svc.add_sales_order(invoicing.db, invoicing.current, document, 1))


async def test_confirm_refuses_an_empty_invoice(invoicing: Invoicing) -> None:
    error = await _status(422, invoicing.confirm(await invoicing.create()))
    assert error.detail == 'The invoice has no lines'


async def test_confirm_refuses_a_line_without_sat_keys(invoicing: Invoicing) -> None:
    document = await _ready(invoicing)
    await invoicing.db.execute(update(FiscalDocumentDetail).values(product_service=None))
    await invoicing.db.commit()

    error = await _status(422, invoicing.confirm(await invoicing.load(document)))
    assert error.detail == 'Line 1: missing SAT product key or unit'


async def test_confirm_refuses_a_quantity_above_what_remains(invoicing: Invoicing) -> None:
    order = await seed_sales_order(invoicing.db, completed=True)
    first = await invoicing.add_order(await invoicing.create(), order)
    second = await invoicing.add_order(await invoicing.create(), order)
    await invoicing.confirm(first)

    await _status(422, invoicing.confirm(second))


async def test_confirm_refuses_a_stale_issue_date(invoicing: Invoicing) -> None:
    document = await _ready(invoicing)
    await invoicing.db.execute(
        update(FiscalDocument).values(issued=local_now() - timedelta(hours=73))
    )
    await invoicing.db.commit()

    error = await _status(422, invoicing.confirm(await invoicing.load(document)))
    assert error.detail == 'The issue date must be within the last 72 hours and not in the future'


async def test_confirm_refuses_an_issuer_without_a_valid_certificate(invoicing: Invoicing) -> None:
    document = await _ready(invoicing)
    await invoicing.db.execute(update(TaxpayerCertificate).values(valid_to=datetime(2025, 1, 1)))
    await invoicing.db.commit()

    error = await _status(422, invoicing.confirm(document))
    assert error.detail == 'The issuer has no active certificate valid at the issue date'


async def test_confirm_refuses_a_recipient_without_regime(invoicing: Invoicing) -> None:
    document = await _ready(invoicing)
    await invoicing.db.execute(update(FiscalDocument).values(taxpayer_regime=None))
    await invoicing.db.commit()

    await _status(422, invoicing.confirm(await invoicing.load(document)))


async def test_a_failed_seal_consumes_no_serial(invoicing: Invoicing) -> None:
    document = await _ready(invoicing)

    with patch.object(svc.cfdi_invoice, 'seal_async', side_effect=RuntimeError('boom')):
        with pytest.raises(RuntimeError):
            await svc.confirm(invoicing.db, invoicing.current, document)

    document = await invoicing.load(document)
    assert (document.status, document.serial, document.issued) == ('draft', None, None)
    xml = await invoicing.db.execute(select(FiscalDocumentXml))
    assert xml.first() is None
    assert (await invoicing.confirm(document)).serial == 1


# ── Manual lines (US2, FR-007) ────────────────────────────────────────────────


async def test_a_manual_line_takes_the_price_list_and_the_product(invoicing: Invoicing) -> None:
    document = await invoicing.create()

    await svc.add_manual_line(invoicing.db, invoicing.current, document, 1)

    [line] = (await invoicing.load(document)).lines
    assert (line.product, line.order_detail, line.quantity) == (1, None, Decimal('1'))
    # Customer 1 is on price list 1, where product 1 costs 100.
    assert (line.price, line.discount) == (Decimal('100'), Decimal('0'))
    assert (line.tax_rate, line.tax_included) == (Decimal('0.16'), False)
    assert (line.product_code, line.product_name) == ('P1', 'Producto Uno')
    assert (line.product_service, line.unit_of_measurement, line.unit_of_measurement_name) == (
        '01010101',
        'H87',
        'Pieza',
    )


async def test_a_manual_line_needs_a_price_in_the_customers_list(invoicing: Invoicing) -> None:
    invoicing.db.add(
        PriceList(
            price_list_id=2,
            name='Otra',
            high_profit_margin=Decimal('0.5'),
            low_profit_margin=Decimal('0.1'),
        )
    )
    await invoicing.db.flush()
    await invoicing.db.execute(update(Customer).values(price_list=2))
    await invoicing.db.commit()
    document = await invoicing.create()

    error = await _status(422, svc.add_manual_line(invoicing.db, invoicing.current, document, 1))
    assert error.detail == "The product has no price in the customer's price list"
    await _status(404, svc.add_manual_line(invoicing.db, invoicing.current, document, 999999))


async def test_a_blank_name_resets_to_the_products(invoicing: Invoicing) -> None:
    document = await invoicing.create()
    await svc.add_manual_line(invoicing.db, invoicing.current, document, 1)
    line = (await invoicing.load(document)).lines[0].fiscal_document_detail_id

    await svc.update_line(
        invoicing.db,
        invoicing.current,
        await invoicing.load(document),
        line,
        InvoiceLineUpdate(product_name='Servicio especial', quantity=Decimal('25')),
    )
    assert (await invoicing.load(document)).lines[0].product_name == 'Servicio especial'
    await svc.update_line(
        invoicing.db,
        invoicing.current,
        await invoicing.load(document),
        line,
        InvoiceLineUpdate(product_name='  '),
    )

    [updated] = (await invoicing.load(document)).lines
    assert (updated.product_name, updated.quantity) == ('Producto Uno', Decimal('25'))


async def test_a_standalone_invoice_seals_with_totals_from_its_lines(invoicing: Invoicing) -> None:
    document = await invoicing.create()
    await svc.add_manual_line(invoicing.db, invoicing.current, document, 1)
    await svc.add_manual_line(invoicing.db, invoicing.current, await invoicing.load(document), 1)
    first, second = (await invoicing.load(document)).lines
    await svc.update_line(
        invoicing.db,
        invoicing.current,
        await invoicing.load(document),
        second.fiscal_document_detail_id,
        InvoiceLineUpdate(quantity=Decimal('3'), price=Decimal('50'), discount=Decimal('0.1')),
    )

    document = await invoicing.confirm(document)

    xml = await fds.get_xml(invoicing.db, document.fiscal_document_id)
    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    # 1 × 100 + 16 % = 116.00; 3 × 50 − 15.00 discount = 135.00 + 16 % = 156.60.
    assert (root.get('SubTotal'), root.get('Descuento'), root.get('Total')) == (
        '250.00',
        '15.00',
        '272.60',
    )


async def test_the_clerks_flow_over_http(invoicing: Invoicing, client: AsyncClient) -> None:
    """US1's independent test, through the routes: create, add an order, edit, confirm."""
    order = await seed_sales_order(invoicing.db, completed=True)

    created = await client.post('/api/v1/fiscal-documents', json={'customer': 1, 'recipient': RFC})
    assert created.status_code == 201, created.text
    document = created.json()
    base = f'/api/v1/fiscal-documents/{document["fiscal_document_id"]}'
    added = await client.post(f'{base}/sales-orders', json={'sales_order': order})
    assert added.status_code == 200, added.text
    line = added.json()['lines'][0]['fiscal_document_detail_id']
    edited = await client.put(f'{base}/lines/{line}', json={'quantity': '4'})
    assert Decimal(edited.json()['lines'][0]['quantity']) == 4
    confirmed = await client.post(f'{base}/confirm')

    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()
    assert (body['status'], body['serial'], body['batch']) == ('sealed', 1, 'A')
    assert body['total'] == '464.00'
    again = await client.post(f'{base}/confirm')
    assert (again.status_code, again.json()['detail']) == (409, 'Fiscal document is not a draft')
    listed = await client.get('/api/v1/fiscal-documents', params={'status': 'sealed'})
    assert [d['fiscal_document_id'] for d in listed.json()['items']] == [
        document['fiscal_document_id']
    ]


# ── Header (US4, FR-008, FR-009) ──────────────────────────────────────────────


async def _header(invoicing: Invoicing, document: FiscalDocument, **body: object) -> FiscalDocument:
    document = await invoicing.load(document)
    await svc.update_header(
        invoicing.db, invoicing.current, document, InvoiceUpdate.model_validate(body)
    )
    return await invoicing.load(document)


async def test_header_fields_change_on_a_draft(invoicing: Invoicing) -> None:
    document = await invoicing.create()
    issued = local_now().replace(microsecond=0) - timedelta(hours=30)

    document = await _header(
        invoicing,
        document,
        usage='G03',
        payment_terms=0,
        payment_method=3,
        issued=issued.isoformat(),
        reference='OC-77',
        comment='Entrega parcial',
    )

    assert (document.usage, document.payment_terms, document.payment_method) == ('G03', 0, 3)
    assert (document.issued, document.reference, document.comment) == (
        issued,
        'OC-77',
        'Entrega parcial',
    )
    # Only the fields sent change.
    document = await _header(invoicing, document, comment=None)
    assert (document.comment, document.reference) == (None, 'OC-77')


async def test_credit_terms_force_payment_form_99(invoicing: Invoicing) -> None:
    document = await _header(invoicing, await invoicing.create(), payment_terms=0, payment_method=3)

    document = await _header(invoicing, document, payment_terms=1)
    assert (document.payment_terms, document.payment_method) == (1, 99)
    document = await _header(invoicing, document, payment_method=4)
    assert document.payment_method == 99


@pytest.mark.parametrize('hours', [-1, 72, 100])
async def test_the_issue_date_must_be_within_72_hours(invoicing: Invoicing, hours: int) -> None:
    document = await invoicing.create()
    issued = local_now().replace(microsecond=0) - timedelta(hours=hours)

    await _status(422, _header(invoicing, document, issued=issued.isoformat()))


async def test_a_new_recipient_is_copied_with_the_general_public_rule(invoicing: Invoicing) -> None:
    document = await _header(invoicing, await invoicing.create(), recipient=XAXX)

    assert (document.recipient, document.recipient_name) == (XAXX, 'PÚBLICO EN GENERAL')
    assert (document.taxpayer_regime, document.taxpayer_postal_code) == ('616', '06000')
    invoicing.db.add(TaxpayerRecipient(taxpayer_recipient_id='CCC010101CC1', name='C', email='c@x'))
    await invoicing.db.commit()
    await _status(422, _header(invoicing, document, recipient='CCC010101CC1'))


async def test_the_series_must_be_an_invoice_series_of_the_issuer(invoicing: Invoicing) -> None:
    invoicing.db.add(TaxpayerBatch(taxpayer=ISSUER_RFC, batch='B', type=0, template='{}'))
    await invoicing.db.commit()
    document = await invoicing.create()

    assert (await _header(invoicing, document, batch='B')).batch == 'B'
    await _status(422, _header(invoicing, document, batch='P'))


async def test_an_unknown_usage_is_refused(invoicing: Invoicing) -> None:
    await _status(422, _header(invoicing, await invoicing.create(), usage='ZZZ'))


async def test_changing_the_store_recopies_where_it_was_issued(invoicing: Invoicing) -> None:
    invoicing.db.add(SatPostalCode(sat_postal_code_id='55600', state='MEX'))
    await invoicing.db.flush()
    invoicing.db.add(
        Facility(
            facility_id=2,
            code='F2',
            name='Zumpango',
            type=FacilityType.STORE,
            location='55600',
            address=1,
            taxpayer=ISSUER_RFC,
            status=EntityStatus.ACTIVE,
        )
    )
    await invoicing.db.commit()
    document = await invoicing.create(recipient=XAXX)

    document = await _header(invoicing, document, facility=2)

    assert (document.facility, document.issued_location, document.taxpayer_postal_code) == (
        2,
        '55600',
        '55600',
    )
    await _status(422, _header(invoicing, document, facility=999))


# ── Relations (US4, FR-010) ───────────────────────────────────────────────────


async def _stamped(invoicing: Invoicing, **overrides: object) -> int:
    values = {'serial': 7, 'stamp_uuid': 'A1B2C3D4-0000-4000-8000-000000000001'} | overrides
    document = _legacy_invoice(issuer=ISSUER_RFC, serial=values.pop('serial'))
    for name, value in values.items():
        setattr(document, name, value)
    invoicing.db.add(document)
    await invoicing.db.commit()
    return document.fiscal_document_id


async def _relate(invoicing: Invoicing, document: FiscalDocument, relation: int, type_: str):  # noqa: ANN202
    document = await invoicing.load(document)
    await svc.add_relation(
        invoicing.db,
        invoicing.current,
        document,
        InvoiceRelationAdd.model_validate({'relation': relation, 'type': type_}),
    )
    return await invoicing.load(document)


async def test_an_advance_is_applied_and_written_to_the_xml(invoicing: Invoicing) -> None:
    advance = await _stamped(invoicing)
    applied = await _stamped(
        invoicing, type=101, serial=8, stamp_uuid='A1B2C3D4-0000-4000-8000-000000000002'
    )
    document = await _ready(invoicing)

    document = await _relate(invoicing, document, advance, '07')
    document = await _relate(invoicing, document, applied, '07')

    assert [(r['relation'], r['type'], r['serial']) for r in document.relations] == [
        (advance, '7', 7),
        (applied, '7', 8),
    ]
    response = FiscalDocumentResponse.model_validate(document)
    assert [r.type for r in response.relations] == ['07', '07']
    document = await invoicing.confirm(document)
    root = etree.fromstring((await fds.get_xml(invoicing.db, document.fiscal_document_id)).encode())
    related = root.find('{http://www.sat.gob.mx/cfd/4}CfdiRelacionados')
    assert related.get('TipoRelacion') == '07'
    assert [r.get('UUID') for r in related] == [
        'A1B2C3D4-0000-4000-8000-000000000001',
        'A1B2C3D4-0000-4000-8000-000000000002',
    ]


async def test_a_substitution_relates_a_cancelled_invoice(invoicing: Invoicing) -> None:
    cancelled = await _stamped(invoicing, cancelled=True)
    live = await _stamped(invoicing, serial=8, stamp_uuid='A1B2C3D4-0000-4000-8000-000000000002')
    document = await invoicing.create()

    await _status(422, _relate(invoicing, document, live, '04'))
    await _status(422, _relate(invoicing, document, cancelled, '07'))
    document = await _relate(invoicing, document, cancelled, '04')

    assert [r['type'] for r in document.relations] == ['4']
    # One type per invoice.
    error = await _status(422, _relate(invoicing, document, live, '07'))
    assert error.detail == 'All relations must share one type'


async def test_relations_need_the_same_issuer_recipient_and_a_stamp(invoicing: Invoicing) -> None:
    other_recipient = await _stamped(invoicing, recipient=XAXX)
    other_issuer = await _stamped(invoicing, issuer='EKU9003173C9', serial=9)
    unstamped = await _stamped(invoicing, serial=10, stamp_uuid=None)
    credit_note = await _stamped(invoicing, type=100, serial=11)
    document = await invoicing.create()

    for target in (other_recipient, other_issuer, unstamped, credit_note):
        await _status(422, _relate(invoicing, document, target, '07'))
    await _status(404, _relate(invoicing, document, 999999, '07'))


async def test_a_relation_is_added_once_and_can_be_removed(invoicing: Invoicing) -> None:
    advance = await _stamped(invoicing)
    document = await _relate(invoicing, await invoicing.create(), advance, '07')

    await _status(409, _relate(invoicing, document, advance, '07'))
    relation = document.relations[0]['fiscal_document_relation_id']
    await svc.remove_relation(invoicing.db, invoicing.current, document, relation)

    assert (await invoicing.load(document)).relations == []
    await _status(404, svc.remove_relation(invoicing.db, invoicing.current, document, relation))


# ── Reopen (US4, FR-015) ──────────────────────────────────────────────────────


async def test_reopen_keeps_the_serial_and_reseals_with_a_new_date(invoicing: Invoicing) -> None:
    sealed = await invoicing.confirm(await _ready(invoicing))
    serial = sealed.serial

    await svc.reopen(invoicing.db, invoicing.current, sealed)
    document = await invoicing.load(sealed)
    assert (document.status, document.serial) == ('draft', serial)
    assert await fds.get_xml(invoicing.db, document.fiscal_document_id) is None

    issued = local_now().replace(microsecond=0) - timedelta(hours=1)
    document = await _header(invoicing, document, issued=issued.isoformat())
    # Another invoice in the same series meanwhile must not take or move this serial.
    await invoicing.confirm(await _ready(invoicing))
    document = await invoicing.confirm(document)

    assert (document.status, document.serial, document.issued) == ('sealed', serial, issued)
    await _status(409, _header(invoicing, document, comment='x'))
    await _status(409, svc.reopen(invoicing.db, invoicing.current, await invoicing.create()))


async def test_a_reopened_invoice_keeps_its_series(invoicing: Invoicing) -> None:
    invoicing.db.add(TaxpayerBatch(taxpayer=ISSUER_RFC, batch='B', type=0, template='{}'))
    await invoicing.db.commit()
    sealed = await invoicing.confirm(await _ready(invoicing))
    await svc.reopen(invoicing.db, invoicing.current, sealed)

    await _status(409, _header(invoicing, await invoicing.load(sealed), batch='B'))


async def test_a_stamped_invoice_is_never_changed(invoicing: Invoicing) -> None:
    stamped = await invoicing.load(await _stamped(invoicing))

    assert stamped.status == 'issued'
    await _status(409, svc.reopen(invoicing.db, invoicing.current, stamped))
    await _status(409, svc.confirm(invoicing.db, invoicing.current, stamped))
    await _status(409, _header(invoicing, stamped, comment='x'))
    await _status(409, _relate(invoicing, stamped, stamped.fiscal_document_id, '07'))
