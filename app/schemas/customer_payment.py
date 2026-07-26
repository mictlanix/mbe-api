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


class ReversalRequest(BaseModel):
    """The reason is mandatory — SC-009 allows no anonymous or unexplained reversal."""

    reason: str = Field(min_length=1, max_length=500)


class RejectionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class OutstandingOrderResponse(BaseModel):
    sales_order_id: int
    serial: int | None
    customer: int
    customer_name: str | None
    date: datetime
    due_date: datetime
    currency: CurrencyCode
    total: Decimal
    balance: Decimal
