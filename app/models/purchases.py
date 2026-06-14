from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import CurrencyCode


class PurchaseRequest(Base):
    __tablename__ = "purchase_request"

    purchase_request_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    comment: Mapped[str | None] = mapped_column(String(500))
    serial: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool | None] = mapped_column(Boolean)
    cancelled: Mapped[bool | None] = mapped_column(Boolean)
    approved: Mapped[bool] = mapped_column(Boolean)


class PurchaseRequestDetail(Base):
    __tablename__ = "purchase_request_detail"

    purchase_request_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_request: Mapped[int] = mapped_column(
        Integer, ForeignKey("purchase_request.purchase_request_id")
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    product_name: Mapped[str | None] = mapped_column(String(250))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    customer: Mapped[int | None] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    to_purchase: Mapped[bool] = mapped_column(Boolean)
    # column has mixed-case name in DB: "Accepted"
    accepted: Mapped[bool] = mapped_column("Accepted", Boolean)


class PurchaseOrder(Base):
    __tablename__ = "purchase_order"

    purchase_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplier: Mapped[int | None] = mapped_column(Integer, ForeignKey("supplier.supplier_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    approved: Mapped[bool] = mapped_column(Boolean)
    estimated_receipt_date: Mapped[datetime | None] = mapped_column(DateTime)
    invoice_number: Mapped[str | None] = mapped_column(String(50))
    comment: Mapped[str | None] = mapped_column(String(500))
    approver: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))


class PurchaseOrderDetail(Base):
    __tablename__ = "purchase_order_detail"

    purchase_order_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purchase_order: Mapped[int] = mapped_column(Integer, ForeignKey("purchase_order.purchase_order_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 7))
    discount: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    product_code: Mapped[str] = mapped_column(String(25))
    product_name: Mapped[str] = mapped_column(String(250))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
    purchase_request_detail: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("purchase_request_detail.purchase_request_detail_id")
    )


class ExpenseVoucher(Base):
    __tablename__ = "expense_voucher"

    expense_voucher_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    cash_session: Mapped[int] = mapped_column(Integer, ForeignKey("cash_session.cash_session_id"))
    comment: Mapped[str | None] = mapped_column(String(500))
    date: Mapped[datetime] = mapped_column(DateTime)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool | None] = mapped_column(Boolean)
    cancelled: Mapped[bool | None] = mapped_column(Boolean)


class ExpenseVoucherDetail(Base):
    __tablename__ = "expense_voucher_detail"

    expense_voucher_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    expense_voucher: Mapped[int] = mapped_column(Integer, ForeignKey("expense_voucher.expense_voucher_id"))
    expense: Mapped[int] = mapped_column(Integer, ForeignKey("expenses.expense_id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    comment: Mapped[str | None] = mapped_column(String(500))
