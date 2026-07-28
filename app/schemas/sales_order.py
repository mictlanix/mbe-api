from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.enums import CurrencyCode, PaymentTerms, Priority

# ── Lifecycle ─────────────────────────────────────────────────────────────────


class DocumentStatus(StrEnum):
    """One lifecycle state, derived from the completed/cancelled/paid flags.

    Clients get a single state rather than three raw booleans they would have to combine
    themselves — and combine identically everywhere, or disagree about what an order is.
    """

    DRAFT = 'draft'
    COMPLETED = 'completed'
    PAID = 'paid'
    CANCELLED = 'cancelled'


def derive_status(*, completed: bool, cancelled: bool, paid: bool = False) -> DocumentStatus:
    if cancelled:
        return DocumentStatus.CANCELLED
    if paid:
        return DocumentStatus.PAID
    if completed:
        return DocumentStatus.COMPLETED
    return DocumentStatus.DRAFT


# ── Lines ─────────────────────────────────────────────────────────────────────


class SalesOrderLineCreate(BaseModel):
    product: int
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    discount_rate: Decimal = Field(default=Decimal(0), ge=0, le=1)
    warehouse: int | None = None
    comment: str | None = None


class SalesOrderLineUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    discount_rate: Decimal | None = Field(default=None, ge=0, le=1)
    warehouse: int | None = None
    comment: str | None = None


class SalesOrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_order_detail_id: int
    product: int
    product_code: str
    product_name: str
    quantity: Decimal
    cost: Decimal
    price: Decimal
    discount_rate: Decimal
    tax_rate: Decimal
    tax_included: bool
    currency: CurrencyCode
    exchange_rate: Decimal
    warehouse: int | None
    comment: str | None
    # Derived, never stored (spec Assumption 7)
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


# ── Header ────────────────────────────────────────────────────────────────────


class SalesOrderCreate(BaseModel):
    """Every field is optional — an empty body opens a draft on configured defaults (FR-010)."""

    customer: int | None = None
    salesperson: int | None = None
    point_sale: int | None = None
    payment_terms: PaymentTerms | None = None
    currency: CurrencyCode | None = None
    date: datetime | None = None
    promise_date: datetime | None = None
    contact: int | None = None
    ship_to: int | None = None
    recipient: str | None = Field(default=None, max_length=13)
    customer_name: str | None = Field(default=None, max_length=100)
    priority: Priority = Priority.NORMAL
    comment: str | None = None


class SalesOrderUpdate(BaseModel):
    customer: int | None = None
    salesperson: int | None = None
    payment_terms: PaymentTerms | None = None
    currency: CurrencyCode | None = None
    promise_date: datetime | None = None
    contact: int | None = None
    ship_to: int | None = None
    recipient: str | None = Field(default=None, max_length=13)
    customer_name: str | None = Field(default=None, max_length=100)
    # The one field that stays editable after completion (FR-011)
    priority: Priority | None = None
    comment: str | None = None


class SalesOrderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_order_id: int
    facility: int
    serial: int | None
    point_sale: int
    salesperson: int
    customer: int
    customer_name: str | None
    sales_quote: int | None
    payment_terms: PaymentTerms
    date: datetime
    promise_date: datetime
    due_date: datetime
    contact: int | None
    ship_to: int | None
    recipient: str | None
    recipient_name: str | None
    currency: CurrencyCode
    exchange_rate: Decimal
    priority: Priority
    comment: str | None
    status: DocumentStatus
    lines: list[SalesOrderLineResponse] = []
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    balance: Decimal


class SalesOrderSummary(BaseModel):
    """Flat row for list endpoints — no lines, so a page of orders stays one query."""

    model_config = ConfigDict(from_attributes=True)

    sales_order_id: int
    serial: int | None
    customer: int
    customer_name: str | None
    salesperson: int
    date: datetime
    due_date: datetime
    currency: CurrencyCode
    status: DocumentStatus
    total: Decimal
    balance: Decimal


# ── Product lookup ────────────────────────────────────────────────────────────


class ProductStockResponse(BaseModel):
    warehouse: int
    warehouse_name: str | None = None
    on_hand: Decimal


class ProductLookupResponse(BaseModel):
    product: int
    code: str
    name: str
    sku: str | None
    brand: str | None
    model: str | None
    bar_code: str | None
    price: Decimal
    tax_rate: Decimal
    tax_included: bool
    min_order_qty: int
    stock_required: bool
    stockable: bool
    stock: list[ProductStockResponse] = []
