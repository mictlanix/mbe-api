"""The minimum set of rows that makes the API answerable: one of everything, at id 1.

Every endpoint that takes an id is called with `1` by the smoke test, so seeding at 1 is what turns
"404, lookup worked" into "the handler actually ran". The flow tests build on the same rows.

Deliberately one row per table, not a fixture library. What each flow needs beyond this — a sales
order with lines, a confirmed delivery — it creates through the API, because creating it through the
API is the thing under test.
"""

import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import EllipsisType

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    CurrencyCode,
    EntityStatus,
    FacilityType,
    FiscalCertificationProvider,
    PaymentTerms,
    Priority,
)
from app.models.core import (
    Address,
    CashDrawer,
    Contact,
    Employee,
    Facility,
    PointSale,
    Warehouse,
)
from app.models.customer import Customer, TaxpayerRecipient
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentXml,
    TaxpayerBatch,
    TaxpayerIssuer,
)
from app.models.product import PriceList, Product, ProductPrice
from app.models.sales import SalesOrder, SalesOrderDetail
from app.models.sat_catalog import (
    SatCfdiUsage,
    SatPostalCode,
    SatProductService,
    SatTaxRegime,
    SatUnitOfMeasurement,
)
from app.models.user import User

RFC = 'AAA010101AAA'
ISSUER_RFC = 'BBB020202BB1'
POSTAL_CODE = '06000'
UNIT = 'H87'
REGIME = '601'


async def seed_baseline(db: AsyncSession) -> None:
    """One row per table an endpoint is likely to reach, committed so every session sees it."""
    # SAT catalogs first: products and issuers hold foreign keys into them.
    db.add_all(
        [
            SatUnitOfMeasurement(sat_unit_of_measurement_id=UNIT, name='Pieza', symbol='pz'),
            SatTaxRegime(sat_tax_regime_id=REGIME, description='General de Ley Personas Morales'),
            SatProductService(
                sat_product_service_id='01010101', description='No existe en catálogo'
            ),
            SatPostalCode(sat_postal_code_id=POSTAL_CODE, state='CDMX'),
        ]
    )
    await db.flush()

    db.add(
        Address(
            address_id=1,
            type=1,
            street='Av. Reforma',
            exterior_number='100',
            postal_code=POSTAL_CODE,
            neighborhood='Centro',
            borough='Cuauhtémoc',
            state='CDMX',
            city='Ciudad de México',
            country='MX',
            status=EntityStatus.ACTIVE,
        )
    )
    db.add(
        Employee(
            employee_id=1,
            first_name='Ana',
            last_name='Ruiz',
            nickname='ana',
            gender=1,
            birthday=date(1990, 1, 1),
            sales_person=True,
            status=EntityStatus.ACTIVE,
            start_job_date=date(2020, 1, 1),
        )
    )
    db.add(
        TaxpayerIssuer(
            taxpayer_issuer_id=ISSUER_RFC,
            name='Mictlanix SA de CV',
            regime=REGIME,
            postal_code=POSTAL_CODE,
            provider=FiscalCertificationProvider.NONE,
        )
    )
    db.add(TaxpayerRecipient(taxpayer_recipient_id=RFC, name='Acme', email='a@example.com'))
    db.add(
        PriceList(
            price_list_id=1,
            name='General',
            high_profit_margin=Decimal('0.5'),
            low_profit_margin=Decimal('0.1'),
        )
    )
    await db.flush()

    db.add(
        Facility(
            facility_id=1,
            code='F1',
            name='Matriz',
            type=FacilityType.STORE,
            location=POSTAL_CODE,
            address=1,
            taxpayer=ISSUER_RFC,
            logo='logo.png',
            status=EntityStatus.ACTIVE,
        )
    )
    await db.flush()

    db.add_all(
        [
            Warehouse(
                warehouse_id=1,
                facility=1,
                code='W1',
                name='Almacén',
                status=EntityStatus.ACTIVE,
                in_transit=False,
            ),
            # Dispatch needs one per facility, and it must never be chosen as a source (FR-012).
            Warehouse(
                warehouse_id=2,
                facility=1,
                code='WT',
                name='En tránsito',
                status=EntityStatus.ACTIVE,
                in_transit=True,
            ),
        ]
    )
    await db.flush()

    db.add_all(
        [
            PointSale(
                point_sale_id=1,
                facility=1,
                code='POS1',
                name='Caja 1',
                warehouse=1,
                status=EntityStatus.ACTIVE,
            ),
            CashDrawer(
                cash_drawer_id=1, facility=1, code='CD1', name='Cajón 1', status=EntityStatus.ACTIVE
            ),
            Contact(contact_id=1, name='Juan Pérez', mobile='5555555555'),
            Customer(
                customer_id=1,
                code='C1',
                name='Cliente Uno',
                credit_limit=Decimal('1000'),
                credit_days=30,
                price_list=1,
                status=EntityStatus.ACTIVE,
            ),
            User(
                user_id='tester',
                password='x',
                email='tester@example.com',
                employee_id=1,
                administrator=True,
                status=EntityStatus.ACTIVE,
                session_version=1,
            ),
            Product(
                product_id=1,
                code='P1',
                name='Producto Uno',
                photo='p1.png',
                unit_of_measurement=UNIT,
                stockable=True,
                perishable=False,
                seriable=False,
                purchasable=True,
                salable=True,
                invoiceable=True,
                tax_rate=Decimal('0.16'),
                tax_included=False,
                price_type=0,
                currency=CurrencyCode.MXN,
                min_order_qty=1,
                status=EntityStatus.ACTIVE,
                stock_verification=False,
            ),
        ]
    )
    await db.flush()

    db.add(
        ProductPrice(
            product=1, price_list=1, price=Decimal('100'), low_profit=Decimal('0.1'),
            high_profit=Decimal('0.5'),
        )
    )
    await db.commit()


async def seed_sales_order(
    db: AsyncSession, *, completed: bool = False, paid: bool = False
) -> int:
    """A sales order with one line, so the delivery flow has something to be raised from.

    `completed` and `paid` are separate because the guards are: a delivery needs a completed sale, a
    refund needs a paid one, and "completed but not paid" is the state that tells the two apart.
    """
    now = datetime(2026, 8, 1, 10, 0)
    order = SalesOrder(
        creator=1,
        updater=1,
        creation_time=now,
        modification_time=now,
        facility=1,
        point_sale=1,
        salesperson=1,
        customer=1,
        date=now,
        promise_date=now,
        due_date=now,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.IMMEDIATE,
        priority=Priority.NORMAL,
        completed=completed,
        cancelled=False,
        paid=paid,
        delivered=False,
        serial=1 if completed else None,
    )
    db.add(order)
    await db.flush()

    db.add(
        SalesOrderDetail(
            sales_order=order.sales_order_id,
            product=1,
            quantity=Decimal('10'),
            cost=Decimal('50'),
            price=Decimal('100'),
            discount_rate=Decimal('0'),
            tax_rate=Decimal('0.16'),
            product_code='P1',
            product_name='Producto Uno',
            warehouse=1,
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )
    )
    await db.commit()
    return order.sales_order_id


CFDI_FIXTURES = Path(__file__).parent.parent / 'fixtures' / 'cfdi'
_FIXTURE_TYPES = {
    'invoice': 0,
    'invoice_retention': 0,
    'credit_note': 100,
    'advance': 101,
    'payment': 200,
}
# Every batch in `mbe_dev` holds this text, single quotes included (spec 020, research R5).
LEGACY_BATCH_TEMPLATE = """{
    'Name': 'T02Blue',
    'Logo': '~/Content/images/casamaestra.png',
    'HeaderHeight': 0,
    'FooterHeight': 15,
    'ExtraInfo': []
}"""


def _attrs(element: ET.Element) -> dict[str, str]:
    return {key.split('}')[-1]: value for key, value in element.attrib.items()}


async def seed_fiscal_document(
    db: AsyncSession,
    fixture: str,
    *,
    completed: bool = True,
    cancelled: bool = False,
    stamp: bool = True,
    xml: str | None | EllipsisType = ...,
    comments: list[str | None] | None = None,
    template: str = LEGACY_BATCH_TEMPLATE,
    **overrides: object,
) -> int:
    """A fiscal document built from a stamped fixture in `tests/fixtures/cfdi/`.

    The row's values are read from the fixture, as legacy copied them at stamping. `xml` defaults
    to the fixture's text for an issued document and to none for a draft, as in `mbe_dev`.
    `overrides` set any other `fiscal_document` column.
    """
    text = (CFDI_FIXTURES / f'{fixture}.xml').read_text(encoding='utf-8')
    root = ET.fromstring(text.encode('utf-8'))
    comprobante = _attrs(root)
    emisor = _attrs(root.find('{*}Emisor'))
    receptor = _attrs(root.find('{*}Receptor'))
    timbre = _attrs(root.find('.//{*}TimbreFiscalDigital'))
    conceptos = [_attrs(c) for c in root.findall('.//{*}Concepto')]

    # Catalog rows and the issuer, created once per test database.
    rows = [
        TaxpayerIssuer(
            taxpayer_issuer_id=emisor['Rfc'],
            name=emisor['Nombre'],
            regime=REGIME,
            postal_code=POSTAL_CODE,
            provider=FiscalCertificationProvider.NONE,
        ),
        SatTaxRegime(sat_tax_regime_id='612', description='Personas Físicas con Actividades'),
        SatTaxRegime(sat_tax_regime_id='626', description='Régimen Simplificado de Confianza'),
        *(
            SatCfdiUsage(sat_cfdi_usage_id=code, description=description)
            for code, description in (
                ('G01', 'Adquisición de mercancías'),
                ('G02', 'Devoluciones, descuentos o bonificaciones'),
                ('G03', 'Gastos en general'),
                ('CP01', 'Pagos'),
            )
        ),
        *(
            SatProductService(sat_product_service_id=c['ClaveProdServ'], description='Fixture')
            for c in conceptos
        ),
        *(
            SatUnitOfMeasurement(
                sat_unit_of_measurement_id=c['ClaveUnidad'], name=c.get('Unidad', ''), symbol=''
            )
            for c in conceptos
        ),
    ]
    for row in rows:
        await db.merge(row)
    await db.flush()
    batch = comprobante.get('Serie')
    existing = await db.execute(
        select(TaxpayerBatch).where(
            TaxpayerBatch.taxpayer == emisor['Rfc'], TaxpayerBatch.batch == batch
        )
    )
    if existing.first() is None:
        db.add(
            TaxpayerBatch(
                taxpayer=emisor['Rfc'], batch=batch, type=_FIXTURE_TYPES[fixture], template=template
            )
        )

    issued = datetime.fromisoformat(comprobante['Fecha'])
    values = dict(
        creation_time=issued,
        modification_time=issued,
        creator=1,
        updater=1,
        issuer=emisor['Rfc'],
        issuer_name=emisor['Nombre'],
        issuer_regime=emisor['RegimenFiscal'],
        issuer_regime_name='General de Ley Personas Morales',
        customer=1,
        recipient=receptor['Rfc'],
        recipient_name=receptor['Nombre'],
        taxpayer_regime=receptor['RegimenFiscalReceptor'],
        taxpayer_postal_code=receptor['DomicilioFiscalReceptor'],
        usage=receptor['UsoCFDI'],
        type=_FIXTURE_TYPES[fixture],
        facility=1,
        batch=batch,
        serial=int(comprobante['Folio']),
        issued=issued,
        issued_location=comprobante['LugarExpedicion'],
        completed=completed,
        cancelled=cancelled,
        cancellation_date=datetime(2026, 9, 29, 10, 0) if cancelled else None,
        payment_method=int(comprobante.get('FormaPago', '99')),
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        payment_terms=0 if comprobante.get('MetodoPago') != 'PPD' else 1,
        version=Decimal('4.0'),
        provider=0,
        retention_rate=Decimal('0'),
        local_retention_rate=Decimal('0'),
    )
    if stamp:
        values.update(
            stamped=datetime.fromisoformat(timbre['FechaTimbrado']),
            stamp_uuid=timbre['UUID'],
            authority_digital_seal=timbre['SelloSAT'],
            authority_certificate_number=timbre['NoCertificadoSAT'],
            rfc_pac=timbre['RfcProvCertif'],
        )
    values.update(overrides)
    document = FiscalDocument(**values)
    db.add(document)
    await db.flush()

    comments = comments or [None] * len(conceptos)
    for concepto, comment in zip(conceptos, comments, strict=True):
        db.add(
            FiscalDocumentDetail(
                document=document.fiscal_document_id,
                product=1,
                product_service=concepto['ClaveProdServ'],
                product_code=concepto.get('NoIdentificacion'),
                product_name=concepto['Descripcion'],
                unit_of_measurement=concepto['ClaveUnidad'],
                unit_of_measurement_name=concepto.get('Unidad'),
                quantity=Decimal(concepto['Cantidad']),
                price=Decimal(concepto['ValorUnitario']),
                discount=Decimal('0'),
                tax_rate=Decimal('0.16'),
                exchange_rate=Decimal('1'),
                currency=CurrencyCode.MXN,
                tax_included=False,
                comment=comment,
            )
        )
    if xml is ...:
        xml = text if completed else None
    if xml is not None:
        db.add(FiscalDocumentXml(fiscal_document_xml_id=document.fiscal_document_id, data=xml))
    await db.commit()
    return document.fiscal_document_id
