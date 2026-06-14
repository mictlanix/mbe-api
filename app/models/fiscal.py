from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, LargeBinary, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import CurrencyCode


class TaxpayerIssuer(Base):
    __tablename__ = "taxpayer_issuer"

    taxpayer_issuer_id: Mapped[str] = mapped_column(String(13), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(250))
    regime: Mapped[str] = mapped_column(String(3), ForeignKey("sat_tax_regime.sat_tax_regime_id"))
    provider: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str | None] = mapped_column(String(500))
    postal_code: Mapped[str | None] = mapped_column(
        String(5), ForeignKey("sat_postal_code.sat_postal_code_id")
    )


class TaxpayerCertificate(Base):
    __tablename__ = "taxpayer_certificate"

    taxpayer_certificate_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    taxpayer: Mapped[str] = mapped_column(String(13), ForeignKey("taxpayer_issuer.taxpayer_issuer_id"))
    certificate_data: Mapped[bytes] = mapped_column(LargeBinary)
    key_data: Mapped[bytes] = mapped_column(LargeBinary)
    key_password: Mapped[bytes] = mapped_column(LargeBinary)
    valid_from: Mapped[datetime] = mapped_column(DateTime)
    valid_to: Mapped[datetime] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean)


class TaxpayerBatch(Base):
    __tablename__ = "taxpayer_batch"

    taxpayer_batch_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    taxpayer: Mapped[str] = mapped_column(String(13), ForeignKey("taxpayer_issuer.taxpayer_issuer_id"))
    batch: Mapped[str] = mapped_column(String(10))
    type: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(Text)


class FiscalDocument(Base):
    __tablename__ = "fiscal_document"

    fiscal_document_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey("employee.employee_id"))
    issuer: Mapped[str] = mapped_column(String(13), ForeignKey("taxpayer_issuer.taxpayer_issuer_id"))
    issuer_name: Mapped[str | None] = mapped_column(String(250))
    issuer_regime: Mapped[str | None] = mapped_column(String(3))
    issuer_regime_name: Mapped[str | None] = mapped_column(String(250))
    issuer_address: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    customer: Mapped[int] = mapped_column(Integer, ForeignKey("customer.customer_id"))
    recipient: Mapped[str] = mapped_column(String(13))
    recipient_name: Mapped[str | None] = mapped_column(String(250))
    recipient_address: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    type: Mapped[int] = mapped_column(Integer)
    store: Mapped[int] = mapped_column(Integer, ForeignKey("store.store_id"))
    batch: Mapped[str | None] = mapped_column(String(10))
    serial: Mapped[int | None] = mapped_column(Integer)
    issued: Mapped[datetime | None] = mapped_column(DateTime)
    issued_at: Mapped[int | None] = mapped_column(Integer, ForeignKey("address.address_id"))
    issued_location: Mapped[str] = mapped_column(String(250))
    # bit(1) in DB
    completed: Mapped[bool] = mapped_column(Boolean)
    cancelled: Mapped[bool] = mapped_column(Boolean)
    cancellation_date: Mapped[datetime | None] = mapped_column(DateTime)
    reference: Mapped[str | None] = mapped_column(String(25))
    payment_method: Mapped[int] = mapped_column(Integer)
    payment_reference: Mapped[str | None] = mapped_column(String(50))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    payment_terms: Mapped[int] = mapped_column(Integer)
    usage: Mapped[str | None] = mapped_column(String(3), ForeignKey("sat_cfdi_usage.sat_cfdi_usage_id"))
    comment: Mapped[str | None] = mapped_column(String(1000))
    stamped: Mapped[datetime | None] = mapped_column(DateTime)
    stamp_uuid: Mapped[str | None] = mapped_column(String(36))
    authority_digital_seal: Mapped[str | None] = mapped_column(String(500))
    authority_certificate_number: Mapped[str | None] = mapped_column(String(20))
    version: Mapped[Decimal] = mapped_column(Numeric(3, 1))
    provider: Mapped[int] = mapped_column(Integer)
    retention_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    local_retention_name: Mapped[str | None] = mapped_column(String(32))
    local_retention_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    cancellation_reason: Mapped[str | None] = mapped_column(String(250))
    cancellation_substitution: Mapped[str | None] = mapped_column(String(250))
    taxpayer_regime: Mapped[str | None] = mapped_column(String(3))
    taxpayer_postal_code: Mapped[str | None] = mapped_column(String(5))
    rfc_pac: Mapped[str | None] = mapped_column(String(13))


class FiscalDocumentDetail(Base):
    __tablename__ = "fiscal_document_detail"

    fiscal_document_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[int] = mapped_column(Integer, ForeignKey("fiscal_document.fiscal_document_id"))
    product: Mapped[int] = mapped_column(Integer, ForeignKey("product.product_id"))
    order_detail: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("sales_order_detail.sales_order_detail_id")
    )
    product_service: Mapped[str | None] = mapped_column(
        String(8), ForeignKey("sat_product_service.sat_product_service_id")
    )
    product_code: Mapped[str | None] = mapped_column(String(35))
    product_name: Mapped[str] = mapped_column(String(1000))
    unit_of_measurement: Mapped[str | None] = mapped_column(
        String(3), ForeignKey("sat_unit_of_measurement.sat_unit_of_measurement_id")
    )
    unit_of_measurement_name: Mapped[str | None] = mapped_column(String(128))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    price: Mapped[Decimal] = mapped_column(Numeric(18, 7))
    discount: Mapped[Decimal] = mapped_column(Numeric(9, 8))
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(7, 6))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    currency: Mapped[CurrencyCode] = mapped_column(Integer)
    tax_included: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(String(1000))


class FiscalDocumentRelation(Base):
    __tablename__ = "fiscal_document_relation"

    fiscal_document_relation_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[int] = mapped_column(Integer, ForeignKey("fiscal_document.fiscal_document_id"))
    relation: Mapped[int] = mapped_column(Integer, ForeignKey("fiscal_document.fiscal_document_id"))
    exchange_rate: Mapped[Decimal] = mapped_column(Numeric(8, 4))
    installment: Mapped[int] = mapped_column(Integer)
    previous_balance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    type: Mapped[str | None] = mapped_column(String(3))


class FiscalDocumentXml(Base):
    """Stored XML blob for a stamped CFDI (PK is also FK to fiscal_document)."""

    __tablename__ = "fiscal_document_xml"

    fiscal_document_xml_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("fiscal_document.fiscal_document_id"), primary_key=True
    )
    data: Mapped[str] = mapped_column(Text)
