"""Delivery-document fixtures on top of the integration baseline (spec 026).

ORM rows inserted directly, committed, in the style of `commission_seed`. The baseline supplies
facility 1, warehouse 1 (plus the in-transit warehouse 2), customer 1, contact 1, product 1 and
employee 1.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    CurrencyCode,
    DeliveryOrderStatus,
    EntityStatus,
    FulfillmentType,
    ItineraryStatus,
    Priority,
)
from app.models.core import Address, Contact, Employee, Vehicle, VehicleOperator, Warehouse
from app.models.customer import Customer
from app.models.logistics import (
    DeliveriesItinerary,
    DeliveriesItineraryDetail,
    DeliveriesItineraryStop,
    DeliveryOrder,
    DeliveryOrderDetail,
    DeliveryOrderEvent,
)
from app.models.product import Product
from app.models.sales import CustomerPayment, SalesOrder, SalesOrderDetail, SalesOrderPayment

WHEN = datetime(2026, 8, 1, 10, 0)


async def customer(db: AsyncSession, *, name: str, credit_days: int = 0) -> int:
    row = Customer(
        code=f'K{name[:10]}',
        name=name,
        credit_limit=Decimal('0'),
        credit_days=credit_days,
        price_list=1,
        status=EntityStatus.ACTIVE,
    )
    db.add(row)
    await db.commit()
    return row.customer_id


async def product(db: AsyncSession, *, code: str, name: str, unit: str = 'H87') -> int:
    row = Product(
        code=code,
        name=name,
        unit_of_measurement=unit,
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
    )
    db.add(row)
    await db.commit()
    return row.product_id


async def address(
    db: AsyncSession, *, url_address: str | None = None, nickname: str | None = None
) -> int:
    row = Address(
        nickname=nickname,
        type=1,
        street='Calle Obra',
        exterior_number='45',
        postal_code='06000',
        neighborhood='Colonia Obra',
        borough='Benito Juárez',
        state='CDMX',
        country='MX',
        url_address=url_address,
        status=EntityStatus.ACTIVE,
    )
    db.add(row)
    await db.commit()
    return row.address_id


async def contact(db: AsyncSession, *, mobile: str, name: str = 'Pedro Residente') -> int:
    row = Contact(name=name, mobile=mobile)
    db.add(row)
    await db.commit()
    return row.contact_id


async def employee(db: AsyncSession, *, first_name: str, last_name: str) -> int:
    row = Employee(
        first_name=first_name,
        last_name=last_name,
        nickname=first_name.lower(),
        gender=1,
        birthday=date(1990, 1, 1),
        sales_person=False,
        status=EntityStatus.ACTIVE,
        start_job_date=date(2020, 1, 1),
    )
    db.add(row)
    await db.commit()
    return row.employee_id


async def warehouse(db: AsyncSession, facility: int = 1) -> int:
    count = len((await db.execute(select(Warehouse.warehouse_id))).all())
    row = Warehouse(
        facility=facility,
        code=f'WX{count}',
        name=f'Bodega {count}',
        status=EntityStatus.ACTIVE,
        in_transit=False,
    )
    db.add(row)
    await db.commit()
    return row.warehouse_id


async def sales_order_for(
    db: AsyncSession,
    *,
    terms: int,
    paid: bool,
    due_date: datetime,
    customer: int = 1,
    customer_name: str | None = None,
    price: str = '100',
    quantity: str = '10',
    comment: str | None = None,
    product: int = 1,
    product_code: str = 'P1',
    product_name: str = 'Producto Uno',
) -> tuple[int, int]:
    """A completed order with one tax-excluded line (16% on top); returns (order, line)."""
    order = SalesOrder(
        creator=1,
        updater=1,
        creation_time=WHEN,
        modification_time=WHEN,
        facility=1,
        point_sale=1,
        salesperson=1,
        customer=customer,
        customer_name=customer_name,
        date=WHEN,
        promise_date=WHEN,
        due_date=due_date,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=terms,
        priority=Priority.NORMAL,
        completed=True,
        cancelled=False,
        paid=paid,
        delivered=False,
        serial=None,
    )
    db.add(order)
    await db.flush()
    line = SalesOrderDetail(
        sales_order=order.sales_order_id,
        product=product,
        quantity=Decimal(quantity),
        cost=Decimal('0'),
        price=Decimal(price),
        discount_rate=Decimal('0'),
        tax_rate=Decimal('0.16'),
        product_code=product_code,
        product_name=product_name,
        warehouse=1,
        exchange_rate=Decimal('1'),
        currency=CurrencyCode.MXN,
        tax_included=False,
        comment=comment,
    )
    db.add(line)
    await db.commit()
    return order.sales_order_id, line.sales_order_detail_id


async def payment_on_delivery(
    db: AsyncSession, sales_order_id: int, amount: str, method: int
) -> int:
    """A payment taken with no cash session, and the application standing it on the order."""
    payment = CustomerPayment(
        amount=Decimal(amount),
        method=method,
        date=WHEN,
        cash_session=None,
        customer=1,
        facility=1,
        serial=1,
        creator=1,
        updater=1,
        creation_time=WHEN,
        modification_time=WHEN,
        currency=CurrencyCode.MXN,
        payment_type=0,
    )
    db.add(payment)
    await db.flush()
    db.add(
        SalesOrderPayment(
            sales_order=sales_order_id,
            customer_payment=payment.customer_payment_id,
            amount=Decimal(amount),
            amount_change=Decimal('0'),
            applier=1,
            date=WHEN,
            cancelled=False,
        )
    )
    await db.commit()
    return payment.customer_payment_id


async def delivery_order(
    db: AsyncSession,
    *,
    fulfillment_type: int,
    status: int,
    customer: int = 1,
    ship_to: int | None = None,
    contact: int | None = None,
    lines: list[tuple[int, str, int, int | None]],
    comment: str | None = None,
    updater: int = 1,
    serial: int | None = None,
    when: datetime = WHEN,
) -> int:
    """A delivery order; `lines` are (product, quantity, warehouse, sales_order_detail)."""
    order = DeliveryOrder(
        creator=1,
        updater=updater,
        creation_time=when,
        modification_time=when,
        facility=1,
        serial=serial,
        customer=customer,
        ship_to=ship_to,
        contact=contact,
        date=when,
        priority=Priority.NORMAL,
        comment=comment,
        status=DeliveryOrderStatus(status),
        fulfillment_type=FulfillmentType(fulfillment_type),
    )
    db.add(order)
    await db.flush()
    for product_id, quantity, warehouse_id, detail in lines:
        row = await db.get(Product, product_id)
        db.add(
            DeliveryOrderDetail(
                delivery_order=order.delivery_order_id,
                sales_order_detail=detail,
                product=product_id,
                quantity=Decimal(quantity),
                product_code=row.code,
                product_name=row.name,
                warehouse=warehouse_id,
            )
        )
    await db.commit()
    return order.delivery_order_id


async def pickup_event(db: AsyncSession, delivery_order: int, employee: int) -> None:
    db.add(
        DeliveryOrderEvent(
            delivery_order=delivery_order,
            from_status=DeliveryOrderStatus.READY_FOR_PICKUP,
            to_status=DeliveryOrderStatus.PICKED_UP,
            employee=employee,
            event_time=WHEN,
        )
    )
    await db.commit()


async def itinerary(
    db: AsyncSession,
    *,
    status: int,
    vehicle: bool = True,
    operator: bool = True,
    warehouse: int | None = 1,
    comment: str | None = None,
) -> int:
    vehicle_id = operator_id = None
    if vehicle:
        row = Vehicle(
            license_plate='ABC-123',
            name='Camión',
            nickname='Torton',
            tons_capacity=10,
            status=EntityStatus.ACTIVE,
        )
        db.add(row)
        await db.flush()
        vehicle_id = row.vehicle_id
    if operator:
        driver = await employee(db, first_name='Chofer', last_name='Gomez')
        row = VehicleOperator(
            driver=driver,
            license_type='E',
            driver_license_number='LIC123',
            issue_date=date(2020, 1, 1),
            expiration_date=date(2030, 1, 1),
            issuing_location='CDMX',
            creation_time=WHEN,
            modification_time=WHEN,
            creator=1,
            updater=1,
            status=EntityStatus.ACTIVE,
        )
        db.add(row)
        await db.flush()
        operator_id = row.vehicle_operator_id
    trip = DeliveriesItinerary(
        vehicle=vehicle_id,
        vehicle_operator=operator_id,
        date=date(2026, 8, 2),
        creator=1,
        updater=1,
        creation_time=WHEN,
        modification_time=WHEN,
        comment=comment,
        warehouse=warehouse,
        status=ItineraryStatus(status),
        departure_time=WHEN if status != ItineraryStatus.OPEN else None,
    )
    db.add(trip)
    await db.commit()
    return trip.deliveries_itinerary_id


async def stop(
    db: AsyncSession,
    itinerary: int,
    sequence: int,
    lines: list[tuple[int, str]],
) -> int:
    """A stop with its lines, as (delivery_order_detail, sent_quantity)."""
    row = DeliveriesItineraryStop(deliveries_itinerary=itinerary, sequence=sequence)
    db.add(row)
    await db.flush()
    for detail, sent in lines:
        db.add(
            DeliveriesItineraryDetail(
                deliveries_itinerary_stop=row.deliveries_itinerary_stop_id,
                delivery_order_detail=detail,
                committed_quantity=Decimal(sent),
                sent_quantity=Decimal(sent),
            )
        )
    await db.commit()
    return row.deliveries_itinerary_stop_id


async def detail_ids(db: AsyncSession, delivery_order: int) -> list[int]:
    rows = await db.execute(
        select(DeliveryOrderDetail.delivery_order_detail_id)
        .where(DeliveryOrderDetail.delivery_order == delivery_order)
        .order_by(DeliveryOrderDetail.delivery_order_detail_id)
    )
    return [r[0] for r in rows.all()]
