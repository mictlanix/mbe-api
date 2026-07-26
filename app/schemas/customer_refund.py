from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.enums import CurrencyCode
from app.schemas.sales_order import DocumentStatus


class RefundPayout(StrEnum):
    """How the customer gets their money back (FR-065).

    A refundable order is always fully paid, so its balance is zero and the whole refund total is
    owed back — the only question is the form.
    """

    CASH = 'cash'
    CREDIT_NOTE = 'credit_note'


class CustomerRefundCreate(BaseModel):
    sales_order: int


class CustomerRefundLineUpdate(BaseModel):
    quantity: Decimal | None = Field(default=None, ge=0)
    warehouse: int | None = None


class CustomerRefundConfirm(BaseModel):
    payout: RefundPayout


class CustomerRefundLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_refund_detail_id: int
    sales_order_detail: int
    product: int
    product_code: str
    product_name: str
    quantity: Decimal
    price: Decimal
    # The refund line's column is `discount`, not `discount_rate` (see spec Divergences)
    discount: Decimal
    tax_rate: Decimal
    tax_included: bool
    currency: CurrencyCode
    warehouse: int | None
    refundable_quantity: Decimal
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


class CustomerRefundResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_refund_id: int
    sales_order: int
    customer: int | None
    sales_person: int
    facility: int
    serial: int | None
    date: datetime | None
    currency: CurrencyCode
    exchange_rate: Decimal
    status: DocumentStatus
    lines: list[CustomerRefundLineResponse] = []
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


class CustomerRefundSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_refund_id: int
    sales_order: int
    customer: int | None
    serial: int | None
    date: datetime | None
    currency: CurrencyCode
    status: DocumentStatus
    total: Decimal
