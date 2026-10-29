"""Warehouse receipts, issues and transfers (spec 029, contracts/inventory-documents.md), and
physical counts, stock and the kardex (spec 030, contracts/count-stock-kardex.md).

Receipts and issues reuse `DocumentStatus` and `derive_status` from the sales order: their
`completed` is the spec's "confirmed". A transfer has a state of its own, because "in transit" and
"received" are both completed (research R4).
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.schemas import LocalDateTime
from app.schemas.sales_order import DocumentStatus

#: A line quantity: positive, and no finer than the columns' four decimal places.
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=4)]
#: A counted quantity: like `Quantity`, but zero is a count too (spec 030, FR-002).
CountedQuantity = Annotated[Decimal, Field(ge=0, max_digits=18, decimal_places=4)]
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
            raise PydanticCustomError(
                'inventory_transfer_duplicate_line',
                'Each line may be received only once per request',
            )
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


# ── Counts (spec 030) ─────────────────────────────────────────────────────────


class CountCreate(BaseModel):
    warehouse: int
    comment: Comment


class CountUpdate(BaseModel):
    warehouse: int | None = None
    comment: Comment


class CountLineSet(BaseModel):
    product: int | None = None
    product_code: str | None = None
    quantity: CountedQuantity

    @model_validator(mode='after')
    def _exactly_one_product_reference(self) -> Self:
        if (self.product is None) == (self.product_code is None):
            raise PydanticCustomError(
                'inventory_count_product_reference', 'Give exactly one of product and product_code'
            )
        return self


class CountLinesSet(BaseModel):
    """A batch: each product's quantity is replaced, never added to (research R3)."""

    lines: list[CountLineSet] = Field(min_length=1, max_length=2000)


class CountLineUpdate(BaseModel):
    quantity: CountedQuantity


class CountConfirm(BaseModel):
    #: Add every product with a non-zero balance and no line, counted at 0 (FR-005).
    include_uncounted: bool = False


class InventoryCountLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    count_detail_id: int
    product: int
    product_code: str | None
    product_name: str | None
    quantity: Decimal
    #: Null while a draft, which is blind (research R4).
    quantity_on_hand: Decimal | None = None
    difference: Decimal | None = None


class InventoryCountResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_count_id: int
    facility: int
    warehouse: int
    serial: int | None
    status: DocumentStatus
    comment: str | None
    creator: int
    updater: int
    creation_time: LocalDateTime
    modification_time: LocalDateTime
    lines: list[InventoryCountLineResponse] = []


class NegativeAvailable(BaseModel):
    product: int
    product_code: str | None
    counted: Decimal
    reserved: Decimal
    available: Decimal


class CorrectedByOtherCount(BaseModel):
    product: int
    product_code: str | None
    inventory_count_id: int
    serial: int | None


class InventoryCountConfirmResponse(InventoryCountResponse):
    """The confirmed count and its two warnings; neither stops the confirm (FR-010, FR-010a)."""

    negative_available: list[NegativeAvailable] = []
    corrected_by_other_counts: list[CorrectedByOtherCount] = []


class InventoryCountSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    inventory_count_id: int
    facility: int
    warehouse: int
    serial: int | None
    status: DocumentStatus
    creator: int
    creation_time: LocalDateTime


# ── Stock (spec 030, R5) ──────────────────────────────────────────────────────


class StockRow(BaseModel):
    product: int
    product_code: str | None
    product_name: str | None
    warehouse: int
    on_hand: Decimal
    #: Sales order reservations (type 11) only.
    reserved: Decimal
    #: `on_hand − reserved`.
    available: Decimal
    #: Outstanding quantities of transfers in transit to the warehouse.
    incoming: Decimal


class InTransitStock(BaseModel):
    facility: int
    warehouse: int
    on_hand: Decimal


class ProductStock(BaseModel):
    product: int
    warehouses: list[StockRow] = []
    in_transit: list[InTransitStock] = []


# ── Kardex (spec 030, R6) ─────────────────────────────────────────────────────


class KardexDocument(BaseModel):
    #: The ledger type the document was resolved from.
    type: int
    id: int
    facility: int | None = None
    serial: int | None = None


class KardexRow(BaseModel):
    lot_serial_tracking_id: int
    date: LocalDateTime
    source: int
    source_name: str
    #: Null when the entry cannot be resolved; `source` and `reference` are then shown raw.
    document: KardexDocument | None
    reference: int
    quantity_in: Decimal
    quantity_out: Decimal
    balance: Decimal


class Kardex(BaseModel):
    model_config = ConfigDict(validate_by_name=True, validate_by_alias=True)

    product: int
    warehouse: int
    #: `from` on the wire; a Python keyword, so `from_` here.
    from_: date = Field(alias='from')
    #: Inclusive of its whole day.
    to: date
    opening: Decimal
    rows: list[KardexRow] = []
    total_in: Decimal
    total_out: Decimal
    closing: Decimal
