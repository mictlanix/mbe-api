from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Table
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import CurrencyCode

# Junction tables
supplier_address = Table(
    'supplier_address',
    Base.metadata,
    Column('supplier', Integer, ForeignKey('supplier.supplier_id'), primary_key=True),
    Column('address', Integer, ForeignKey('address.address_id'), primary_key=True),
)

supplier_contact = Table(
    'supplier_contact',
    Base.metadata,
    Column('supplier', Integer, ForeignKey('supplier.supplier_id'), primary_key=True),
    Column('contact', Integer, ForeignKey('contact.contact_id'), primary_key=True),
)

supplier_bank_account = Table(
    'supplier_bank_account',
    Base.metadata,
    Column('supplier', Integer, ForeignKey('supplier.supplier_id'), primary_key=True),
    Column('bank_account', Integer, ForeignKey('bank_account.bank_account_id'), primary_key=True),
)


class Supplier(Base):
    __tablename__ = 'supplier'

    supplier_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    zone: Mapped[str | None] = mapped_column(String(250))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    credit_days: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))


class SupplierAgreement(Base):
    __tablename__ = 'supplier_agreement'

    supplier_agreement_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier: Mapped[int] = mapped_column(Integer, ForeignKey('supplier.supplier_id'))
    start: Mapped[str] = mapped_column(String(10))  # date stored as string in MariaDB date type
    end: Mapped[str] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(String(500))


class SupplierPayment(Base):
    __tablename__ = 'supplier_payment'

    supplier_payment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier: Mapped[int] = mapped_column(Integer, ForeignKey('supplier.supplier_id'))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    method: Mapped[int] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime)
    reference: Mapped[str | None] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(String(500))
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))


class SupplierReturn(Base):
    __tablename__ = 'supplier_return'

    supplier_return_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order: Mapped[int] = mapped_column(
        Integer, ForeignKey('purchase_order.purchase_order_id')
    )
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    supplier: Mapped[int] = mapped_column(Integer, ForeignKey('supplier.supplier_id'))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)


class SupplierReturnDetail(Base):
    __tablename__ = 'supplier_return_detail'

    supplier_return_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier_return: Mapped[int] = mapped_column(
        Integer, ForeignKey('supplier_return.supplier_return_id')
    )
    purchase_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey('purchase_order_detail.purchase_order_detail_id')
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey('product.product_id'))
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey('warehouse.warehouse_id'))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str] = mapped_column(String(25))
    product_name: Mapped[str] = mapped_column(String(250))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    discount: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
