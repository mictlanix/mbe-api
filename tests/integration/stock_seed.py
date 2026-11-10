"""Dated ledger entries for the inventory analytics tests (spec 038).

`inventory_seed.stock` posts at the current time, through `stock_ledger.post_movement`; stock as of
a past date needs entries placed on chosen days, so these are inserted straight from the model.
The baseline's facility 1 has warehouse 1 and its transit warehouse 2; `setup_inventory` adds a
second facility with its own pair.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TransactionType
from app.models.inventory import LotSerialTracking

T = TransactionType


async def entry(
    db: AsyncSession,
    product: int,
    warehouse: int,
    qty: str,
    at: datetime,
    *,
    source: TransactionType = T.INVENTORY_ADJUSTMENT,
    reference: int = 0,
) -> int:
    """One ledger entry of `qty` (signed) on `at`; returns its id."""
    row = LotSerialTracking(
        source=int(source), reference=reference, date=at, warehouse=warehouse, product=product,
        quantity=Decimal(qty), lot_number=None, expiration_date=None, serial_number=None,
    )  # fmt: skip
    db.add(row)
    await db.commit()
    return row.lot_serial_tracking_id
