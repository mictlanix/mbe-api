"""Two-step transfers between warehouses, through the origin facility's transit (spec 029, US3).

Drafts, lines, the list and cancel are the shared mechanics of `inventory_documents`; this adds
what only a transfer has: dispatch into transit, receive (in full or per line), closing the
remainder, the in-transit view and the sales-order items (FR-040 to FR-046, research R4, R10).

Every step runs inside `busy_as_conflict` and starts with `begin_locked`, with no ordinary read
before it (R2). The transit warehouse is that of the transfer's own `facility`, never the origin
warehouse's current one, which a warehouse edit could change (R4).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any, NamedTuple

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.deps import CurrentUser
from app.models.core import Warehouse
from app.models.inventory import InventoryTransfer, InventoryTransferDetail
from app.models.product import Product
from app.models.sales import SalesOrder, SalesOrderDetail
from app.schemas.inventory import (
    InTransitLine,
    InTransitProduct,
    InTransitResponse,
    InventoryTransferLineResponse,
    TransferStatus,
    derive_transfer_status,
)
from app.services import documents, stock_ledger
from app.services import inventory_documents as inv
from app.services.inventory_documents import TRANSFER

# ── Pure helpers ──────────────────────────────────────────────────────────────


def transfer_line_figures(
    quantity: Decimal, quantity_received: Decimal | None, completed: bool, in_transit: bool
) -> tuple[Decimal, Decimal]:
    """`(received, outstanding)` of one line (R4, FR-044a).

    A NULL `quantity_received` is a legacy one-step line: received in full once completed. Only a
    transfer in transit owes anything; a closed or cancelled one keeps what had been received.
    """
    if quantity_received is not None:
        received = quantity_received
    else:
        received = quantity if completed else Decimal(0)
    return received, (quantity - received if in_transit else Decimal(0))


def _outstanding(line: Any) -> Decimal:
    return transfer_line_figures(line.quantity, line.quantity_received, True, True)[1]


def apply_receipt(
    lines: Sequence[Any], requested: Sequence[tuple[int, Decimal]] | None
) -> list[tuple[Any, Decimal]]:
    """What each line of a transfer in transit receives (FR-042).

    `None` receives everything outstanding. Otherwise a line of another transfer is 404, the same
    line twice 422, and more than a line's outstanding quantity (or any, when none is) 409.
    """
    if requested is None:
        return [(line, _outstanding(line)) for line in lines if _outstanding(line) > 0]

    by_id = {line.transfer_detail_id: line for line in lines}
    seen: set[int] = set()
    received = []
    for line_id, quantity in requested:
        if line_id in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Line {line_id} is received more than once',
            )
        seen.add(line_id)
        line = by_id.get(line_id)
        if line is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f'Line {line_id} not found on this transfer',
            )
        outstanding = _outstanding(line)
        if quantity > outstanding:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f'Line {line.product_code}: {quantity} received, {outstanding} outstanding'
                ),
            )
        received.append((line, quantity))
    return received


class Addition(NamedTuple):
    quantity: Decimal
    code: str
    name: str


def sales_order_additions(
    order_lines: Iterable[Any], existing_products: set[int]
) -> dict[int, Addition]:
    """Product → what a sales order adds to a transfer (FR-046, R9).

    Stockable lines summed per product, skipping products already on the transfer, so adding the
    same order twice adds nothing the second time. `order_lines` carry `product`, `quantity`,
    `stockable`, and the product's `code` and `name` for the line snapshots.
    """
    totals: dict[int, Decimal] = defaultdict(Decimal)
    names: dict[int, tuple[str, str]] = {}
    for line in order_lines:
        if not line.stockable or line.product in existing_products:
            continue
        totals[line.product] += line.quantity
        names[line.product] = (line.code, line.name)
    return {product: Addition(totals[product], *names[product]) for product in totals}


def transfer_status_clause(kind: inv.Kind, value: str) -> ColumnElement[bool]:
    """The list's `status` filter for transfers (R4, R5); anything else is refused."""
    header = kind.header
    if value == TransferStatus.DRAFT:
        return and_(header.completed.is_(False), header.cancelled.is_(False))
    if value == TransferStatus.IN_TRANSIT:
        return and_(
            header.completed.is_(True), header.cancelled.is_(False), header.in_transit.is_(True)
        )
    if value == TransferStatus.RECEIVED:
        return and_(
            header.completed.is_(True), header.cancelled.is_(False), header.in_transit.is_(False)
        )
    if value == TransferStatus.CANCELLED:
        return header.cancelled.is_(True)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, in_transit, received, cancelled',
    )


# ── Derived fields ────────────────────────────────────────────────────────────


def attach_status(transfer: Any) -> Any:
    transfer.__dict__['status'] = derive_transfer_status(
        completed=transfer.completed, cancelled=transfer.cancelled, in_transit=transfer.in_transit
    )
    return transfer


async def attach_derived(db: AsyncSession, transfer: InventoryTransfer) -> InventoryTransfer:
    """The status, and the lines with what each has received and still owes."""
    attach_status(transfer)
    lines = []
    for line in await inv._lines(db, TRANSFER, transfer.inventory_transfer_id):
        received, outstanding = transfer_line_figures(
            line.quantity, line.quantity_received, transfer.completed, transfer.in_transit
        )
        lines.append(
            InventoryTransferLineResponse(
                transfer_detail_id=line.transfer_detail_id,
                product=line.product,
                product_code=line.product_code,
                product_name=line.product_name,
                quantity=line.quantity,
                quantity_received=received,
                quantity_outstanding=outstanding,
            )
        )
    transfer.__dict__['lines'] = lines
    return transfer


# ── Steps ─────────────────────────────────────────────────────────────────────


async def _transit(db: AsyncSession, transfer: InventoryTransfer) -> int:
    """The transit of the transfer's own facility. `begin_locked(stock=True)` has already locked
    it, and refused a facility without one; the rows are fixed (spec 013), so this read is current.
    """
    return (await inv._transit_map(db))[transfer.facility]


async def _destination_facility(db: AsyncSession, transfer: InventoryTransfer) -> int:
    warehouse = await db.get(Warehouse, transfer.warehouse_to, populate_existing=True)
    return warehouse.facility


def _assert_in_transit(transfer: InventoryTransfer) -> None:
    if not transfer.in_transit:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Transfer is not in transit'
        )


def _move(
    db: AsyncSession, transfer_id: int, line: Any, quantity: Decimal, source: int, target: int
) -> None:
    """One line's quantity out of `source` and into `target`."""
    for warehouse, outbound in ((source, True), (target, False)):
        stock_ledger.post_movement(
            db,
            source=TRANSFER.transaction_type,
            reference=transfer_id,
            product=line.product,
            warehouse=warehouse,
            quantity=quantity,
            outbound=outbound,
        )


async def dispatch(
    db: AsyncSession, transfer_id: int, *, current: CurrentUser
) -> InventoryTransfer:
    """Number the transfer and move its lines from the origin into transit (FR-041).

    A facility without a transit warehouse is refused by `begin_locked` (422), before any serial
    is taken. Any refusal rolls back, so no serial is consumed (R1).
    """
    async with inv.busy_as_conflict(db):
        transfer = await inv.begin_locked(db, TRANSFER, transfer_id, stock=True)
        # Another facility learns nothing about the transfer's state or lines.
        inv.assert_facility(current, transfer.facility)
        documents.assert_editable(transfer)

        lines = await inv._lines(db, TRANSFER, transfer_id)
        if not lines:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='Transfer has no lines'
            )
        products = {
            row.product_id: row
            for row in (
                await db.execute(
                    select(Product).where(Product.product_id.in_({ln.product for ln in lines}))
                )
            )
            .scalars()
            .all()
        }
        for line in lines:
            if line.quantity <= 0:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f'Line {line.product_code} has no quantity',
                )
            if not products[line.product].stockable:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f'Product {products[line.product].code} is not stockable',
                )

        serial = await inv.next_serial(db, InventoryTransfer, transfer.facility)
        await inv.assert_available(db, inv.per_product_totals(lines), transfer.warehouse)
        transit = await _transit(db, transfer)

        for line in lines:
            _move(db, transfer_id, line, line.quantity, transfer.warehouse, transit)

        # Last, after every read, so autoflush cannot write it before the checks pass.
        transfer.serial = serial
        transfer.completed = True
        transfer.in_transit = True
        for line in lines:
            line.quantity_received = Decimal(0)
        inv._touch(transfer, current)
        await db.commit()
    await db.refresh(transfer)
    return transfer


async def receive(
    db: AsyncSession,
    transfer_id: int,
    requested: Sequence[tuple[int, Decimal]] | None,
    *,
    current: CurrentUser,
) -> InventoryTransfer:
    """Move received quantities from transit to the destination (FR-042).

    `None` receives everything outstanding. The transfer stays in transit while any line owes.
    """
    async with inv.busy_as_conflict(db):
        transfer = await inv.begin_locked(db, TRANSFER, transfer_id, stock=True)
        inv.assert_facility(current, await _destination_facility(db, transfer))
        _assert_in_transit(transfer)

        lines = await inv._lines(db, TRANSFER, transfer_id)
        received = apply_receipt(lines, requested)
        transit = await _transit(db, transfer)

        for line, quantity in received:
            _move(db, transfer_id, line, quantity, transit, transfer.warehouse_to)
            line.quantity_received = (line.quantity_received or Decimal(0)) + quantity
        if all(_outstanding(line) <= 0 for line in lines):
            transfer.in_transit = False
        inv._touch(transfer, current)
        await db.commit()
    await db.refresh(transfer)
    return transfer


async def close_remainder(
    db: AsyncSession, transfer_id: int, *, current: CurrentUser
) -> InventoryTransfer:
    """Return what is still in transit to the origin; the transfer is received (FR-043).

    Either end may close it (R6). Lines keep their received quantities.
    """
    async with inv.busy_as_conflict(db):
        transfer = await inv.begin_locked(db, TRANSFER, transfer_id, stock=True)
        inv.assert_facility(current, transfer.facility, await _destination_facility(db, transfer))
        _assert_in_transit(transfer)

        transit = await _transit(db, transfer)
        for line in await inv._lines(db, TRANSFER, transfer_id):
            outstanding = _outstanding(line)
            if outstanding > 0:
                _move(db, transfer_id, line, outstanding, transit, transfer.warehouse)
        transfer.in_transit = False
        inv._touch(transfer, current)
        await db.commit()
    await db.refresh(transfer)
    return transfer


async def cancel(db: AsyncSession, transfer_id: int, *, current: CurrentUser) -> InventoryTransfer:
    """Reverse everything the transfer posted and clear `in_transit` (FR-044, R3)."""
    return await inv.cancel(db, TRANSFER, transfer_id, current=current)


async def add_sales_order_items(
    db: AsyncSession, transfer_id: int, sales_order: int, *, current: CurrentUser
) -> InventoryTransfer:
    """Add a confirmed sales order's stockable lines, skipping products already on the transfer
    (FR-046). Repeating it adds nothing. Locks the document only (R2)."""
    async with inv.busy_as_conflict(db):
        transfer = await inv.begin_locked(db, TRANSFER, transfer_id)
        documents.assert_editable(transfer)

        order = await db.get(SalesOrder, sales_order)
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found'
            )
        if not order.completed or order.cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail='Sales order is not confirmed, or is cancelled',
            )

        order_lines = (
            await db.execute(
                select(
                    SalesOrderDetail.product,
                    SalesOrderDetail.quantity,
                    Product.stockable,
                    Product.code,
                    Product.name,
                )
                .join(Product, Product.product_id == SalesOrderDetail.product)
                .where(SalesOrderDetail.sales_order == sales_order)
                .order_by(SalesOrderDetail.sales_order_detail_id)
            )
        ).all()
        existing = {line.product for line in await inv._lines(db, TRANSFER, transfer_id)}
        additions = sales_order_additions(order_lines, existing)

        for product, addition in additions.items():
            db.add(
                InventoryTransferDetail(
                    transfer=transfer_id,
                    product=product,
                    quantity=addition.quantity,
                    product_code=addition.code,
                    product_name=addition.name,
                )
            )
        if additions:
            inv._touch(transfer, current)
        await db.commit()
    await db.refresh(transfer)
    return transfer


# ── In-transit view (R10) ─────────────────────────────────────────────────────


async def in_transit_view(db: AsyncSession, facility: int) -> InTransitResponse:
    """What is on its way to or from the facility: per outstanding line and per product
    (FR-045). It reads the transfers, not the transit balance, which also holds delivery stock."""
    origin = aliased(Warehouse)
    destination = aliased(Warehouse)
    rows = (
        await db.execute(
            select(
                InventoryTransfer.inventory_transfer_id,
                InventoryTransfer.serial,
                InventoryTransfer.warehouse,
                InventoryTransfer.warehouse_to,
                InventoryTransferDetail.transfer_detail_id,
                InventoryTransferDetail.product,
                InventoryTransferDetail.product_code,
                InventoryTransferDetail.product_name,
                InventoryTransferDetail.quantity,
                InventoryTransferDetail.quantity_received,
            )
            .join(
                InventoryTransferDetail,
                InventoryTransferDetail.transfer == InventoryTransfer.inventory_transfer_id,
            )
            .join(origin, origin.warehouse_id == InventoryTransfer.warehouse)
            .join(destination, destination.warehouse_id == InventoryTransfer.warehouse_to)
            .where(
                InventoryTransfer.in_transit.is_(True),
                InventoryTransfer.cancelled.is_(False),
                or_(origin.facility == facility, destination.facility == facility),
            )
            .order_by(
                InventoryTransfer.inventory_transfer_id, InventoryTransferDetail.transfer_detail_id
            )
        )
    ).all()

    lines = []
    products: dict[int, InTransitProduct] = {}
    for row in rows:
        outstanding = _outstanding(row)
        if outstanding <= 0:
            continue
        lines.append(
            InTransitLine(
                inventory_transfer_id=row.inventory_transfer_id,
                serial=row.serial,
                warehouse=row.warehouse,
                warehouse_to=row.warehouse_to,
                transfer_detail_id=row.transfer_detail_id,
                product=row.product,
                product_code=row.product_code,
                product_name=row.product_name,
                quantity_outstanding=outstanding,
            )
        )
        total = products.setdefault(
            row.product,
            InTransitProduct(
                product=row.product,
                product_code=row.product_code,
                product_name=row.product_name,
                quantity=Decimal(0),
            ),
        )
        total.quantity += outstanding
    return InTransitResponse(
        facility=facility,
        products=[products[product] for product in sorted(products)],
        lines=lines,
    )
