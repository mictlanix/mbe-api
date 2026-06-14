from decimal import Decimal

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, String, Table
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Junction tables
customer_address = Table(
    "customer_address",
    Base.metadata,
    Column("customer", Integer, ForeignKey("customer.customer_id"), primary_key=True),
    Column("address", Integer, ForeignKey("address.address_id"), primary_key=True),
)

customer_contact = Table(
    "customer_contact",
    Base.metadata,
    Column("customer", Integer, ForeignKey("customer.customer_id"), primary_key=True),
    Column("contact", Integer, ForeignKey("contact.contact_id"), primary_key=True),
)

customer_taxpayer = Table(
    "customer_taxpayer",
    Base.metadata,
    Column("customer", Integer, ForeignKey("customer.customer_id"), primary_key=True),
    Column(
        "taxpayer_recipient",
        String(13),
        ForeignKey("taxpayer_recipient.taxpayer_recipient_id"),
        primary_key=True,
    ),
)


class TaxpayerRecipient(Base):
    __tablename__ = "taxpayer_recipient"

    taxpayer_recipient_id: Mapped[str] = mapped_column(String(13), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(250))
    email: Mapped[str] = mapped_column(String(80))
    postal_code: Mapped[str | None] = mapped_column(String(5))
    regime: Mapped[str | None] = mapped_column(
        String(3), ForeignKey("sat_tax_regime.sat_tax_regime_id")
    )


class Customer(Base):
    __tablename__ = "customer"

    customer_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    zone: Mapped[str | None] = mapped_column(String(250))
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    credit_days: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(1024))
    price_list: Mapped[int] = mapped_column(Integer, ForeignKey("price_list.price_list_id"))
    shipping: Mapped[bool] = mapped_column(Boolean)
    shipping_required_document: Mapped[bool] = mapped_column(Boolean)
    salesperson: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    disabled: Mapped[bool | None] = mapped_column(Boolean)
    creator: Mapped[int | None] = mapped_column(Integer, ForeignKey("employee.employee_id"))


class CustomerDiscount(Base):
    __tablename__ = "customer_discount"

    customer_discount_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    discount: Mapped[Decimal] = mapped_column(Numeric(9, 8))
