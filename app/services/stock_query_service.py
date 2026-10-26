"""Stock by warehouse and by product, with reservations and incoming transfers (spec 030, R5).

Three grouped sums, merged in Python: on hand from the ledger, reserved from sales-order
reservations (type 11 only) and incoming from the outstanding lines of transfers in transit.
Nothing here joins labels, so a product with several labels still counts once (FR-022).
"""

from collections.abc import Iterable, Sequence
from decimal import Decimal
from typing import NamedTuple

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TransactionType
from app.models.core import Warehouse
from app.models.inventory import (
    InventoryTransfer,
    InventoryTransferDetail,
    LotSerialRqmt,
    LotSerialTracking,
)
from app.models.product import Product
from app.schemas.inventory import InTransitStock, ProductStock, StockRow
from app.services import stock_ledger, warehouse_service

ZERO = Decimal(0)
_CHUNK = 500


class Figures(NamedTuple):
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal


# ── Pure helpers ──────────────────────────────────────────────────────────────


def merge_stock(
    on_hand: dict[int, Decimal], reserved: dict[int, Decimal], incoming: dict[int, Decimal]
) -> dict[int, Figures]:
    """One `Figures` per product named by any of the three sums; a missing figure is zero."""
    merged: dict[int, Figures] = {}
    for product in on_hand.keys() | reserved.keys() | incoming.keys():
        held = on_hand.get(product, ZERO)
        claimed = reserved.get(product, ZERO)
        merged[product] = Figures(held, claimed, held - claimed, incoming.get(product, ZERO))
    return merged


def keep_row(figures: Figures, *, include_zero: bool) -> bool:
    return include_zero or any(value != 0 for value in figures)


def split_transit(
    on_hand: dict[int, Decimal], transit: dict[int, int]
) -> tuple[dict[int, Decimal], list[InTransitStock]]:
    """Separate in-transit warehouses (`transit`: warehouse -> facility) from the rest.

    A transit warehouse with a zero balance is dropped; the rest come back ordered by facility.
    """
    regular = {w: v for w, v in on_hand.items() if w not in transit}
    held = [
        InTransitStock(facility=transit[w], warehouse=w, on_hand=v)
        for w, v in on_hand.items()
        if w in transit and v != 0
    ]
    return regular, sorted(held, key=lambda t: (t.facility, t.warehouse))


def sort_and_page[T](
    products: Iterable[T], codes: dict[T, str | None], *, skip: int, limit: int
) -> tuple[list[T], int]:
    """Sort by product code (ties by id), then slice. Returns the page and the full count."""
    ordered = sorted(products, key=lambda p: (codes.get(p) or '', p))
    return ordered[skip : skip + limit], len(ordered)


# ── Queries ───────────────────────────────────────────────────────────────────


def _in_transit_to() -> list[ColumnElement[bool]]:
    return [
        InventoryTransfer.completed.is_(True),
        InventoryTransfer.cancelled.is_(False),
        InventoryTransfer.in_transit.is_(True),
    ]


def _outstanding():  # noqa: ANN202
    return InventoryTransferDetail.quantity - func.coalesce(
        InventoryTransferDetail.quantity_received, 0
    )


async def _names(db: AsyncSession, products: Iterable[int]) -> dict[int, tuple[str, str]]:
    ids = list(products)
    found: dict[int, tuple[str, str]] = {}
    for start in range(0, len(ids), _CHUNK):
        rows = await db.execute(
            select(Product.product_id, Product.code, Product.name).where(
                Product.product_id.in_(ids[start : start + _CHUNK])
            )
        )
        found.update({pid: (code, name) for pid, code, name in rows.all()})
    return found


async def list_stock(
    db: AsyncSession,
    *,
    warehouse: int,
    q: str | None,
    include_zero: bool,
    skip: int,
    limit: int,
) -> tuple[Sequence[StockRow], int]:
    await warehouse_service.assert_sellable(db, warehouse)

    matching: list[ColumnElement[bool]] = []
    if q:
        term = f'%{q}%'
        found = select(Product.product_id).where(
            or_(Product.code.ilike(term), Product.name.ilike(term))
        )
        matching = [found]

    def narrowed(column):  # noqa: ANN001, ANN202
        return [column.in_(matching[0])] if matching else []

    on_hand = {
        p: t
        for p, t in (
            await db.execute(
                select(LotSerialTracking.product, func.sum(LotSerialTracking.quantity))
                .where(
                    LotSerialTracking.warehouse == warehouse,
                    *narrowed(LotSerialTracking.product),
                )
                .group_by(LotSerialTracking.product)
            )
        ).all()
    }
    reserved = {
        p: t
        for p, t in (
            await db.execute(
                select(LotSerialRqmt.product, func.sum(LotSerialRqmt.quantity))
                .where(
                    LotSerialRqmt.source == int(TransactionType.SALES_ORDER_RESERVATION),
                    LotSerialRqmt.warehouse == warehouse,
                    *narrowed(LotSerialRqmt.product),
                )
                .group_by(LotSerialRqmt.product)
            )
        ).all()
    }
    incoming = {
        p: t
        for p, t in (
            await db.execute(
                select(InventoryTransferDetail.product, func.sum(_outstanding()))
                .join(
                    InventoryTransfer,
                    InventoryTransfer.inventory_transfer_id == InventoryTransferDetail.transfer,
                )
                .where(
                    *_in_transit_to(),
                    InventoryTransfer.warehouse_to == warehouse,
                    *narrowed(InventoryTransferDetail.product),
                )
                .group_by(InventoryTransferDetail.product)
            )
        ).all()
    }

    merged = {
        p: f
        for p, f in merge_stock(on_hand, reserved, incoming).items()
        if keep_row(f, include_zero=include_zero)
    }
    names = await _names(db, merged)
    page, total = sort_and_page(
        merged, {p: names.get(p, (None, None))[0] for p in merged}, skip=skip, limit=limit
    )
    rows = [
        StockRow(
            product=p,
            product_code=names.get(p, (None, None))[0],
            product_name=names.get(p, (None, None))[1],
            warehouse=warehouse,
            on_hand=merged[p].on_hand,
            reserved=merged[p].reserved,
            available=merged[p].available,
            incoming=merged[p].incoming,
        )
        for p in page
    ]
    return rows, total


async def product_stock(db: AsyncSession, product: int) -> ProductStock:
    row = await db.get(Product, product)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')

    held = await stock_ledger.on_hand_by_warehouse(db, products={product})
    claimed = await stock_ledger.reserved_by_warehouse(db, products={product})
    owed = {
        w: t
        for w, t in (
            await db.execute(
                select(InventoryTransfer.warehouse_to, func.sum(_outstanding()))
                .join(
                    InventoryTransferDetail,
                    InventoryTransfer.inventory_transfer_id == InventoryTransferDetail.transfer,
                )
                .where(*_in_transit_to(), InventoryTransferDetail.product == product)
                .group_by(InventoryTransfer.warehouse_to)
            )
        ).all()
    }

    transit = {
        w: f
        for w, f in (
            await db.execute(
                select(Warehouse.warehouse_id, Warehouse.facility).where(
                    Warehouse.in_transit.is_(True)
                )
            )
        ).all()
    }
    on_hand_by_w = {w: t for (_, w), t in held.items()}
    regular, in_transit = split_transit(on_hand_by_w, transit)
    merged = merge_stock(
        regular,
        {w: t for (_, w), t in claimed.items()},
        {w: t for w, t in owed.items() if w not in transit},
    )
    warehouses = [
        StockRow(
            product=product,
            product_code=row.code,
            product_name=row.name,
            warehouse=w,
            on_hand=f.on_hand,
            reserved=f.reserved,
            available=f.available,
            incoming=f.incoming,
        )
        for w, f in sorted(merged.items())
        if keep_row(f, include_zero=False)
    ]
    return ProductStock(product=product, warehouses=warehouses, in_transit=in_transit)
