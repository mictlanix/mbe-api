"""Warehouse receipts, issues and transfers (spec 029, contracts/inventory-documents.md).

Receipts and issues reuse `DocumentStatus` and `derive_status` from the sales order: their
`completed` is the spec's "confirmed". A transfer has a state of its own, because "in transit" and
"received" are both completed (research R4).
"""

from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import LocalDateTime
from app.schemas.sales_order import DocumentStatus

#: A line quantity: positive, and no finer than the columns' four decimal places.
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
Comment = Annotated[str | None, Field(default=None, max_length=500)]

# ── Lifecycle ─────────────────────────────────────────────────────────────────


class TransferStatus(StrEnum):
    DRAFT = 'draft'
    IN_TRANSIT = 'in_transit'
    RECEIVED = 'received'
    CANCELLED = 'cancelled'


def derive_transfer_status(*, completed: bool, cancelled: bool, in_transit: bool) -> TransferStatus:
    if cancelled:
        return TransferStatus.CANCELLED
    if not completed:
        return TransferStatus.DRAFT
    if in_transit:
        return TransferStatus.IN_TRANSIT
    return TransferStatus.RECEIVED


# ── Requests ──────────────────────────────────────────────────────────────────


class ReceiptCreate(BaseModel):
    warehouse: int
    comment: Comment


class ReceiptUpdate(BaseModel):
    warehouse: int | None = None
    comment: Comment


class IssueCreate(BaseModel):
    warehouse: int
    comment: Comment


class IssueUpdate(BaseModel):
    warehouse: int | None = None
    comment: Comment


class TransferCreate(BaseModel):
    warehouse: int
    warehouse_to: int
    comment: Comment


class TransferUpdate(BaseModel):
    warehouse: int | None = None
    warehouse_to: int | None = None
    comment: Comment


class LineAdd(BaseModel):
    product: int
    quantity: Quantity


class LineUpdate(BaseModel):
    quantity: Quantity


class SalesOrderItemsAdd(BaseModel):
    sales_order: int


class TransferReceiveLine(BaseModel):
    line_id: int
    quantity: Quantity


class TransferReceive(BaseModel):
    #: Omitted receives everything outstanding; an empty list is refused rather than read as that.
    lines: list[TransferReceiveLine] | None = Field(default=None, min_length=1)

    @field_validator('lines')
    @classmethod
    def _each_line_once(
        cls, lines: list[TransferReceiveLine] | None
    ) -> list[TransferReceiveLine] | None:
        if lines is not None and len({line.line_id for line in lines}) != len(lines):
            raise ValueError('Each line may be received only once per request')
        return lines


# ── Receipts ──────────────────────────────────────────────────────────────────


class InventoryReceiptLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    receipt_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal
    purchase_order_detail: int | None
    quantity_ordered: Decimal
    #: Null for a line not linked to a purchase order (research R7).
    quantity_received_elsewhere: Decimal | None = None


class InventoryReceiptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_receipt_id: int
    facility: int
    warehouse: int
    purchase_order: int | None
    serial: int | None
    status: DocumentStatus
    comment: str | None
    creator: int
    updater: int
    creation_time: LocalDateTime
    modification_time: LocalDateTime
    lines: list[InventoryReceiptLineResponse] = []
    warnings: list[str] = []


class InventoryReceiptSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_receipt_id: int
    facility: int
    warehouse: int
    purchase_order: int | None
    serial: int | None
    status: DocumentStatus
    creator: int
    creation_time: LocalDateTime


# ── Issues ────────────────────────────────────────────────────────────────────


class InventoryIssueLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    issue_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal


class InventoryIssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_issue_id: int
    facility: int
    warehouse: int
    supplier_return: int | None
    serial: int | None
    status: DocumentStatus
    comment: str | None
    creator: int
    updater: int
    creation_time: LocalDateTime
    modification_time: LocalDateTime
    lines: list[InventoryIssueLineResponse] = []


class InventoryIssueSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_issue_id: int
    facility: int
    warehouse: int
    supplier_return: int | None
    serial: int | None
    status: DocumentStatus
    creator: int
    creation_time: LocalDateTime


# ── Transfers ─────────────────────────────────────────────────────────────────


class InventoryTransferLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    transfer_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal
    #: The stored quantity, or `quantity` for a completed legacy line that has none (R4).
    quantity_received: Decimal
    quantity_outstanding: Decimal


class InventoryTransferResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_transfer_id: int
    facility: int
    warehouse: int
    warehouse_to: int
    serial: int | None
    status: TransferStatus
    comment: str | None
    creator: int
    updater: int
    creation_time: LocalDateTime
    modification_time: LocalDateTime
    lines: list[InventoryTransferLineResponse] = []


class InventoryTransferSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_transfer_id: int
    facility: int
    warehouse: int
    warehouse_to: int
    serial: int | None
    status: TransferStatus
    creator: int
    creation_time: LocalDateTime


class InTransitProduct(BaseModel):
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal


class InTransitLine(BaseModel):
    inventory_transfer_id: int
    serial: int | None
    warehouse: int
    warehouse_to: int
    transfer_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity_outstanding: Decimal


class InTransitResponse(BaseModel):
    facility: int
    products: list[InTransitProduct] = []
    lines: list[InTransitLine] = []
