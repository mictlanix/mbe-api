from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.enums import CurrencyCode
from app.schemas import CUSTOMER_DISPLAY_NAME_DESCRIPTION, LocalDateTime
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
    #: The customer's own name (#284), as on `CustomerPaymentResponse` (#270).
    customer_display_name: str | None = Field(
        default=None, description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
    )
    sales_person: int
    facility: int
    serial: int | None
    date: LocalDateTime | None
    currency: CurrencyCode
    exchange_rate: Decimal
    status: DocumentStatus
    #: How the customer was paid back, from the refund's credit note (#286): null until confirmed,
    #: and on the current system's refunds that only reduced what an unpaid order owed.
    payout: RefundPayout | None = None
    #: The payment the confirm wrote: the cash that left the drawer, or the credit note's backing
    #: payment, whose id the note's ticket prints and redeems by.
    customer_payment: int | None = None
    #: The refund's credit note. A cash payout has one too, already paid back in cash.
    credit_note: int | None = None
    lines: list[CustomerRefundLineResponse] = []
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal


class CustomerRefundSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    customer_refund_id: int
    sales_order: int
    customer: int | None
    #: The customer's own name (#284), as on `CustomerPaymentResponse` (#270).
    customer_display_name: str | None = Field(
        default=None, description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
    )
    serial: int | None
    date: LocalDateTime | None
    currency: CurrencyCode
    status: DocumentStatus
    #: How the customer was paid back, from the refund's credit note (#286): null until confirmed,
    #: and on the current system's refunds that only reduced what an unpaid order owed.
    payout: RefundPayout | None = None
    #: The payment the confirm wrote: the cash that left the drawer, or the credit note's backing
    #: payment, whose id the note's ticket prints and redeems by.
    customer_payment: int | None = None
    #: The refund's credit note. A cash payout has one too, already paid back in cash.
    credit_note: int | None = None
    total: Decimal
