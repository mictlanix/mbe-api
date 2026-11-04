"""Warehouse receipts (spec 029, US1 and US4): the shared mechanics, plus what is only a receipt's.

Draft, line, numbering, confirm and cancel rules live in `inventory_documents`; each wrapper here
runs one of them for `RECEIPT` and returns the receipt ready for its response. The purchase-order
view of a line and its over-receipt warnings (R7) are added in `attach_derived`; the complementary
receipt (R8) is `complementary_receipt`.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.models.inventory import InventoryReceipt, InventoryReceiptDetail
from app.models.product import Product
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail
from app.schemas.inventory import LineAdd, LineUpdate, ReceiptCreate, ReceiptUpdate
from app.services import inventory_documents as inv

# ── Pure helpers (R7, R8) ─────────────────────────────────────────────────────


def _plain(quantity: Decimal) -> str:
    return f'{quantity.normalize():f}'


def over_receipt_warnings(
    lines: Iterable[Any],
    ordered_by_po_line: dict[int, Decimal],
    received_elsewhere: dict[int, Decimal],
) -> list[str]:
    """One message per order line this receipt takes above the order line's own quantity.

    This receipt's linked lines are summed per order line: a legacy receipt may link one twice.
    The stored `quantity_ordered` is never read; legacy complementary receipts put the remaining
    quantity in it (R7).
    """
    here: dict[int, Decimal] = defaultdict(Decimal)
    codes: dict[int, str] = {}
    for line in lines:
        order_line = line.purchase_order_detail
        if order_line is None or order_line not in ordered_by_po_line:
            continue
        here[order_line] += line.quantity
        codes.setdefault(order_line, line.product_code or str(line.product))

    warnings = []
    for order_line, quantity in here.items():
        ordered = ordered_by_po_line[order_line]
        total = received_elsewhere.get(order_line, Decimal(0)) + quantity
        if total > ordered:
            warnings.append(
                f'Over-received {codes[order_line]} by {_plain(total - ordered)}: '
                f'{_plain(total)} received of {_plain(ordered)} ordered'
            )
    return warnings


def remaining_lines(
    order_lines: Iterable[Any], received: dict[int, Decimal]
) -> list[tuple[Any, Decimal]]:
    """`(order line, remaining)` for each order line with something left to receive."""
    result = []
    for order_line in order_lines:
        remaining = order_line.quantity - received.get(
            order_line.purchase_order_detail_id, Decimal(0)
        )
        if remaining > 0:
            result.append((order_line, remaining))
    return result


def complementary_refusal(
    order: Any | None, remaining: Sequence[Any]
) -> tuple[str, str] | None:
    """Why no complementary receipt can be given for `order`, or None when it can (FR-023).

    Returns `(detail, reason)`: today's English sentence and the `reason` param's enum value.
    """
    if order is None:
        return 'The receipt has no purchase order', 'no_purchase_order'
    if order.cancelled:
        return 'The purchase order is cancelled', 'purchase_order_cancelled'
    if not order.completed:
        return 'The purchase order is not completed', 'purchase_order_not_completed'
    if not remaining:
        return (
            "The purchase order is fully received for this receipt's warehouse",
            'purchase_order_fully_received',
        )
    return None


def _complementary_unavailable(refusal: tuple[str, str]) -> DomainError:
    detail, reason = refusal
    return DomainError(
        ErrorCode.INVENTORY_RECEIPT_COMPLEMENTARY_UNAVAILABLE, detail, {'reason': reason}
    )


# ── Purchase-order view (R7) ──────────────────────────────────────────────────


async def _received_elsewhere(
    db: AsyncSession, purchase_order_details: Iterable[int], exclude_receipt: int | None
) -> dict[int, Decimal]:
    """Per order line, what completed, non-cancelled receipts other than `exclude_receipt` hold."""
    ids = set(purchase_order_details)
    if not ids:
        return {}
    detail = InventoryReceiptDetail
    query = (
        select(detail.purchase_order_detail, func.sum(detail.quantity))
        .join(InventoryReceipt, InventoryReceipt.inventory_receipt_id == detail.receipt)
        .where(
            detail.purchase_order_detail.in_(ids),
            InventoryReceipt.completed.is_(True),
            InventoryReceipt.cancelled.is_(False),
        )
        .group_by(detail.purchase_order_detail)
    )
    if exclude_receipt is not None:
        query = query.where(InventoryReceipt.inventory_receipt_id != exclude_receipt)
    return {order_line: total for order_line, total in (await db.execute(query)).all()}


async def attach_derived(db: AsyncSession, receipt: InventoryReceipt) -> InventoryReceipt:
    """Status, lines, and each linked line's purchase-order figures plus the receipt's warnings.

    A linked line shows its order line's own quantity, read live, and what other completed
    receipts hold of it; an unlinked line has no figure and gives no warning (R7, FR-024).
    """
    await inv.attach(db, inv.RECEIPT, receipt)
    lines = receipt.__dict__['lines']
    linked = {line.purchase_order_detail for line in lines} - {None}
    ordered: dict[int, Decimal] = {}
    if linked:
        rows = await db.execute(
            select(
                PurchaseOrderDetail.purchase_order_detail_id, PurchaseOrderDetail.quantity
            ).where(PurchaseOrderDetail.purchase_order_detail_id.in_(linked))
        )
        ordered = dict(rows.all())
    elsewhere = await _received_elsewhere(db, linked, receipt.inventory_receipt_id)

    for line in lines:
        order_line = line.purchase_order_detail
        if order_line is None:
            line.__dict__['quantity_received_elsewhere'] = None
            continue
        if order_line in ordered:
            # The response's figure only: no history, so nothing writes it back to the row.
            set_committed_value(line, 'quantity_ordered', ordered[order_line])
        line.__dict__['quantity_received_elsewhere'] = elsewhere.get(order_line, Decimal(0))
    # The cost warnings of the confirm or cancel that returned this receipt, if any (spec 031).
    receipt.__dict__['warnings'] = over_receipt_warnings(
        lines, ordered, elsewhere
    ) + receipt.__dict__.get('cost_warnings', [])
    return receipt


async def create(
    db: AsyncSession, data: ReceiptCreate, *, current: CurrentUser
) -> InventoryReceipt:
    receipt = await inv.create_draft(
        db, inv.RECEIPT, warehouse=data.warehouse, comment=data.comment, current=current
    )
    return await attach_derived(db, receipt)


async def update(
    db: AsyncSession, receipt_id: int, data: ReceiptUpdate, *, current: CurrentUser
) -> InventoryReceipt:
    """A receipt of a purchase order keeps its warehouse: its lines are that warehouse's order
    lines, and the complementary receipt finds it by order and warehouse (spec 031, R7). The
    order link is never changed once set, so the plain read is current."""
    if data.warehouse is not None:
        existing = await db.get(InventoryReceipt, receipt_id)
        if (
            existing is not None
            and existing.purchase_order is not None
            and data.warehouse != existing.warehouse
        ):
            raise DomainError(
                ErrorCode.INVENTORY_RECEIPT_WAREHOUSE_FIXED,
                "A purchase order's receipt keeps its warehouse",
            )
    receipt = await inv.update_header(db, inv.RECEIPT, receipt_id, data, current=current)
    return await attach_derived(db, receipt)


async def add_line(
    db: AsyncSession, receipt_id: int, data: LineAdd, *, current: CurrentUser
) -> InventoryReceipt:
    receipt = await inv.add_line(
        db,
        inv.RECEIPT,
        receipt_id,
        product=data.product,
        quantity=data.quantity,
        current=current,
    )
    return await attach_derived(db, receipt)


async def update_line(
    db: AsyncSession, receipt_id: int, line_id: int, data: LineUpdate, *, current: CurrentUser
) -> InventoryReceipt:
    receipt = await inv.update_line(
        db, inv.RECEIPT, receipt_id, line_id, quantity=data.quantity, current=current
    )
    return await attach_derived(db, receipt)


async def remove_line(
    db: AsyncSession, receipt_id: int, line_id: int, *, current: CurrentUser
) -> InventoryReceipt:
    receipt = await inv.remove_line(db, inv.RECEIPT, receipt_id, line_id, current=current)
    return await attach_derived(db, receipt)


async def confirm(db: AsyncSession, receipt_id: int, *, current: CurrentUser) -> InventoryReceipt:
    receipt = await inv.confirm_simple(db, inv.RECEIPT, receipt_id, current=current)
    return await attach_derived(db, receipt)


async def cancel(db: AsyncSession, receipt_id: int, *, current: CurrentUser) -> InventoryReceipt:
    receipt = await inv.cancel(db, inv.RECEIPT, receipt_id, current=current)
    return await attach_derived(db, receipt)


# ── Complementary receipt (R8) ────────────────────────────────────────────────


async def _reread(db: AsyncSession, receipt_id: int) -> InventoryReceipt:
    return (
        await db.execute(
            select(InventoryReceipt)
            .where(InventoryReceipt.inventory_receipt_id == receipt_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _create_complementary(
    db: AsyncSession,
    source: InventoryReceipt,
    remaining: Sequence[tuple[PurchaseOrderDetail, Decimal]],
    *,
    current: CurrentUser,
) -> int:
    """A draft of the source's order and warehouse, one linked line per remaining order line.

    `quantity_ordered` is the order line's own quantity, not the remaining one (R8).
    """
    products = {
        row.product_id: row
        for row in (
            await db.execute(
                select(Product).where(
                    Product.product_id.in_({line.product for line, _ in remaining})
                )
            )
        )
        .scalars()
        .all()
    }
    now = local_now()
    draft = InventoryReceipt(
        facility=source.facility,
        warehouse=source.warehouse,
        purchase_order=source.purchase_order,
        creator=current.employee_id,
        updater=current.employee_id,
        creation_time=now,
        modification_time=now,
        completed=False,
        cancelled=False,
    )
    db.add(draft)
    await db.flush()
    for order_line, quantity in remaining:
        product = products[order_line.product]
        db.add(
            InventoryReceiptDetail(
                receipt=draft.inventory_receipt_id,
                product=order_line.product,
                purchase_order_detail=order_line.purchase_order_detail_id,
                quantity=quantity,
                quantity_ordered=order_line.quantity,
                product_code=product.code,
                product_name=product.name,
            )
        )
    return draft.inventory_receipt_id


async def complementary_receipt(
    db: AsyncSession, receipt: InventoryReceipt, *, current: CurrentUser
) -> tuple[InventoryReceipt, bool]:
    """The open draft of the receipt's order and warehouse, or a new one of what is left (FR-023).

    `True` when the draft is new. The purchase-order row is locked before any ordinary read, so two
    requests for one order queue and the second finds the first one's draft (R8).
    """
    order_id = receipt.purchase_order
    if order_id is None:
        raise _complementary_unavailable(complementary_refusal(None, []))

    async with inv.busy_as_conflict(db):
        await db.commit()
        order = (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.purchase_order_id == order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        source = await _reread(db, receipt.inventory_receipt_id)
        if source.cancelled:
            raise DomainError(
                ErrorCode.INVENTORY_DOCUMENT_ALREADY_CANCELLED,
                'The receipt is cancelled',
                {'resource': 'inventory_receipt'},
            )

        # Non-stockable order lines get no receipt line, as on confirm; confirming one would be
        # refused (spec 031, R7).
        order_lines = (
            (
                await db.execute(
                    select(PurchaseOrderDetail)
                    .join(Product, Product.product_id == PurchaseOrderDetail.product)
                    .where(
                        PurchaseOrderDetail.purchase_order == order_id,
                        PurchaseOrderDetail.warehouse == source.warehouse,
                        Product.stockable.is_(True),
                    )
                    .order_by(PurchaseOrderDetail.purchase_order_detail_id)
                )
            )
            .scalars()
            .all()
        )
        received = await _received_elsewhere(
            db, [line.purchase_order_detail_id for line in order_lines], None
        )
        remaining = remaining_lines(order_lines, received)
        refusal = complementary_refusal(order, remaining)
        if refusal is not None:
            raise _complementary_unavailable(refusal)

        draft_id = (
            await db.execute(
                select(InventoryReceipt.inventory_receipt_id)
                .where(
                    InventoryReceipt.purchase_order == order_id,
                    InventoryReceipt.warehouse == source.warehouse,
                    InventoryReceipt.completed.is_(False),
                    InventoryReceipt.cancelled.is_(False),
                )
                .order_by(InventoryReceipt.inventory_receipt_id)
                .limit(1)
            )
        ).scalar_one_or_none()
        created = draft_id is None
        if created:
            draft_id = await _create_complementary(db, source, remaining, current=current)
        await db.commit()
    return await attach_derived(db, await _reread(db, draft_id)), created
