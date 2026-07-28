"""The inventory ledger — `lot_serial_tracking` — and the on-hand figure derived from it.

There is no stock-balance table. On-hand is the sum of the ledger, positive inbound and negative
outbound (research R4). The ledger is append-only: a sale writes a negative entry, and cancelling
that sale writes a *second*, positive entry rather than removing the first, so both remain visible
(FR-019a). SC-003 is verifiable from these rows alone.

Lot and serial numbers are deliberately left unset — capturing them belongs to the inventory
feature, not this one.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TransactionType
from app.models.inventory import LotSerialRqmt, LotSerialTracking


def post_movement(
    db: AsyncSession,
    *,
    source: TransactionType,
    reference: int,
    product: int,
    warehouse: int,
    quantity: Decimal,
    outbound: bool,
) -> LotSerialTracking:
    """Stage one ledger entry. The caller commits it with the rest of its transaction.

    `quantity` is always positive; `outbound` decides the sign. Letting callers pre-sign the
    quantity would make a missing minus indistinguishable from an intended inbound movement.
    """
    if quantity < 0:
        raise ValueError('quantity must be positive; use outbound to express direction')

    entry = LotSerialTracking(
        source=int(source),
        reference=reference,
        date=datetime.now(),
        warehouse=warehouse,
        product=product,
        quantity=-quantity if outbound else quantity,
        lot_number=None,
        expiration_date=None,
        serial_number=None,
    )
    db.add(entry)
    return entry


async def on_hand(db: AsyncSession, *, product: int, warehouse: int) -> Decimal:
    """Stock available for `product` in `warehouse`, straight from the ledger."""
    total = (
        await db.execute(
            select(func.sum(LotSerialTracking.quantity)).where(
                LotSerialTracking.product == product,
                LotSerialTracking.warehouse == warehouse,
            )
        )
    ).scalar_one_or_none()

    return total if total is not None else Decimal(0)


def reserve(
    db: AsyncSession,
    *,
    sales_order: int,
    product: int,
    warehouse: int,
    quantity: Decimal,
) -> LotSerialRqmt:
    """Stage a claim on stock that a confirmed sales order has not yet taken off the shelf.

    Confirming an order no longer writes an outbound ledger entry (FR-055), so the goods stay in
    `on_hand` until the truck leaves. This row is what stops them being promised twice in the
    meantime; `available()` is the figure the stock check must use.

    Reservations are namespaced under `SALES_ORDER_RESERVATION`, not `SALES_ORDER`. The legacy
    application wrote 2,609 rows under the latter before it stopped in January 2025 — reusing it
    would make `reserved()` count those as ours and `release_reservations()` delete them.
    """
    if quantity < 0:
        raise ValueError('quantity must be positive')

    entry = LotSerialRqmt(
        source=int(TransactionType.SALES_ORDER_RESERVATION),
        reference=sales_order,
        warehouse=warehouse,
        product=product,
        quantity=quantity,
    )
    db.add(entry)
    return entry


async def reserved(db: AsyncSession, *, product: int, warehouse: int) -> Decimal:
    """Stock spoken for by confirmed sales orders that have not yet departed."""
    total = (
        await db.execute(
            select(func.sum(LotSerialRqmt.quantity)).where(
                LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
                LotSerialRqmt.product == product,
                LotSerialRqmt.warehouse == warehouse,
            )
        )
    ).scalar_one_or_none()

    return total if total is not None else Decimal(0)


async def available(db: AsyncSession, *, product: int, warehouse: int) -> Decimal:
    """What can still be promised: on hand, less what is already claimed (FR-055a).

    The stock check at sales-order confirmation must use this rather than `on_hand`. Confirmation
    stopped decrementing on-hand, so an unreserved check would let one physical unit satisfy an
    unlimited number of orders — every confirmation succeeding, the shortfall surfacing only when
    the truck is loaded.
    """
    return await on_hand(db, product=product, warehouse=warehouse) - await reserved(
        db, product=product, warehouse=warehouse
    )


async def release_reservations(db: AsyncSession, *, sales_order: int) -> None:
    """Give back everything this order was holding.

    Whole-order release, for cancellation only. Departure must **not** use this: an order's
    reservations are one row per line, so releasing by reference alone would give back the lines
    that stayed behind as well as the ones that left.

    Deletion is safe because only rows this application wrote carry `SALES_ORDER_RESERVATION`.
    """
    await db.execute(
        delete(LotSerialRqmt).where(
            LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
            LotSerialRqmt.reference == sales_order,
        )
    )


async def release_reservation(
    db: AsyncSession,
    *,
    sales_order: int,
    product: int,
    warehouse: int,
    quantity: Decimal,
) -> Decimal:
    """Release exactly `quantity` of one product's claim, leaving the rest of the order alone.

    What departure and counter pickup need: those consume part of an order, and the part that has
    not moved must keep its claim or it becomes available to sell twice over.

    Consumes across the order's rows for this product and warehouse, deleting a row once it is
    exhausted. Returns the amount actually released, which is less than requested only when the
    order was holding less than the caller thought — that is worth surfacing rather than hiding,
    so callers can assert on it.
    """
    if quantity <= 0:
        return Decimal(0)

    rows = (
        (
            await db.execute(
                select(LotSerialRqmt)
                .where(
                    LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
                    LotSerialRqmt.reference == sales_order,
                    LotSerialRqmt.product == product,
                    LotSerialRqmt.warehouse == warehouse,
                )
                .order_by(LotSerialRqmt.lot_serial_rqmt_id)
            )
        )
        .scalars()
        .all()
    )

    outstanding = quantity
    for row in rows:
        if outstanding <= 0:
            break
        taken = min(row.quantity, outstanding)
        row.quantity -= taken
        outstanding -= taken
        if row.quantity <= 0:
            await db.delete(row)

    return quantity - outstanding


async def on_hand_by_warehouse(
    db: AsyncSession, *, products: set[int]
) -> dict[tuple[int, int], Decimal]:
    """On-hand for many products at once, keyed by `(product, warehouse)`.

    One aggregate query for the whole set. The per-product form is fine for a confirmation
    checking a handful of lines; a product search asking it per product per warehouse turns a
    single screen into dozens of round trips.
    """
    if not products:
        return {}

    rows = (
        await db.execute(
            select(
                LotSerialTracking.product,
                LotSerialTracking.warehouse,
                func.sum(LotSerialTracking.quantity),
            )
            .where(LotSerialTracking.product.in_(products))
            .group_by(LotSerialTracking.product, LotSerialTracking.warehouse)
        )
    ).all()

    return {(product, warehouse): total for product, warehouse, total in rows}


async def reserved_by_warehouse(
    db: AsyncSession, *, products: set[int]
) -> dict[tuple[int, int], Decimal]:
    """Reserved quantity for many products at once, keyed by `(product, warehouse)`."""
    if not products:
        return {}

    rows = (
        await db.execute(
            select(
                LotSerialRqmt.product,
                LotSerialRqmt.warehouse,
                func.sum(LotSerialRqmt.quantity),
            )
            .where(
                LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
                LotSerialRqmt.product.in_(products),
            )
            .group_by(LotSerialRqmt.product, LotSerialRqmt.warehouse)
        )
    ).all()

    return {(product, warehouse): total for product, warehouse, total in rows}
