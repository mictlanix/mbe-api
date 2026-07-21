from decimal import Decimal

from sqlalchemy import Boolean, Column, ForeignKey, Integer, Numeric, SmallInteger, String, Table
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import CurrencyCode, EntityStatus

# Junction table: product ↔ label (no extra columns)
product_label = Table(
    'product_label',
    Base.metadata,
    Column('product', Integer, ForeignKey('product.product_id'), primary_key=True),
    Column('label', Integer, ForeignKey('label.label_id'), primary_key=True),
)


class PriceList(Base):
    __tablename__ = 'price_list'

    price_list_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(250))
    high_profit_margin: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    low_profit_margin: Mapped[Decimal] = mapped_column(Numeric(5, 4))


class Product(Base):
    __tablename__ = 'product'

    product_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(25))
    name: Mapped[str] = mapped_column(String(250))
    photo: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(50))
    brand: Mapped[str | None] = mapped_column(String(100))
    model: Mapped[str | None] = mapped_column(String(100))
    bar_code: Mapped[str | None] = mapped_column(String(13))
    location: Mapped[str | None] = mapped_column(String(50))
    unit_of_measurement: Mapped[str] = mapped_column(
        String(3), ForeignKey('sat_unit_of_measurement.sat_unit_of_measurement_id')
    )
    stockable: Mapped[bool] = mapped_column(Boolean)
    perishable: Mapped[bool] = mapped_column(Boolean)
    seriable: Mapped[bool] = mapped_column(Boolean)
    purchasable: Mapped[bool] = mapped_column(Boolean)
    salable: Mapped[bool] = mapped_column(Boolean)
    invoiceable: Mapped[bool] = mapped_column(Boolean)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6))
    tax_included: Mapped[bool] = mapped_column(Boolean)
    price_type: Mapped[int] = mapped_column(SmallInteger)
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    min_order_qty: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))
    supplier: Mapped[int | None] = mapped_column(Integer, ForeignKey('supplier.supplier_id'))
    key: Mapped[str | None] = mapped_column(
        String(8), ForeignKey('sat_product_service.sat_product_service_id')
    )
    status: Mapped[EntityStatus] = mapped_column(
        Integer, default=EntityStatus.ACTIVE, server_default='0'
    )
    stock_verification: Mapped[bool] = mapped_column(Boolean)


class ProductPrice(Base):
    __tablename__ = 'product_price'

    product_price_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product: Mapped[int] = mapped_column(Integer, ForeignKey('product.product_id'))
    # column name is "list" in DB — Python builtin, aliased here
    price_list: Mapped[int] = mapped_column('list', Integer, ForeignKey('price_list.price_list_id'))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    low_profit: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    high_profit: Mapped[Decimal] = mapped_column(Numeric(20, 6))
