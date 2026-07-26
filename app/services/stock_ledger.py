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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TransactionType
from app.models.inventory import LotSerialTracking


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
