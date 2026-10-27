"""Purchase requests, purchase orders and their cost history (spec 031, contracts/purchasing.md).

A request's state comes from its legacy flags (research R1): `draft`, `submitted`, `approved` and
`cancelled`. A purchase order reuses `DocumentStatus`: its `completed` is the spec's "confirmed".
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.clock import local_today
from app.schemas import LocalDateTime
from app.schemas.inventory import Comment
from app.schemas.sales_order import DocumentStatus
from app.schemas.sales_quote import QuoteEmailRequest

#: A request line's quantity: positive, at the column's two decimals (FR-002).
RequestQuantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
#: A purchase-order line's quantity, at four decimals (FR-021).
OrderQuantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
#: A unit price before tax, at the column's seven decimals (FR-021).
UnitPrice = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=7)]
TaxRate = Annotated[Decimal, Field(ge=0, lt=1, max_digits=5, decimal_places=4)]
ProductName = Annotated[str, Field(min_length=1, max_length=250)]


class RequestStatus(StrEnum):
    DRAFT = 'draft'
    SUBMITTED = 'submitted'
    APPROVED = 'approved'
    CANCELLED = 'cancelled'


def _not_past(value: date | None) -> date | None:
    if value is not None and value < local_today():
        raise ValueError('The estimated receipt date cannot be in the past')
    return value


# ── Purchase requests: requests ───────────────────────────────────────────────


class PurchaseRequestCreate(BaseModel):
    #: Defaults to the caller's point-of-sale warehouse (FR-001).
    warehouse: int | None = None
    comment: Comment


class PurchaseRequestUpdate(BaseModel):
    warehouse: int | None = None
    comment: Comment


class RequestLineAdd(BaseModel):
    product: int
    #: Defaults to the product's minimum order quantity, or 1 when that is 0 (FR-002).
    quantity: RequestQuantity | None = None
    product_name: ProductName | None = None
    #: Defaults to the request's warehouse.
    warehouse: int | None = None


class RequestLineUpdate(BaseModel):
    quantity: RequestQuantity | None = None
    product_name: ProductName | None = None
    warehouse: int | None = None


class ToPurchaseSet(BaseModel):
    to_purchase: bool


class RejectRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator('reason')
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('A reason is required and cannot be blank')
        return value.strip()


class BulkApprove(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=100)


# ── Purchase requests: responses ──────────────────────────────────────────────


class PurchaseRequestSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_request_id: int
    serial: int | None
    status: RequestStatus
    warehouse: int
    facility: int | None
    date: LocalDateTime
    comment: str | None
    creator: int
    updater: int
    approver: int | None
    approval_time: LocalDateTime | None
    line_count: int


class PurchaseRequestLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_request_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal
    warehouse: int | None
    to_purchase: bool
    min_order_qty: int | None
    #: The live purchase-order line this line is on, if any (FR-010).
    purchase_order_detail: int | None = None
    purchase_order: int | None = None
    purchase_order_status: DocumentStatus | None = None


class PurchaseRequestResponse(PurchaseRequestSummary):
    warnings: list[str] = []
    lines: list[PurchaseRequestLine] = []


class BulkApproveResult(BaseModel):
    purchase_request_id: int
    approved: bool
    reason: str | None = None


class BulkApproveResponse(BaseModel):
    results: list[BulkApproveResult]


# ── Purchase orders: requests ─────────────────────────────────────────────────


class PurchaseOrderCreate(BaseModel):
    supplier: int
    estimated_receipt_date: date | None = None
    comment: Comment

    _date = field_validator('estimated_receipt_date')(_not_past)


class PurchaseOrderUpdate(BaseModel):
    supplier: int | None = None
    estimated_receipt_date: date | None = None
    comment: Comment

    _date = field_validator('estimated_receipt_date')(_not_past)


class OrderLineAdd(BaseModel):
    product: int
    quantity: OrderQuantity
    #: Defaults to the caller's point-of-sale warehouse (FR-021).
    warehouse: int | None = None
    #: Defaults to the product's cost, 0 when it has none.
    price: UnitPrice | None = None
    #: Defaults to the product's.
    tax_rate: TaxRate | None = None


class OrderLineUpdate(BaseModel):
    quantity: OrderQuantity | None = None
    warehouse: int | None = None
    price: UnitPrice | None = None
    tax_rate: TaxRate | None = None


class AddRequestLines(BaseModel):
    purchase_request: int


class ToPurchaseCreate(BaseModel):
    #: Required: lines without a supplier are added to a PO by hand (FR-024).
    supplier: int
    facility: int


class PurchaseOrderEmailRequest(QuoteEmailRequest):
    message: str | None = Field(default=None, max_length=1000)


# ── Purchase orders: responses ────────────────────────────────────────────────


class PurchaseOrderLine(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_order_detail_id: int
    product: int
    product_code: str
    product_name: str
    warehouse: int | None
    quantity: Decimal
    price: Decimal
    tax_rate: Decimal
    amount: Decimal
    purchase_request_detail: int | None
    purchase_request: int | None = None
    #: On confirmed, non-cancelled receipts; null for a non-stockable line (FR-030).
    quantity_received: Decimal | None = None
    quantity_pending: Decimal | None = None


class SkippedLine(BaseModel):
    purchase_request_detail: int
    reason: str


class GeneratedReceipt(BaseModel):
    inventory_receipt_id: int
    warehouse: int
    facility: int
    line_count: int


class PurchaseOrderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    purchase_order_id: int
    status: DocumentStatus
    supplier: int | None
    supplier_name: str | None
    estimated_receipt_date: LocalDateTime | None
    comment: str | None
    creator: int
    updater: int
    creation_time: LocalDateTime
    modification_time: LocalDateTime
    line_count: int
    subtotal: Decimal
    tax: Decimal
    total: Decimal


class PurchaseOrderResponse(PurchaseOrderSummary):
    lines: list[PurchaseOrderLine] = []
    warnings: list[str] = []
    skipped: list[SkippedLine] = []
    receipts: list[GeneratedReceipt] = []


class ToPurchaseLine(BaseModel):
    purchase_request_detail_id: int
    purchase_request: int
    serial: int | None
    product: int
    product_code: str
    product_name: str | None
    quantity: Decimal
    warehouse: int
    #: The product's cost, 0 when it has none.
    cost: Decimal


class ToPurchaseGroup(BaseModel):
    #: Null for the lines whose product has no supplier.
    supplier: int | None
    supplier_name: str | None
    facility: int
    line_count: int
    estimated_total: Decimal
    lines: list[ToPurchaseLine]


class ToPurchaseResponse(BaseModel):
    groups: list[ToPurchaseGroup]


class CostChange(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_cost_change_id: int
    product: int
    receipt: int
    purchase_order_detail: int | None
    previous_cost: Decimal
    new_cost: Decimal
    quantity_on_hand: Decimal | None
    quantity_received: Decimal | None
    unit_cost: Decimal | None
    reverted_change: int | None
    creation_time: LocalDateTime
    creator: int
