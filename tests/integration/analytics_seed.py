"""Documents for the analytics tests (spec 037): confirmed sales, refunds, credit notes, payments.

Built straight from the models, so a test states the exact lines it needs and the facts read them
as the confirm paths would have left them. The baseline's customer 1 is the walk-in customer
(`settings.default_customer_id`), so it is a cash customer whatever its limit; customer 2 is a
credit customer.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import CurrencyCode, EntityStatus, PaymentTerms, PaymentType, Priority
from app.models.core import CashSession, Label
from app.models.customer import Customer
from app.models.fiscal import FiscalDocumentDetail
from app.models.product import Product, product_label
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from tests.integration.seed import UNIT, seed_fiscal_document

CREDIT_CUSTOMER = 2


@dataclass(frozen=True)
class L:
    """One line: quantity, price (VAT included unless `included=False`), cost, discount rate."""

    qty: str
    price: str
    cost: str = '0'
    discount: str = '0'
    rate: str = '0.16'
    included: bool = True
    product: int = 1


async def seed_catalog(db: AsyncSession) -> None:
    """A credit customer, two more products with brands, and labels (product 2 carries two)."""
    db.add(
        Customer(
            customer_id=CREDIT_CUSTOMER, code='C2', name='Constructora Dos',
            credit_limit=Decimal('50000'), credit_days=30, price_list=1,
            status=EntityStatus.ACTIVE,
        )
    )  # fmt: skip
    for product_id, code, brand, model in (
        (2, 'P2', ' Cemex ', 'Gris 50'),
        (3, 'P3', 'cemex', None),
    ):
        db.add(
            Product(
                product_id=product_id, code=code, name=f'Producto {code}', photo='x.png',
                unit_of_measurement=UNIT, stockable=False, perishable=False, seriable=False,
                purchasable=True, salable=True, invoiceable=True, tax_rate=Decimal('0.16'),
                tax_included=True, price_type=0, currency=CurrencyCode.MXN, min_order_qty=1,
                status=EntityStatus.ACTIVE, stock_verification=False, brand=brand, model=model,
            )
        )  # fmt: skip
    db.add_all([Label(label_id=1, name='Cementos'), Label(label_id=2, name='Obra negra')])
    await db.flush()
    await db.execute(
        insert(product_label),
        [{'product': 2, 'label': 1}, {'product': 2, 'label': 2}, {'product': 3, 'label': 1}],
    )
    await db.commit()


async def sale(
    db: AsyncSession,
    at: datetime,
    lines: list[L],
    *,
    facility: int = 1,
    customer: int = 1,
    salesperson: int = 1,
    terms: PaymentTerms = PaymentTerms.IMMEDIATE,
    cancelled: bool = False,
) -> tuple[int, list[int]]:
    """A confirmed sale (cancelled afterwards when asked); its id and its line ids."""
    order = SalesOrder(
        creator=1, updater=1, creation_time=at, modification_time=at, facility=facility,
        point_sale=1, salesperson=salesperson, customer=customer, date=at, confirmation_time=at,
        promise_date=at, due_date=at, currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
        payment_terms=terms, priority=Priority.NORMAL, completed=True, cancelled=cancelled,
        paid=False, delivered=False,
    )  # fmt: skip
    db.add(order)
    await db.flush()
    details = [
        SalesOrderDetail(
            sales_order=order.sales_order_id, product=line.product, quantity=Decimal(line.qty),
            cost=Decimal(line.cost), price=Decimal(line.price),
            discount_rate=Decimal(line.discount), tax_rate=Decimal(line.rate),
            product_code=f'P{line.product}', product_name='Producto', warehouse=1,
            exchange_rate=Decimal('1'), currency=CurrencyCode.MXN, tax_included=line.included,
        )
        for line in lines
    ]  # fmt: skip
    db.add_all(details)
    await db.commit()
    return order.sales_order_id, [d.sales_order_detail_id for d in details]


async def refund(
    db: AsyncSession,
    order: int,
    returned: list[tuple[int, str, L]],
    at: datetime,
    *,
    facility: int = 1,
    salesperson: int = 1,
    customer: int | None = 1,
) -> int:
    """A confirmed refund of `(sale line, quantity, the line's pricing)` items."""
    row = CustomerRefund(
        sales_order=order, customer=customer, creator=1, updater=1, sales_person=salesperson,
        creation_time=at, modification_time=at, completed=True, cancelled=False,
        facility=facility, date=at, currency=CurrencyCode.MXN, exchange_rate=Decimal('1'),
    )  # fmt: skip
    db.add(row)
    await db.flush()
    db.add_all(
        CustomerRefundDetail(
            customer_refund=row.customer_refund_id, sales_order_detail=line_id,
            quantity=Decimal(qty), product=line.product, price=Decimal(line.price),
            product_code='P', product_name='P', tax_rate=Decimal(line.rate),
            discount=Decimal(line.discount), exchange_rate=Decimal('1'),
            currency=CurrencyCode.MXN, tax_included=line.included,
        )
        for line_id, qty, line in returned
    )  # fmt: skip
    await db.commit()
    return row.customer_refund_id


async def credit_note(
    db: AsyncSession,
    at: datetime,
    lines: list[L],
    *,
    serial: int,
    facility: int = 1,
    cancelled: bool = False,
) -> int:
    """A stamped CFDI credit note (type 100) issued at `at` with these lines."""
    note = await seed_fiscal_document(
        db, 'credit_note', serial=serial, issued=at, facility=facility, cancelled=cancelled
    )
    await db.execute(delete(FiscalDocumentDetail).where(FiscalDocumentDetail.document == note))
    db.add_all(
        FiscalDocumentDetail(
            document=note, product=line.product, product_service='01010101',
            product_name='Producto', unit_of_measurement=UNIT, quantity=Decimal(line.qty),
            price=Decimal(line.price), discount=Decimal(line.discount),
            tax_rate=Decimal(line.rate), exchange_rate=Decimal('1'), currency=CurrencyCode.MXN,
            tax_included=line.included,
        )
        for line in lines
    )  # fmt: skip
    await db.commit()
    return note


async def cash_session(db: AsyncSession, at: datetime, *, cashier: int = 1) -> int:
    session = CashSession(start=at, end=at, cashier=cashier, cash_drawer=1)
    db.add(session)
    await db.commit()
    return session.cash_session_id


_serial = iter(range(5000, 100000))


async def payment(
    db: AsyncSession,
    at: datetime,
    amount: str,
    *,
    method: int = 1,
    kind: PaymentType = PaymentType.IMMEDIATE,
    session: int | None = None,
    facility: int = 1,
    customer: int = 1,
    applied_to: int | None = None,
    change: str = '0',
) -> int:
    """A customer payment, applied to `applied_to` with `change` handed back when given."""
    row = CustomerPayment(
        amount=Decimal(amount), method=method, date=at, cash_session=session, customer=customer,
        facility=facility, serial=next(_serial), creator=1, updater=1, creation_time=at,
        modification_time=at, currency=CurrencyCode.MXN, payment_type=kind,
    )  # fmt: skip
    db.add(row)
    await db.flush()
    if applied_to is not None:
        db.add(
            SalesOrderPayment(
                sales_order=applied_to, customer_payment=row.customer_payment_id,
                amount=Decimal(amount) - Decimal(change), amount_change=Decimal(change),
                applier=1, date=at, confirmed=True, cancelled=False,
            )
        )  # fmt: skip
    await db.commit()
    return row.customer_payment_id
