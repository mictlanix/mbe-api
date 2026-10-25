"""Payment receipts on real tables: create, paid invoices, balances and confirm (spec 028)."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from lxml import etree
from sqlalchemy import insert, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import CurrencyCode, EntityStatus, PaymentType
from app.models.customer import Customer, TaxpayerRecipient, customer_taxpayer
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentRelation,
    FiscalDocumentXml,
    TaxpayerBatch,
    TaxpayerCertificate,
)
from app.models.sales import CustomerPayment, SalesOrderDetail, SalesOrderPayment
from app.schemas.fiscal import (
    InvoiceUpdate,
    PaidInvoiceAdd,
    PaidInvoiceUpdate,
    PaymentReceiptCreate,
)
from app.services import fiscal_document_service as fds
from app.services import invoice_service as svc
from tests.cfdi_schema import assert_valid_cfdi40
from tests.integration.seed import seed_fiscal_document, seed_sales_order
from tests.unit.test_csd_service import _CERTIFICATE_NUMBER, _PASSWORD, _make_csd

ISSUER = 'EKU9003173C9'
RECIPIENT = 'XAÑ010101AB1'
_NS = {'cfdi': 'http://www.sat.gob.mx/cfd/4', 'pago20': 'http://www.sat.gob.mx/Pagos20'}
pytestmark = pytest.mark.usefixtures('seeded')


class Receipts:
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

    async def create(self, **body: object) -> FiscalDocument:
        data = PaymentReceiptCreate.model_validate(
            {'customer': 1, 'recipient': RECIPIENT, 'issuer': ISSUER, **body}
        )
        return await self.load(await svc.create_payment_receipt(self.db, self.current, data))

    async def header(self, document: FiscalDocument, **body: object) -> FiscalDocument:
        document = await self.load(document)
        await svc.update_header(self.db, self.current, document, InvoiceUpdate.model_validate(body))
        return await self.load(document)

    async def pay(self, document: FiscalDocument, invoice: int, **body: object) -> FiscalDocument:
        document = await self.load(document)
        await svc.add_paid_invoice(
            self.db,
            self.current,
            document,
            PaidInvoiceAdd.model_validate({'invoice': invoice, **body}),
        )
        return await self.load(document)

    async def confirm(self, document: FiscalDocument) -> FiscalDocument:
        document = await self.load(document)
        await svc.confirm(self.db, self.current, document)
        return await self.load(document)

    async def ready(self, invoice: int, amount: str) -> FiscalDocument:
        """A draft receipt paying `amount` of `invoice`, dated 1 Oct 2026."""
        document = await self.create()
        document = await self.header(
            document, payment_date='2026-10-01', payment_amount=amount, payment_method=3
        )
        return await self.pay(document, invoice)


@pytest.fixture
async def receipts(db: AsyncSession, current_user: CurrentUser) -> Receipts:
    """Fixture invoice RH 11405's issuer with a receipt series and a valid CSD."""
    await seed_fiscal_document(db, 'invoice')  # creates the issuer, catalogs and CP01 usage
    await db.execute(update(FiscalDocument).values(cancelled=True))  # keep only the test's own
    certificate_data, key_data = _make_csd(rfc=ISSUER)
    db.add_all(
        [
            TaxpayerBatch(taxpayer=ISSUER, batch='P', type=200, template='{}'),
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
    return Receipts(db, current_user)


async def _ppd_invoice(db: AsyncSession, **overrides: object) -> int:
    """A stamped PPD invoice, RH 11405 from the fixture: total 3,160.00, all at 16 %."""
    return await seed_fiscal_document(db, 'invoice', payment_terms=1, **overrides)


async def _status(code: int, call) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == code, caught.value.detail
    return caught.value


def _relation(document: FiscalDocument, invoice: int) -> dict:
    return next(p for p in document.paid_invoices if p['invoice'] == invoice)


# ── Create ────────────────────────────────────────────────────────────────────


async def test_create_by_hand_applies_the_defaults(receipts: Receipts) -> None:
    document = await receipts.create()

    assert (document.type, document.batch, document.usage, document.payment_terms) == (
        200,
        'P',
        'CP01',
        0,
    )
    assert (document.status, document.serial, document.customer_payment) == ('draft', None, None)
    assert (document.recipient, document.taxpayer_regime) == (RECIPIENT, '601')


async def test_create_needs_a_receipt_series(receipts: Receipts) -> None:
    await receipts.db.execute(update(TaxpayerBatch).where(TaxpayerBatch.type == 200).values(type=1))
    await receipts.db.commit()

    error = await _status(422, receipts.create())
    assert error.detail == 'The issuer has no payment receipt series'


# ── Paid invoices and balances ────────────────────────────────────────────────


async def test_a_paid_invoice_shows_its_installment_and_balance(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)

    document = await receipts.ready(invoice, '1000.00')

    paid = _relation(document, invoice)
    assert (paid['installment'], paid['previous_balance'], paid['amount'], paid['remaining']) == (
        1,
        Decimal('3160.00'),
        Decimal('1000.00'),
        Decimal('2160.00'),
    )
    assert (paid['batch'], paid['serial']) == ('RH', 11405)


async def test_the_default_amount_is_capped_by_the_balance(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)

    document = await receipts.ready(invoice, '5000.00')

    assert _relation(document, invoice)['amount'] == Decimal('3160.00')


@pytest.mark.parametrize(
    'overrides',
    [
        {'payment_terms': 0},
        {'stamp': False, 'completed': False},
        {'cancelled': True},
        {'recipient': 'CAC010101AB2'},
    ],
)
async def test_only_live_ppd_invoices_of_the_recipient_can_be_paid(
    receipts: Receipts, overrides: dict
) -> None:
    invoice = await seed_fiscal_document(
        receipts.db, 'invoice', **({'payment_terms': 1} | overrides)
    )
    document = await receipts.header(
        await receipts.create(), payment_date='2026-10-01', payment_amount='100.00'
    )

    error = await _status(422, receipts.pay(document, invoice))
    assert error.detail == 'The invoice cannot be paid by this receipt'


async def test_amount_limits(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    other = await _ppd_invoice(receipts.db)
    document = await receipts.header(
        await receipts.create(), payment_date='2026-10-01', payment_amount='3500.00'
    )

    error = await _status(422, receipts.pay(document, invoice, amount='3160.01'))
    assert error.detail == 'Amount exceeds the invoice balance of 3160.00'
    document = await receipts.pay(document, invoice, amount='3000.00')
    error = await _status(422, receipts.pay(document, other, amount='600.00'))
    assert error.detail == 'The amounts paid exceed the payment amount'
    await _status(409, receipts.pay(document, invoice, amount='1.00'))
    relation = _relation(document, invoice)['fiscal_document_relation_id']
    await svc.update_paid_invoice(
        receipts.db,
        receipts.current,
        await receipts.load(document),
        relation,
        PaidInvoiceUpdate(amount=Decimal('2900.00')),
    )
    document = await receipts.pay(document, other)
    assert _relation(document, other)['amount'] == Decimal('600.00')
    await svc.remove_paid_invoice(receipts.db, receipts.current, document, relation)
    assert [p['invoice'] for p in (await receipts.load(document)).paid_invoices] == [other]


async def test_the_payment_date_cannot_precede_the_invoice(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)  # issued 2026-09-29
    document = await receipts.header(
        await receipts.create(), payment_date='2026-09-28', payment_amount='100.00'
    )

    error = await _status(422, receipts.pay(document, invoice))
    assert error.detail == "The payment date must be between the invoices' dates and today"


async def test_the_payment_amount_cannot_drop_below_the_amounts_paid(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    document = await receipts.ready(invoice, '1000.00')

    error = await _status(422, receipts.header(document, payment_amount='999.99'))
    assert error.detail == 'The amounts paid exceed the payment amount'


# ── Confirm ───────────────────────────────────────────────────────────────────


async def test_confirm_seals_a_valid_pagos_20_receipt(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)

    document = await receipts.confirm(await receipts.ready(invoice, '1000.00'))

    assert (document.status, document.batch, document.serial) == ('sealed', 'P', 1)
    stored = (
        await receipts.db.execute(
            select(FiscalDocumentRelation).where(
                FiscalDocumentRelation.document == document.fiscal_document_id
            )
        )
    ).scalar_one()
    assert (stored.installment, stored.previous_balance, stored.amount) == (
        1,
        Decimal('3160.00'),
        Decimal('1000.00'),
    )
    xml = await fds.get_xml(receipts.db, document.fiscal_document_id)
    assert_valid_cfdi40(xml)
    root = etree.fromstring(xml.encode('utf-8'))
    [docto] = root.findall('.//pago20:DoctoRelacionado', _NS)
    assert (
        docto.get('NumParcialidad'),
        docto.get('ImpSaldoAnt'),
        docto.get('ImpSaldoInsoluto'),
    ) == (
        '1',
        '3160.00',
        '2160.00',
    )
    assert root.find('.//pago20:Pago', _NS).get('FechaPago') == '2026-10-01T00:00:00'


@pytest.mark.parametrize(
    ('header', 'detail'),
    [
        ({'payment_amount': '1000.01'}, 'The amounts paid must equal the payment amount'),
        ({'payment_method': 1}, None),
    ],
)
async def test_confirm_checks_the_payment(receipts: Receipts, header: dict, detail: str) -> None:
    invoice = await _ppd_invoice(receipts.db)
    document = await receipts.header(await receipts.ready(invoice, '1000.00'), **header)

    if detail is None:
        assert (await receipts.confirm(document)).status == 'sealed'
    else:
        error = await _status(422, receipts.confirm(document))
        assert error.detail == detail


async def test_confirm_needs_a_paid_invoice_and_a_date(receipts: Receipts) -> None:
    document = await receipts.header(
        await receipts.create(), payment_date='2026-10-01', payment_amount='10.00'
    )
    error = await _status(422, receipts.confirm(document))
    assert error.detail == 'The receipt pays no invoice'


async def test_a_second_receipt_continues_the_installments(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    await receipts.confirm(await receipts.ready(invoice, '1000.00'))

    second = await receipts.ready(invoice, '2160.00')

    paid = _relation(second, invoice)
    assert (paid['installment'], paid['previous_balance'], paid['remaining']) == (
        2,
        Decimal('2160.00'),
        Decimal('0.00'),
    )
    assert (await receipts.confirm(second)).serial == 2


async def test_a_credit_note_lowers_the_balance(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    credit_note = await seed_fiscal_document(receipts.db, 'credit_note', issuer=ISSUER)
    receipts.db.add(
        FiscalDocumentRelation(
            document=credit_note,
            relation=invoice,
            type='1',
            exchange_rate=Decimal(1),
            installment=1,
            previous_balance=Decimal(0),
            amount=Decimal(0),
        )
    )
    await receipts.db.execute(
        update(FiscalDocumentXml)
        .where(FiscalDocumentXml.fiscal_document_xml_id == credit_note)
        .values(data='<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" Total="160.00"/>')
    )
    await receipts.db.commit()

    document = await receipts.ready(invoice, '3000.00')

    paid = _relation(document, invoice)
    assert (paid['installment'], paid['previous_balance']) == (1, Decimal('3000.00'))


async def test_reopening_frees_the_amount(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    sealed = await receipts.confirm(await receipts.ready(invoice, '3160.00'))

    await svc.reopen(receipts.db, receipts.current, sealed)

    other = await receipts.ready(invoice, '3160.00')
    assert _relation(other, invoice)['installment'] == 1


async def test_confirm_recomputes_under_the_lock(receipts: Receipts) -> None:
    """Two drafts each claiming the whole balance: the second confirm finds nothing left."""
    invoice = await _ppd_invoice(receipts.db)
    first = await receipts.ready(invoice, '3160.00')
    second = await receipts.ready(invoice, '3160.00')

    await receipts.confirm(first)

    error = await _status(422, receipts.confirm(second))
    assert error.detail == 'Amount exceeds the invoice balance of 0.00'


# ── From a customer payment ───────────────────────────────────────────────────


async def _payment_applied_to(db: AsyncSession, invoice: int) -> int:
    """A 1,160.00 transfer applied to an order whose line is on `invoice`."""
    order = await seed_sales_order(db, completed=True, paid=True)
    detail = (
        await db.execute(
            select(SalesOrderDetail.sales_order_detail_id).where(
                SalesOrderDetail.sales_order == order
            )
        )
    ).scalar_one()
    first_line = (
        await db.execute(
            select(FiscalDocumentDetail.fiscal_document_detail_id)
            .where(FiscalDocumentDetail.document == invoice)
            .limit(1)
        )
    ).scalar_one()
    await db.execute(
        update(FiscalDocumentDetail)
        .where(FiscalDocumentDetail.fiscal_document_detail_id == first_line)
        .values(order_detail=detail)
    )
    when = datetime(2026, 10, 2, 11, 0)
    payment = CustomerPayment(
        amount=Decimal('1160.0000'),
        method=3,
        date=when,
        reference='SPEI-0042',
        customer=1,
        facility=1,
        serial=1,
        creator=1,
        updater=1,
        creation_time=when,
        modification_time=when,
        currency=CurrencyCode.MXN,
        payment_type=PaymentType.CREDIT_PAYMENT,
    )
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=order,
            customer_payment=payment.customer_payment_id,
            amount=Decimal('1160.0000'),
            amount_change=Decimal(0),
            date=when,
            cancelled=False,
        )
    )
    await db.commit()
    return payment.customer_payment_id


async def test_create_from_a_customer_payment(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    payment = await _payment_applied_to(receipts.db, invoice)

    document = await receipts.create(customer_payment=payment)

    assert document.customer_payment == payment
    assert (document.payment_date, document.payment_amount, document.payment_method) == (
        date(2026, 10, 2),
        Decimal('1160.00'),
        3,
    )
    assert document.payment_reference == 'SPEI-0042'
    assert [(p['invoice'], p['amount']) for p in document.paid_invoices] == [
        (invoice, Decimal('1160.00'))
    ]
    assert (await receipts.confirm(document)).status == 'sealed'


async def test_a_payment_backs_one_live_receipt(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    payment = await _payment_applied_to(receipts.db, invoice)
    first = await receipts.create(customer_payment=payment)

    error = await _status(409, receipts.create(customer_payment=payment))
    assert error.detail.startswith('This payment already has receipt')
    await receipts.db.execute(
        update(FiscalDocument)
        .where(FiscalDocument.fiscal_document_id == first.fiscal_document_id)
        .values(cancelled=True)
    )
    await receipts.db.commit()
    assert (await receipts.create(customer_payment=payment)).customer_payment == payment


async def test_a_payment_of_another_customer_is_refused(receipts: Receipts) -> None:
    invoice = await _ppd_invoice(receipts.db)
    payment = await _payment_applied_to(receipts.db, invoice)
    receipts.db.add(
        Customer(
            customer_id=2,
            code='C2',
            name='Otro',
            credit_limit=Decimal(0),
            credit_days=0,
            price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )
    await receipts.db.flush()
    await receipts.db.execute(update(CustomerPayment).values(customer=2))
    await receipts.db.commit()

    await _status(422, receipts.create(customer_payment=payment))
