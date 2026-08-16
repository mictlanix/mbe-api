from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import CurrencyCode, PaymentMethod, PaymentType


class CustomerPaymentCreate(BaseModel):
    customer: int
    amount: Decimal = Field(gt=0)
    method: PaymentMethod
    currency: CurrencyCode | None = None
    payment_charge: int | None = None
    reference: str | None = Field(default=None, max_length=50)
    date: datetime | None = None
    payment_type: PaymentType = PaymentType.IMMEDIATE


class CustomerPaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_payment_id: int
    customer: int
    amount: Decimal
    currency: CurrencyCode
    method: PaymentMethod
    payment_charge: int | None
    reference: str | None
    date: datetime
    facility: int
    cash_session: int | None
    payment_type: PaymentType
    verifier: int | None
    # Derived: amount less its non-cancelled applications (never stored)
    unapplied: Decimal


class ApplicationCreate(BaseModel):
    sales_order: int
    amount: Decimal = Field(gt=0)
    # Change handed back on a cash tender; does not consume the payment's unapplied amount
    amount_change: Decimal = Field(default=Decimal(0), ge=0)


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sales_order_payment_id: int
    sales_order: int
    customer_payment: int
    amount: Decimal
    amount_change: Decimal
    applier: int | None
    date: datetime | None
    cancelled: bool


class OrderApplicationResponse(ApplicationResponse):
    """An application seen from the order's side, with its payment flattened onto it (#134).

    The payment fields are the ones needed to render a row — how it was tendered, what identifies
    it, and whether verification has passed. `date` stays the application's; the payment's own is
    `payment_date`, because the two differ whenever a payment is applied later than it was taken.
    """

    method: PaymentMethod
    currency: CurrencyCode
    reference: str | None
    payment_date: datetime
    payment_type: PaymentType
    verifier: int | None


class ReversalRequest(BaseModel):
    """The reason is mandatory — SC-009 allows no anonymous or unexplained reversal."""

    reason: str = Field(min_length=1, max_length=500)


class RejectionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class OutstandingOrderResponse(BaseModel):
    sales_order_id: int
    serial: int | None
    customer: int
    #: The per-document override, null on every order that did not set one — which was all 1,840
    #: outstanding orders when #174 was raised, so a list rendering it showed a dash on every row.
    #: Read `customer_display_name` to show a customer; this only says whether the document
    #: overrides that name.
    customer_name: str | None
    #: The customer's own name, joined from `customer` (#174). Same field name as
    #: `SalesOrderSummary.customer_display_name` (#173), so the two lists agree.
    customer_display_name: str | None = None
    date: datetime
    due_date: datetime
    currency: CurrencyCode
    total: Decimal
    balance: Decimal
