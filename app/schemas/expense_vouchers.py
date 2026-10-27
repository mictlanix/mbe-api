"""Expense vouchers (spec 032, contracts/payments.md).

A voucher's state comes from its legacy flags (research R1): `draft`, `completed` (confirmed) and
`cancelled`. `voided` tells a confirmed voucher that was voided from a cancelled draft.
"""

from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import LocalDateTime
from app.schemas.inventory import Comment
from app.schemas.sales_order import DocumentStatus

#: Cash out of the drawer: above 0, in cents, at most 99,999,999.99 (research R12).
Amount = Annotated[Decimal, Field(gt=0, le=Decimal('99999999.99'), decimal_places=2)]


class ExpenseVoucherCreate(BaseModel):
    comment: Comment


class ExpenseVoucherUpdate(BaseModel):
    comment: Comment


class ExpenseVoucherLineCreate(BaseModel):
    expense: int
    amount: Amount
    #: Defaults to the category's comment (FR-002).
    comment: Comment


class ExpenseVoucherLineUpdate(BaseModel):
    expense: int | None = None
    amount: Amount | None = None
    comment: Comment


class ExpenseVoucherLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    expense_voucher_detail_id: int
    expense: int
    expense_name: str | None
    amount: Decimal
    comment: str | None


class ExpenseVoucherSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    expense_voucher_id: int
    status: DocumentStatus
    #: Confirmed and then voided; false for a cancelled draft.
    voided: bool
    facility: int
    cash_session: int
    creator: int
    creator_name: str | None
    #: Creation time, then confirmation time (research R3).
    date: LocalDateTime
    comment: str | None
    total: Decimal
    line_count: int


class ExpenseVoucherResponse(ExpenseVoucherSummary):
    lines: list[ExpenseVoucherLine] = []
