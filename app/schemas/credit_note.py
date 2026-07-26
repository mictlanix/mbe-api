from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CreditNoteResponse(BaseModel):
    """A credit note is a view over its backing payment (FR-070).

    `refunded` is the amount *issued* and is never decremented; `remaining` is derived from the
    backing payment's non-cancelled applications, so there is no second balance to drift.
    """

    model_config = ConfigDict(from_attributes=True)

    credit_note_id: int
    customer: int
    sales_order: int
    customer_refund: int
    customer_payment: int
    refunded: Decimal
    remaining: Decimal
    cash_session: int | None
    date: datetime | None
