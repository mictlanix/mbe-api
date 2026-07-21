"""Reproduces GH #95: `_attach_relations` helpers expand a FK by writing to the ORM
instance's `__dict__`. Those instances are shared through the session identity map, so
writing over the mapped column itself corrupts every other response that reads the raw FK.
The expansion must land on a separate key and leave the mapped column untouched."""

from datetime import date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enums import AddressType, EntityStatus, FacilityType
from app.models.core import (
    Address,
    CashDrawer,
    Employee,
    Facility,
    PaymentMethodOption,
    VehicleOperator,
    Warehouse,
)
from app.models.customer import Customer, TaxpayerRecipient
from app.models.product import PriceList, Product
from app.models.sat_catalog import (
    SatPostalCode,
    SatProductService,
    SatTaxRegime,
    SatUnitOfMeasurement,
)
from app.models.supplier import Supplier
from app.schemas.core import (
    CashDrawerResponse,
    FacilityResponse,
    FacilitySummary,
    PaymentMethodOptionResponse,
    VehicleOperatorResponse,
    WarehouseResponse,
    WarehouseSummary,
)
from app.schemas.customer import CustomerResponse, TaxpayerRecipientResponse
from app.schemas.product import ProductResponse
from app.services import (
    cash_drawer_service,
    customer_service,
    facility_service,
    payment_method_option_service,
    product_service,
    taxpayer_recipient_service,
    vehicle_operator_service,
    warehouse_service,
)


def _db_returning(*batches: list) -> AsyncMock:
    """A db whose successive `execute` calls return the given row batches in order."""

    def _result(rows: list) -> MagicMock:
        result = MagicMock()
        result.scalars.return_value.all.return_value = rows
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(rows) for rows in batches])
    return db


def _address() -> Address:
    return Address(
        address_id=1,
        nickname=None,
        type=AddressType.OTHER,
        street='Reforma',
        exterior_number='100',
        interior_number=None,
        postal_code='55620',
        neighborhood='Centro',
        locality=None,
        borough='Cuauhtemoc',
        state='CDMX',
        city=None,
        country='MEX',
        url_address=None,
        comment=None,
        status=EntityStatus.ACTIVE,
    )


def _facility() -> Facility:
    return Facility(
        facility_id=1,
        code='S1',
        name='Main Store',
        type=FacilityType.STORE,
        location='55620',
        address=1,
        taxpayer='RFC123456789A',
        logo=None,
        receipt_message=None,
        default_batch=None,
        status=EntityStatus.ACTIVE,
    )


@pytest.mark.asyncio
async def test_facility_expansion_leaves_mapped_location_intact() -> None:
    facility = _facility()
    db = _db_returning([SatPostalCode(sat_postal_code_id='55620', state='MEX')], [_address()])

    await facility_service._attach_relations(db, [facility])

    # the mapped column still holds the raw FK, so FacilitySummary keeps working
    assert facility.location == '55620'
    assert FacilitySummary.model_validate(facility).location == '55620'
    assert facility.address == 1
    assert FacilitySummary.model_validate(facility).address == 1
    # and the expanded values are reachable for the detail response
    response = FacilityResponse.model_validate(facility)
    assert response.location.id == '55620'
    assert response.address.address_id == 1


@pytest.mark.asyncio
async def test_warehouse_expansion_leaves_mapped_facility_intact() -> None:
    warehouse = Warehouse(
        warehouse_id=1,
        facility=1,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )
    db = _db_returning([_facility()])

    await warehouse_service._attach_relations(db, [warehouse])

    # WarehouseSummary is embedded in point-of-sale responses and reads the raw int
    assert warehouse.facility == 1
    assert WarehouseSummary.model_validate(warehouse).facility == 1
    assert WarehouseResponse.model_validate(warehouse).facility.facility_id == 1


@pytest.mark.asyncio
async def test_expanded_facility_does_not_corrupt_an_embedding_warehouse() -> None:
    """The original failure: one Facility instance reaching both services through the
    identity map, expanded by one and read raw by the other."""
    facility = _facility()

    await facility_service._attach_relations(
        _db_returning([SatPostalCode(sat_postal_code_id='55620', state='MEX')], [_address()]),
        [facility],
    )
    warehouse = Warehouse(
        warehouse_id=1,
        facility=1,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )
    # the very same (already expanded) instance is handed back by the second service
    await warehouse_service._attach_relations(_db_returning([facility]), [warehouse])

    assert WarehouseResponse.model_validate(warehouse).facility.location == '55620'


# ── #104: the same guarantee across the services converted from the clobbering pattern ──


def _employee(employee_id: int = 1) -> Employee:
    return Employee(
        employee_id=employee_id,
        first_name='Ada',
        last_name='Lovelace',
        nickname='ada',
        gender=1,
        birthday=date(1990, 1, 1),
        taxpayer_id=None,
        sales_person=True,
        status=EntityStatus.ACTIVE,
        personal_id=None,
        start_job_date=date(2020, 1, 1),
        enroll_number=None,
        comment=None,
    )


@pytest.mark.asyncio
async def test_cash_drawer_expansion_leaves_mapped_facility_intact() -> None:
    drawer = CashDrawer(
        cash_drawer_id=1,
        facility=1,
        code='CD1',
        name='Front',
        comment=None,
        status=EntityStatus.ACTIVE,
    )

    await cash_drawer_service._attach_relations(_db_returning([_facility()]), [drawer])

    assert drawer.facility == 1
    assert CashDrawerResponse.model_validate(drawer).facility.facility_id == 1


@pytest.mark.asyncio
async def test_payment_method_option_expansion_leaves_mapped_fks_intact() -> None:
    option = PaymentMethodOption(
        payment_method_option_id=1,
        facility=1,
        warehouse=2,
        name='Cash',
        number_of_payments=1,
        display_on_ticket=True,
        payment_method=0,
        commission=Decimal('0'),
        status=EntityStatus.ACTIVE,
    )
    warehouse = Warehouse(
        warehouse_id=2,
        facility=1,
        code='WH1',
        name='Main',
        comment=None,
        status=EntityStatus.ACTIVE,
    )

    await payment_method_option_service._attach_relations(
        _db_returning([_facility()], [warehouse]), [option]
    )

    assert option.facility == 1
    assert option.warehouse == 2
    response = PaymentMethodOptionResponse.model_validate(option)
    assert response.facility.facility_id == 1
    assert response.warehouse is not None and response.warehouse.warehouse_id == 2


@pytest.mark.asyncio
async def test_vehicle_operator_expansion_leaves_mapped_employee_fks_intact() -> None:
    operator = VehicleOperator(
        vehicle_operator_id=1,
        driver=1,
        license_type='A',
        driver_license_number='X1',
        issue_date=date(2020, 1, 1),
        expiration_date=date(2030, 1, 1),
        issuing_location='CDMX',
        creation_time=datetime(2020, 1, 1),
        modification_time=datetime(2020, 1, 1),
        creator=1,
        updater=1,
        status=EntityStatus.ACTIVE,
    )

    await vehicle_operator_service._attach_relations(_db_returning([_employee()]), [operator])

    assert (operator.driver, operator.creator, operator.updater) == (1, 1, 1)
    assert VehicleOperatorResponse.model_validate(operator).driver.employee_id == 1


@pytest.mark.asyncio
async def test_customer_expansion_leaves_mapped_fks_intact() -> None:
    customer = Customer(
        customer_id=1,
        code='C1',
        name='Acme',
        zone=None,
        credit_limit=Decimal('0'),
        credit_days=0,
        price_list=5,
        shipping=False,
        shipping_required_document=False,
        salesperson=1,
        status=EntityStatus.ACTIVE,
        comment=None,
    )
    price_list = PriceList(
        price_list_id=5,
        name='Retail',
        high_profit_margin=Decimal('0.3'),
        low_profit_margin=Decimal('0.1'),
    )

    await customer_service._attach_customer_relations(
        _db_returning([price_list], [_employee()]), [customer]
    )

    assert customer.price_list == 5
    assert customer.salesperson == 1
    response = CustomerResponse.model_validate(customer)
    assert response.price_list.price_list_id == 5
    assert response.salesperson is not None and response.salesperson.employee_id == 1


@pytest.mark.asyncio
async def test_taxpayer_recipient_expansion_leaves_mapped_fks_intact() -> None:
    """The gap the mocked API tests could not see: their fakes carried None here."""
    recipient = TaxpayerRecipient(
        taxpayer_recipient_id='AAA010101AAA',
        name='Acme',
        email='a@example.com',
        postal_code='55620',
        regime='601',
    )

    await taxpayer_recipient_service._attach_relations(
        _db_returning(
            [SatPostalCode(sat_postal_code_id='55620', state='MEX')],
            [SatTaxRegime(sat_tax_regime_id='601', description='General')],
        ),
        [recipient],
    )

    assert recipient.postal_code == '55620'
    assert recipient.regime == '601'
    response = TaxpayerRecipientResponse.model_validate(recipient)
    assert response.postal_code is not None and response.postal_code.id == '55620'
    assert response.regime is not None and response.regime.description == 'General'


@pytest.mark.asyncio
async def test_product_expansion_leaves_mapped_fks_intact() -> None:
    product = Product(
        product_id=1,
        code='P1',
        name='Widget',
        photo=None,
        sku=None,
        brand=None,
        model=None,
        bar_code=None,
        location=None,
        unit_of_measurement='H87',
        key='01010101',
        tax_rate=Decimal('0.16'),
        tax_included=False,
        price_type=0,
        currency=0,
        min_order_qty=1,
        supplier=3,
        stockable=True,
        perishable=False,
        seriable=False,
        purchasable=True,
        salable=True,
        invoiceable=True,
        stock_verification=False,
        status=EntityStatus.ACTIVE,
        comment=None,
    )
    unit = SatUnitOfMeasurement(
        sat_unit_of_measurement_id='H87', name='Pieza', description=None, symbol='pza'
    )
    key = SatProductService(sat_product_service_id='01010101', description='No existe')
    supplier = Supplier(
        supplier_id=3,
        code='S1',
        name='Acme',
        zone=None,
        credit_limit=Decimal('0'),
        credit_days=0,
        comment=None,
    )

    await product_service._attach_product_relations(
        _db_returning([unit], [key], [supplier]), [product]
    )

    assert product.unit_of_measurement == 'H87'
    assert product.key == '01010101'
    assert product.supplier == 3
    response = ProductResponse.model_validate(product)
    assert response.unit_of_measurement.id == 'H87'
    assert response.key is not None and response.key.id == '01010101'
    assert response.supplier is not None and response.supplier.supplier_id == 3
