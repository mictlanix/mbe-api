import re
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import CurrencyCode, PaymentTerms
from app.schemas import CUSTOMER_DISPLAY_NAME_DESCRIPTION, LocalDateTime
from app.schemas.sales_order import DocumentStatus


class SalesQuoteLineCreate(BaseModel):
    product: int
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    # An absolute markup over the base price. The source document's percentage
    # `price_increment_rate` has no column and is a client-side calculation (see Divergences).
    price_adjustment: Decimal = Field(default=Decimal(0))
    #: Omitted means the customer's override for the product, else 0 (spec 023 FR-001).
    discount_rate: Decimal | None = Field(default=None, ge=0, le=1)
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
    date: LocalDateTime | None = None
    due_date: LocalDateTime | None = None
    contact: int | None = None
    ship_to: int | None = None
    comment: str | None = None


class SalesQuoteUpdate(BaseModel):
    customer: int | None = None
    salesperson: int | None = None
    payment_terms: PaymentTerms | None = None
    currency: CurrencyCode | None = None
    due_date: LocalDateTime | None = None
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
    date: LocalDateTime
    due_date: LocalDateTime
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
    #: The customer's own name, joined from `customer` (#213). No `customer_name` override exists
    #: on a quote — the column is not there — so this is a list row's only route to a name, and a
    #: quote list is browsed by customer more than an order list is. `null` only if the customer
    #: row is gone. Same field, same wording, as `SalesOrderSummary` and `CustomerPaymentSummary`.
    customer_display_name: str | None = Field(
        default=None, description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
    )
    salesperson: int
    date: LocalDateTime
    due_date: LocalDateTime
    currency: CurrencyCode
    status: DocumentStatus
    has_expired: bool
    total: Decimal


# One `@`, a dot in the domain, and no whitespace anywhere, which also rules out a CR or LF that
# could smuggle a header. Deliverability is the mail server's call, not this check's (spec 024).
_EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


class QuoteEmailRequest(BaseModel):
    to: list[str] = Field(min_length=1, max_length=10)

    @field_validator('to')
    @classmethod
    def _valid_and_distinct(cls, value: list[str]) -> list[str]:
        """Trimmed, checked, and deduplicated case-insensitively, keeping the first spelling."""
        seen: set[str] = set()
        addresses = []
        for raw in value:
            address = raw.strip()
            if len(address) > 254 or not _EMAIL.match(address):
                raise ValueError(f'Not a valid email address: {raw!r}')
            if address.lower() not in seen:
                seen.add(address.lower())
                addresses.append(address)
        return addresses


class QuoteEmailResponse(BaseModel):
    recipients: list[str]
    sent_at: LocalDateTime

