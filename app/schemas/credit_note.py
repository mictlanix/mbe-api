from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import CUSTOMER_DISPLAY_NAME_DESCRIPTION, LocalDateTime


class CreditNoteResponse(BaseModel):
    """A credit note is a view over its backing payment (FR-070, spec 034).

    `amount` is what was issued, the payment's amount. `remaining` is the payment's unapplied
    amount, change handed back included, and 0 once the note was paid back in cash. `refunded` is
    what was handed back in cash then, the column's meaning in the current system: 0 otherwise.
    """

    model_config = ConfigDict(from_attributes=True)

    credit_note_id: int
    customer: int
    #: The customer's own name (#283), as on `CustomerPaymentResponse` (#270).
    customer_display_name: str | None = Field(
        default=None, description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
    )
    sales_order: int
    customer_refund: int
    customer_payment: int
    #: The backing payment's facility (FR-006).
    facility: int
    amount: Decimal
    remaining: Decimal
    refunded: Decimal
    #: Paid back to the customer in cash: the current system's `IsRefundedToCustomer`.
    refunded_to_customer: bool
    cash_session: int | None
    date: LocalDateTime | None
