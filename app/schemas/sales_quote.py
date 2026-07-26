from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import CurrencyCode, PaymentTerms
from app.schemas.sales_order import DocumentStatus


class SalesQuoteLineCreate(BaseModel):
    product: int
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    # An absolute markup over the base price. The source document's percentage
    # `price_increment_rate` has no column and is a client-side calculation (see Divergences).
    price_adjustment: Decimal = Field(default=Decimal(0))
    discount_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    comment: str | None = None


class SalesQuoteLineUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    price_adjustment: Decimal | None = None
    discount_rate: Decimal | None = Field(default=None, ge=0, le=1)
    comment: str | None = None


class SalesQuoteLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_quote_detail_id: int
    product: int
    product_code: str
    product_name: str
    quantity: Decimal
    price: Decimal
    price_adjustment: Decimal
    discount_rate: Decimal
    tax_rate: Decimal
    tax_included: bool
    currency: CurrencyCode
    exchange_rate: Decimal
    comment: str | None
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


class SalesQuoteCreate(BaseModel):
    customer: int | None = None
    salesperson: int | None = None
    payment_terms: PaymentTerms | None = None
    currency: CurrencyCode | None = None
    date: datetime | None = None
    due_date: datetime | None = None
    contact: int | None = None
    ship_to: int | None = None
    comment: str | None = None


class SalesQuoteUpdate(BaseModel):
    customer: int | None = None
    salesperson: int | None = None
    payment_terms: PaymentTerms | None = None
    currency: CurrencyCode | None = None
    due_date: datetime | None = None
    contact: int | None = None
    ship_to: int | None = None
    comment: str | None = None


class SalesQuoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_quote_id: int
    facility: int
    serial: int | None
    salesperson: int
    customer: int
    payment_terms: PaymentTerms
    date: datetime
    due_date: datetime
    contact: int | None
    ship_to: int | None
    currency: CurrencyCode
    exchange_rate: Decimal
    comment: str | None
    status: DocumentStatus
    has_expired: bool
    lines: list[SalesQuoteLineResponse] = []
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


class SalesQuoteSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_quote_id: int
    serial: int | None
    customer: int
    salesperson: int
    date: datetime
    due_date: datetime
    currency: CurrencyCode
    status: DocumentStatus
    has_expired: bool
    total: Decimal
