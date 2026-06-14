from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import CurrencyCode


class SalesQuote(Base):
    __tablename__ = "sales_quote"

    sales_quote_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[datetime] = mapped_column(DateTime)
    salesperson: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    payment_terms: Mapped[int] = mapped_column(SmallInteger)
    due_date: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    contact: Mapped[int | None] = mapped_column(Integer, ForeignKey("contact.contact_id"))
    ship_to: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    comment: Mapped[str | None] = mapped_column(String(1024))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))


class SalesQuoteDetail(Base):
    __tablename__ = "sales_quote_detail"

    sales_quote_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_quote: Mapped[int] = mapped_column(Integer, ForeignKey("sales_quote.sales_quote_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 7))
    price_adjustment: Mapped[Decimal] = mapped_column(Numeric(18, 7))
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    product_code: Mapped[str] = mapped_column(String(25))
    product_name: Mapped[str] = mapped_column(String(250))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(1024))


class SalesOrder(Base):
    __tablename__ = "sales_order"

    sales_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    point_sale: Mapped[int] = mapped_column(Integer, ForeignKey("point_sale.point_sale_id"))
    salesperson: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    sales_quote: Mapped[int | None] = mapped_column(Integer, ForeignKey("sales_quote.sales_quote_id"))
    payment_terms: Mapped[int] = mapped_column(SmallInteger)
    date: Mapped[datetime] = mapped_column(DateTime)
    promise_date: Mapped[datetime] = mapped_column(DateTime)
    recipient: Mapped[str | None] = mapped_column(String(13))
    recipient_name: Mapped[str | None] = mapped_column(String(250))
    recipient_address: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    due_date: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    paid: Mapped[bool] = mapped_column(Boolean)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    balance_zeroed_time: Mapped[datetime | None] = mapped_column(DateTime)
    contact: Mapped[int | None] = mapped_column(Integer, ForeignKey("contact.contact_id"))
    ship_to: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    delivered: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    customer_name: Mapped[str | None] = mapped_column(String(100))
    customer_shipto: Mapped[str | None] = mapped_column(String(200))
    priority: Mapped[int] = mapped_column(SmallInteger)
    partial_deliveries: Mapped[int | None] = mapped_column(SmallInteger)


class SalesOrderDetail(Base):
    __tablename__ = "sales_order_detail"

    sales_order_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey("sales_order.sales_order_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 7))
    discount_rate: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    product_code: Mapped[str] = mapped_column(String(25))
    product_name: Mapped[str] = mapped_column(String(250))
    delivery: Mapped[bool] = mapped_column(Boolean)
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(500))


class CustomerPayment(Base):
    __tablename__ = "customer_payment"

    customer_payment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    method: Mapped[int] = mapped_column(Integer)
    commission: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    payment_charge: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("payment_method_option.payment_method_option_id")
    )
    date: Mapped[datetime] = mapped_column(DateTime)
    cash_session: Mapped[int | None] = mapped_column(Integer, ForeignKey("cash_session.cash_session_id"))
    reference: Mapped[str | None] = mapped_column(String(50))
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    serial: Mapped[int] = mapped_column(Integer)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    verifier: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    payment_type: Mapped[int] = mapped_column(SmallInteger)


class SalesOrderPayment(Base):
    __tablename__ = "sales_order_payment"

    sales_order_payment_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey("sales_order.sales_order_id"))
    customer_payment: Mapped[int] = mapped_column(Integer, ForeignKey("customer_payment.customer_payment_id"))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    amount_change: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    applier: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    date: Mapped[datetime | None] = mapped_column(DateTime)
    confirmed: Mapped[bool | None] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)


class CustomerRefund(Base):
    __tablename__ = "customer_refund"

    customer_refund_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey("sales_order.sales_order_id"))
    customer: Mapped[int | None] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    sales_person: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    serial: Mapped[int | None] = mapped_column(Integer)
    date: Mapped[datetime | None] = mapped_column(DateTime)
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))


class CustomerRefundDetail(Base):
    __tablename__ = "customer_refund_detail"

    customer_refund_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_refund: Mapped[int] = mapped_column(Integer, ForeignKey("customer_refund.customer_refund_id"))
    sales_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey("sales_order_detail.sales_order_detail_id")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str] = mapped_column(String(25))
    product_name: Mapped[str] = mapped_column(String(250))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    discount: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey("warehouse.warehouse_id"))


class CreditNote(Base):
    __tablename__ = "credit_note"

    credit_note_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sales_order: Mapped[int] = mapped_column(Integer, ForeignKey("sales_order.sales_order_id"))
    customer_refund: Mapped[int] = mapped_column(Integer, ForeignKey("customer_refund.customer_refund_id"))
    customer_payment: Mapped[int] = mapped_column(Integer, ForeignKey("customer_payment.customer_payment_id"))
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    refunded: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    cash_session: Mapped[int | None] = mapped_column(Integer, ForeignKey("cash_session.cash_session_id"))
    date: Mapped[datetime | None] = mapped_column(DateTime)


class PaymentOnDelivery(Base):
    __tablename__ = "payment_on_delivery"

    payment_on_delivery_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer_payment: Mapped[int] = mapped_column(Integer, ForeignKey("customer_payment.customer_payment_id"))
    cash_session: Mapped[int | None] = mapped_column(Integer, ForeignKey("cash_session.cash_session_id"))
    paid: Mapped[bool] = mapped_column(Boolean)
    date: Mapped[datetime] = mapped_column(DateTime)
