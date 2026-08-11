"""The minimum set of rows that makes the API answerable: one of everything, at id 1.

Every endpoint that takes an id is called with `1` by the smoke test, so seeding at 1 is what turns
"404, lookup worked" into "the handler actually ran". The flow tests build on the same rows.

Deliberately one row per table, not a fixture library. What each flow needs beyond this — a sales
order with lines, a confirmed delivery — it creates through the API, because creating it through the
API is the thing under test.
"""

from datetime import date, datetime
from decimal import Decimal

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
from app.models.fiscal import TaxpayerIssuer
from app.models.product import PriceList, Product, ProductPrice
from app.models.sales import SalesOrder, SalesOrderDetail
from app.models.sat_catalog import (
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
                shipping=False,
                shipping_required_document=False,
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
