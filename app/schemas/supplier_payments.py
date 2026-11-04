"""Supplier payments, their applications to purchase orders, and balances (spec 032,
contracts/payments.md).

A payment is `completed` or `cancelled`; it is never a draft. Amounts are cents above 0, at most
99,999,999.99 (research R12).
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.core.clock import local_now
from app.enums import PaymentMethod
from app.schemas import LocalDateTime
from app.schemas.inventory import Comment
from app.schemas.sales_order import DocumentStatus

Amount = Annotated[Decimal, Field(gt=0, le=Decimal('99999999.99'), decimal_places=2)]

MAX_APPLICATIONS = 50


class PaymentStatus(StrEnum):
    COMPLETED = 'completed'
    CANCELLED = 'cancelled'


def _distinct(items: list['ApplicationItem']) -> list['ApplicationItem']:
    orders = [item.purchase_order for item in items]
    if len(orders) != len(set(orders)):
        raise PydanticCustomError(
            'supplier_payment_duplicate_order', 'Each purchase order can appear only once'
        )
    return items


# ── Requests ──────────────────────────────────────────────────────────────────


class ApplicationItem(BaseModel):
    purchase_order: int
    amount: Amount


class SupplierPaymentCreate(BaseModel):
    supplier: int
    amount: Amount
    method: PaymentMethod
    #: Defaults to now. Refused on a cash payment, which is always dated now.
    date: LocalDateTime | None = None
    reference: str | None = Field(default=None, max_length=50)
    comment: Comment
    #: Only from an administrator without a facility, and only for a non-cash payment (research R5).
    facility: int | None = None
    applications: list[ApplicationItem] = Field(default=[], max_length=MAX_APPLICATIONS)

    _applications = field_validator('applications')(_distinct)

    @field_validator('date')
    @classmethod
    def _not_in_the_future(cls, value: datetime | None) -> datetime | None:
        if value is not None and value > local_now():
            raise PydanticCustomError(
                'supplier_payment_date_in_future',
                'The payment date cannot be in the future',
                {'date': value.isoformat()},
            )
        return value

    @model_validator(mode='after')
    def _cash_takes_its_session(self) -> 'SupplierPaymentCreate':
        if self.method is PaymentMethod.CASH:
            if self.date is not None:
                raise PydanticCustomError(
                    'supplier_payment_cash_has_date', 'A cash payment is dated now; send no date'
                )
            if self.facility is not None:
                raise PydanticCustomError(
                    'supplier_payment_cash_has_facility',
                    "A cash payment takes its session's facility; send no facility",
                )
        return self


class ApplicationsCreate(BaseModel):
    applications: list[ApplicationItem] = Field(min_length=1, max_length=MAX_APPLICATIONS)

    _applications = field_validator('applications')(_distinct)


# ── Responses ─────────────────────────────────────────────────────────────────


class Application(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_order_payment_id: int
    purchase_order: int
    amount: Decimal
    applier: int
    date: LocalDateTime
    #: True once reversed. A reversal is kept, never deleted (FR-015).
    cancelled: bool


class SupplierPaymentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_payment_id: int
    #: Null, like `serial`, on the legacy payments recorded before spec 032.
    facility: int | None
    serial: int | None
    status: PaymentStatus
    supplier: int
    supplier_name: str | None
    amount: Decimal
    method: int
    date: LocalDateTime
    reference: str | None
    comment: str | None
    cash_session: int | None
    creator: int
    #: The sum of live applications.
    applied: Decimal
    #: What is left to apply: the amount less `applied`; 0 once cancelled, or on a legacy payment.
    credit: Decimal


class SupplierPaymentResponse(SupplierPaymentSummary):
    applications: list[Application] = []


class PurchaseOrderApplication(Application):
    supplier_payment: int
    serial: int | None


class PurchaseOrderBalance(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_order_id: int
    supplier: int | None
    status: DocumentStatus
    #: Confirmed on or after the balance cutoff: only then does the PO have a balance (FR-020).
    counts: bool
    total: Decimal
    applied: Decimal
    balance: Decimal | None
    applications: list[PurchaseOrderApplication] = []


class SupplierBalanceOrder(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_order_id: int
    #: When the PO was confirmed: its `modification_time`, fixed once confirmed (research R6).
    confirmed: LocalDateTime
    total: Decimal
    applied: Decimal
    balance: Decimal


class SupplierBalancePayment(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_payment_id: int
    facility: int
    serial: int
    date: LocalDateTime
    amount: Decimal
    credit: Decimal


class SupplierBalance(BaseModel):
    supplier: int
    supplier_name: str
    cutoff: LocalDateTime | None
    owed: Decimal
    credit: Decimal
    net: Decimal
    orders: list[SupplierBalanceOrder] = []
    payments: list[SupplierBalancePayment] = []
