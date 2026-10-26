"""Inventory-document fixtures on top of the integration baseline (spec 029, extended by spec 030).

ORM rows inserted directly, committed, in the style of `delivery_seed`. The baseline supplies
facility 1 with warehouse 1 and its in-transit warehouse 2, product 1 and employee 1;
`setup_inventory` adds a second facility with its own pair, a stockable product and a
non-stockable one.

Stock is posted under `INVENTORY_ADJUSTMENT`, never under the receipt, issue or transfer types: a
cancel reverses every entry with its own type and id (research R3), so seeded stock must not look
like something a document posted.
"""

from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    CurrencyCode,
    EntityStatus,
    FacilityType,
    PaymentTerms,
    Priority,
    TransactionType,
)
from app.models.core import Facility, Label, Warehouse
from app.models.inventory import InventoryCount, InventoryCountDetail
from app.models.product import Product, product_label
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail
from app.models.sales import SalesOrder, SalesOrderDetail
from app.services import stock_ledger
from tests.integration.seed import ISSUER_RFC, POSTAL_CODE

WHEN = datetime(2026, 10, 1, 10, 0)

#: `(product, quantity, warehouse)`, the shape of every factory's lines.
Line = tuple[int, str, int]


class Site(NamedTuple):
    facility: int
    warehouse: int
    transit: int


class Inventory(NamedTuple):
    a: Site
    b: Site
    stockable: int
    non_stockable: int


async def warehouse(db: AsyncSession, *, facility: int, code: str, in_transit: bool = False) -> int:
    row = Warehouse(
        facility=facility,
        code=code,
        name=f'Almacén {code}',
        status=EntityStatus.ACTIVE,
        in_transit=in_transit,
    )
    db.add(row)
    await db.commit()
    return row.warehouse_id


async def product(db: AsyncSession, *, code: str, stockable: bool = True) -> int:
    row = Product(
        code=code,
        name=f'Producto {code}',
        unit_of_measurement='H87',
        stockable=stockable,
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
        stock_verification=stockable,
    )
    db.add(row)
    await db.commit()
    return row.product_id


async def labelled_product(db: AsyncSession, *, code: str, labels: int) -> int:
    """A stockable product carrying `labels` labels, so a join on labels would multiply it."""
    product_id = await product(db, code=code)
    for n in range(labels):
        label = Label(name=f'{code} {n + 1}')
        db.add(label)
        await db.flush()
        await db.execute(insert(product_label).values(product=product_id, label=label.label_id))
    await db.commit()
    return product_id


async def setup_inventory(db: AsyncSession) -> Inventory:
    """Facility 1 (baseline) and a second facility, each with a warehouse and its transit."""
    facility = Facility(
        code='F2',
        name='Sucursal',
        type=FacilityType.STORE,
        location=POSTAL_CODE,
        address=1,
        taxpayer=ISSUER_RFC,
        status=EntityStatus.ACTIVE,
    )
    db.add(facility)
    await db.commit()
    b = Site(
        facility=facility.facility_id,
        warehouse=await warehouse(db, facility=facility.facility_id, code='W2'),
        transit=await warehouse(db, facility=facility.facility_id, code='WT2', in_transit=True),
    )
    return Inventory(
        a=Site(facility=1, warehouse=1, transit=2),
        b=b,
        stockable=await product(db, code='INV'),
        non_stockable=await product(db, code='SRV', stockable=False),
    )


async def stock(db: AsyncSession, product: int, warehouse: int, qty: str) -> None:
    """Put `qty` on hand (or take it, when negative) with one ledger entry."""
    quantity = Decimal(qty)
    stock_ledger.post_movement(
        db,
        source=TransactionType.INVENTORY_ADJUSTMENT,
        reference=0,
        product=product,
        warehouse=warehouse,
        quantity=abs(quantity),
        outbound=quantity < 0,
    )
    await db.commit()


async def reserve(db: AsyncSession, product: int, warehouse: int, qty: str) -> int:
    """Claim `qty` for a completed sales order of that line; returns the order's id."""
    order = await sales_order(db, lines=[(product, qty, warehouse)])
    stock_ledger.reserve(
        db, sales_order=order, product=product, warehouse=warehouse, quantity=Decimal(qty)
    )
    await db.commit()
    return order


async def purchase_order(
    db: AsyncSession, *, lines: list[Line], completed: bool = True, cancelled: bool = False
) -> tuple[int, list[int]]:
    """A purchase order with one line per entry; returns (order, line ids in entry order)."""
    order = PurchaseOrder(
        creation_time=WHEN,
        modification_time=WHEN,
        creator=1,
        updater=1,
        completed=completed,
        cancelled=cancelled,
        approved=completed,
    )
    db.add(order)
    await db.flush()
    details = []
    for product_id, quantity, warehouse_id in lines:
        row = await db.get(Product, product_id)
        detail = PurchaseOrderDetail(
            purchase_order=order.purchase_order_id,
            product=product_id,
            warehouse=warehouse_id,
            quantity=Decimal(quantity),
            price=Decimal('10'),
            discount=Decimal('0'),
            tax_rate=Decimal('0.16'),
            product_code=row.code,
            product_name=row.name,
            exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN,
            tax_included=False,
        )
        db.add(detail)
        details.append(detail)
    await db.commit()
    return order.purchase_order_id, [d.purchase_order_detail_id for d in details]


async def sales_order(
    db: AsyncSession, *, lines: list[Line], completed: bool = True, cancelled: bool = False
) -> int:
    """A sales order of facility 1 with one line per entry; returns its id."""
    order = SalesOrder(
        creator=1,
        updater=1,
        creation_time=WHEN,
        modification_time=WHEN,
        facility=1,
        point_sale=1,
        salesperson=1,
        customer=1,
        date=WHEN,
        promise_date=WHEN,
        due_date=WHEN,
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal('1'),
        payment_terms=PaymentTerms.IMMEDIATE,
        priority=Priority.NORMAL,
        completed=completed,
        cancelled=cancelled,
        paid=False,
        delivered=False,
        serial=None,
    )
    db.add(order)
    await db.flush()
    for product_id, quantity, warehouse_id in lines:
        row = await db.get(Product, product_id)
        db.add(
            SalesOrderDetail(
                sales_order=order.sales_order_id,
                product=product_id,
                quantity=Decimal(quantity),
                cost=Decimal('0'),
                price=Decimal('100'),
                discount_rate=Decimal('0'),
                tax_rate=Decimal('0.16'),
                product_code=row.code,
                product_name=row.name,
                warehouse=warehouse_id,
                exchange_rate=Decimal('1'),
                currency=CurrencyCode.MXN,
                tax_included=False,
            )
        )
    await db.commit()
    return order.sales_order_id


async def count(
    db: AsyncSession, warehouse: int, lines: list[tuple[int, str]], *, creator: int = 1
) -> int:
    """A draft count of `warehouse` with one line per `(product, quantity)`; returns its id."""
    row = await db.get(Warehouse, warehouse)
    document = InventoryCount(
        facility=row.facility,
        warehouse=warehouse,
        creator=creator,
        updater=creator,
        creation_time=WHEN,
        modification_time=WHEN,
        completed=False,
        cancelled=False,
    )
    db.add(document)
    await db.flush()
    for product_id, quantity in lines:
        item = await db.get(Product, product_id)
        db.add(
            InventoryCountDetail(
                count_id=document.inventory_count_id,
                product=product_id,
                product_code=item.code,
                product_name=item.name,
                quantity=Decimal(quantity),
            )
        )
    await db.commit()
    return document.inventory_count_id
