"""Warehouse receipts (spec 029, US1): the shared mechanics, plus what is only a receipt's.

Draft, line, numbering, confirm and cancel rules live in `inventory_documents`; each wrapper here
runs one of them for `RECEIPT` and returns the receipt ready for its response. The purchase-order
view of a line and its over-receipt warnings (R7) are added in `attach_derived`.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.models.inventory import InventoryReceipt
from app.schemas.inventory import LineAdd, LineUpdate, ReceiptCreate, ReceiptUpdate
from app.services import inventory_documents as inv


async def attach_derived(db: AsyncSession, receipt: InventoryReceipt) -> InventoryReceipt:
    """Status, lines, and each line's purchase-order figures plus the receipt's warnings.

    Not linked to an order, or not yet computed: no figure and no warning (R7; US4 fills them).
    """
    await inv.attach(db, inv.RECEIPT, receipt)
    for line in receipt.__dict__['lines']:
        line.__dict__['quantity_received_elsewhere'] = None
    receipt.__dict__['warnings'] = []
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
