"""The kardex: one product's ledger in one warehouse over a date range (spec 030, US4, R6).

One row per ledger entry, never netted by day as the current system does (FR-030), with the
running balance from the opening sum. Each entry resolves to its document with one `IN` query per
type over the references in the range; an entry that cannot be resolved shows raw (FR-032).
"""

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.enums import TransactionType
from app.models.core import Warehouse
from app.models.inventory import (
    InventoryCount,
    InventoryIssue,
    InventoryReceipt,
    InventoryTransfer,
    LotSerialTracking,
)
from app.models.logistics import DeliveriesItinerary, DeliveryOrder
from app.models.product import Product
from app.models.purchases import PurchaseOrder
from app.models.sales import CustomerRefund, CustomerRefundDetail, SalesOrder
from app.schemas.inventory import Kardex, KardexDocument, KardexRow

#: FR-031: a range is at most this many days, both ends included.
MAX_DAYS = 366

T = TransactionType
#: R6's table: the document each type references. Types 6 and 10 show the id only.
_FOLIO = {
    T.SALES_ORDER: SalesOrder,
    T.CUSTOMER_REFUND: CustomerRefund,
    T.INVENTORY_ISSUE: InventoryIssue,
    T.INVENTORY_RECEIPT: InventoryReceipt,
    T.INVENTORY_TRANSFER: InventoryTransfer,
    # The current system's count adjustments reference the receipt it raised.
    T.INVENTORY_ADJUSTMENT: InventoryReceipt,
    T.PHYSICAL_COUNT: InventoryCount,
    T.DELIVERY_PICKUP: DeliveryOrder,
}
_ID_ONLY = {T.PURCHASE_ORDER: PurchaseOrder, T.DELIVERY_ORDER: DeliveriesItinerary}
RESOLVABLE = {int(t) for t in (*_FOLIO, *_ID_ONLY)}


class Document(NamedTuple):
    """What a type's query found for one reference."""

    facility: int | None = None
    serial: int | None = None
    #: A refund's products: its entries resolve only for a product on its lines (R6).
    products: frozenset[int] | None = None


class Movements(NamedTuple):
    rows: list[KardexRow]
    total_in: Decimal
    total_out: Decimal
    closing: Decimal


# ── Pure helpers ──────────────────────────────────────────────────────────────


def range_bounds(from_: date, to: date) -> tuple[datetime, datetime]:
    """`[from, to + 1 day)`: the end is inclusive of its whole day (FR-031)."""
    if to < from_:
        raise DomainError(ErrorCode.KARDEX_RANGE_REVERSED, 'The range ends before it starts')
    if (to - from_).days + 1 > MAX_DAYS:
        raise DomainError(
            ErrorCode.KARDEX_RANGE_TOO_LONG,
            f'The range may not exceed {MAX_DAYS} days',
            {'max_days': MAX_DAYS},
        )
    return datetime.combine(from_, time()), datetime.combine(to + timedelta(days=1), time())


#: Type 10 keeps its enum name, but since counter pickups moved to 13 (FR-034) it only ever
#: references an itinerary, so the kardex says so.
_LABELS = {int(TransactionType.DELIVERY_ORDER): 'delivery_itinerary'}


def source_name(source: int) -> str:
    if source in _LABELS:
        return _LABELS[source]
    try:
        return TransactionType(source).name.lower()
    except ValueError:
        return 'unknown'


def references_by_type(entries: Iterable[Any]) -> dict[int, set[int]]:
    """The references to look up, per resolvable type. Reference 0 is no document."""
    references: dict[int, set[int]] = defaultdict(set)
    for entry in entries:
        if entry.source in RESOLVABLE and entry.reference:
            references[entry.source].add(entry.reference)
    return dict(references)


def resolve(
    entries: Iterable[Any], documents_by_type: Mapping[int, Mapping[int, Document]]
) -> dict[int, KardexDocument]:
    """Each resolvable entry's document, by entry id. Unresolved entries are left out."""
    resolved = {}
    for entry in entries:
        if entry.source not in RESOLVABLE:
            continue
        found = documents_by_type.get(entry.source, {}).get(entry.reference)
        if found is None or not entry.reference:
            continue
        if found.products is not None and entry.product not in found.products:
            continue
        resolved[entry.lot_serial_tracking_id] = KardexDocument(
            type=entry.source, id=entry.reference, facility=found.facility, serial=found.serial
        )
    return resolved


def kardex_rows(
    opening: Decimal,
    entries: Iterable[Any],
    documents: Mapping[int, KardexDocument] | None = None,
) -> Movements:
    """The rows with their running balance, the totals in and out, and the closing balance."""
    documents = documents or {}
    balance, total_in, total_out = opening, Decimal(0), Decimal(0)
    rows = []
    for entry in entries:
        quantity = entry.quantity
        quantity_in = quantity if quantity > 0 else Decimal(0)
        quantity_out = -quantity if quantity < 0 else Decimal(0)
        balance += quantity
        total_in += quantity_in
        total_out += quantity_out
        rows.append(
            KardexRow(
                lot_serial_tracking_id=entry.lot_serial_tracking_id,
                date=entry.date,
                source=entry.source,
                source_name=source_name(entry.source),
                document=documents.get(entry.lot_serial_tracking_id),
                reference=entry.reference,
                quantity_in=quantity_in,
                quantity_out=quantity_out,
                balance=balance,
            )
        )
    return Movements(rows, total_in, total_out, balance)


# ── Queries ───────────────────────────────────────────────────────────────────


async def _documents(
    db: AsyncSession, references: Mapping[int, set[int]], products: set[int]
) -> dict[int, dict[int, Document]]:
    """One `IN` query per type (a second one for refunds' lines)."""
    found: dict[int, dict[int, Document]] = {}
    for source, ids in references.items():
        if source in _ID_ONLY:
            model = _ID_ONLY[T(source)]
            key = model.__mapper__.primary_key[0]
            rows = await db.execute(select(key).where(key.in_(ids)))
            found[source] = {row[0]: Document() for row in rows}
            continue
        model = _FOLIO[T(source)]
        key = model.__mapper__.primary_key[0]
        rows = await db.execute(select(key, model.facility, model.serial).where(key.in_(ids)))
        found[source] = {row[0]: Document(row[1], row[2]) for row in rows}

    refunds = found.get(int(T.CUSTOMER_REFUND))
    if refunds:
        lines: dict[int, set[int]] = defaultdict(set)
        rows = await db.execute(
            select(CustomerRefundDetail.customer_refund, CustomerRefundDetail.product).where(
                CustomerRefundDetail.customer_refund.in_(refunds),
                CustomerRefundDetail.product.in_(products),
            )
        )
        for refund, product in rows:
            lines[refund].add(product)
        for refund, document in refunds.items():
            refunds[refund] = document._replace(products=frozenset(lines[refund]))
    return found


async def kardex(
    db: AsyncSession, *, product: int, warehouse: int, from_: date, to: date
) -> Kardex:
    start, end = range_bounds(from_, to)
    if await db.get(Product, product) is None:
        raise not_found(Product, product)
    if await db.get(Warehouse, warehouse) is None:
        raise not_found(Warehouse, warehouse)

    ledger = (LotSerialTracking.product == product, LotSerialTracking.warehouse == warehouse)
    opening = (
        await db.execute(
            select(func.coalesce(func.sum(LotSerialTracking.quantity), 0)).where(
                *ledger, LotSerialTracking.date < start
            )
        )
    ).scalar_one()
    entries = (
        (
            await db.execute(
                select(LotSerialTracking)
                .where(*ledger, LotSerialTracking.date >= start, LotSerialTracking.date < end)
                .order_by(LotSerialTracking.date, LotSerialTracking.lot_serial_tracking_id)
            )
        )
        .scalars()
        .all()
    )

    documents = await _documents(db, references_by_type(entries), {product})
    movements = kardex_rows(Decimal(opening), entries, resolve(entries, documents))
    return Kardex(
        product=product,
        warehouse=warehouse,
        from_=from_,
        to=to,
        opening=Decimal(opening),
        rows=movements.rows,
        total_in=movements.total_in,
        total_out=movements.total_out,
        closing=movements.closing,
    )
