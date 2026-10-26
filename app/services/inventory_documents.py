"""Mechanics shared by warehouse receipts, issues and transfers (spec 029, FR-001 to FR-013).

The three documents have the same draft, line, numbering, cancel and list rules, so they are
written once here and told apart by a `Kind`. What only one document has (purchase-order view,
transit receive) lives in its own service.

**Locking (research R1, R2).** Every step that changes a document runs `begin_locked` first:
plain pre-reads, a commit that ends the request's opening transaction, the document row
`FOR UPDATE`, then, for steps that move stock, the warehouse rows by primary key in id order. Every
ordinary read after that opens its snapshot after the locks, so it sees what the previous holder
committed: `stock_ledger` needs no locking reads and `MAX(serial)` none either. The facility's
transit warehouse is always in the warehouse lock; it is what queues numbering per facility. The
facility row is never locked.
"""

from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, has_any_privilege
from app.enums import AccessRight, SystemObject, TransactionType
from app.models.core import Warehouse
from app.models.inventory import (
    InventoryIssue,
    InventoryIssueDetail,
    InventoryReceipt,
    InventoryReceiptDetail,
    InventoryTransfer,
    InventoryTransferDetail,
    LotSerialTracking,
)
from app.models.product import Product
from app.schemas.sales_order import derive_status
from app.services import documents, stock_ledger, warehouse_service
from app.services.delivery_itinerary_service import _pos_warehouse

# ── Kinds ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Kind:
    header: type
    line: type
    #: The line's column naming its document, e.g. `receipt`.
    line_fk: str
    id_attr: str
    line_id_attr: str
    #: The header's warehouse columns: the origin first, then the destination for a transfer.
    warehouses: tuple[str, ...]
    #: True for transfers: the `in_transit` flag and postings through the transit warehouse.
    transit: bool
    transaction_type: TransactionType
    system_object: SystemObject
    label: str


RECEIPT = Kind(
    header=InventoryReceipt,
    line=InventoryReceiptDetail,
    line_fk='receipt',
    id_attr='inventory_receipt_id',
    line_id_attr='receipt_detail_id',
    warehouses=('warehouse',),
    transit=False,
    transaction_type=TransactionType.INVENTORY_RECEIPT,
    system_object=SystemObject.INVENTORY_RECEIPTS,
    label='Receipt',
)
ISSUE = Kind(
    header=InventoryIssue,
    line=InventoryIssueDetail,
    line_fk='issue',
    id_attr='inventory_issue_id',
    line_id_attr='issue_detail_id',
    warehouses=('warehouse',),
    transit=False,
    transaction_type=TransactionType.INVENTORY_ISSUE,
    system_object=SystemObject.INVENTORY_ISSUES,
    label='Issue',
)
TRANSFER = Kind(
    header=InventoryTransfer,
    line=InventoryTransferDetail,
    line_fk='transfer',
    id_attr='inventory_transfer_id',
    line_id_attr='transfer_detail_id',
    warehouses=('warehouse', 'warehouse_to'),
    transit=True,
    transaction_type=TransactionType.INVENTORY_TRANSFER,
    system_object=SystemObject.INVENTORY_TRANSFERS,
    label='Transfer',
)

# ── Pure helpers ──────────────────────────────────────────────────────────────


class Movement(NamedTuple):
    warehouse: int
    product: int
    #: Signed: negative is outbound.
    quantity: Decimal


def merge_line_quantity(
    kind: Kind, lines: Iterable[Any], product: int, quantity: Decimal
) -> tuple[Any | None, Decimal]:
    """The line a product's quantity goes to, and its new quantity (FR-003, R9).

    A product already on the document adds to its line; when a legacy draft repeats the product,
    to the oldest of them (the lowest id). `None` means a new line.
    """
    same = [line for line in lines if line.product == product]
    if not same:
        return None, quantity
    line = min(same, key=lambda candidate: getattr(candidate, kind.line_id_attr))
    return line, line.quantity + quantity


def per_product_totals(lines: Iterable[Any]) -> dict[int, Decimal]:
    totals: dict[int, Decimal] = defaultdict(Decimal)
    for line in lines:
        totals[line.product] += line.quantity
    return dict(totals)


def shortages(
    required: dict[int, Decimal], available: dict[int, Decimal]
) -> list[tuple[int, Decimal, Decimal]]:
    """`(product, available, required)` for every product short, by product id."""
    return [
        (product, available.get(product, Decimal(0)), needed)
        for product, needed in sorted(required.items())
        if available.get(product, Decimal(0)) < needed
    ]


def reversal_entries(entries: Iterable[Any]) -> list[Movement]:
    """The opposite of each ledger entry a document posted (FR-008, R3)."""
    return [Movement(entry.warehouse, entry.product, -entry.quantity) for entry in entries]


def net_outbound(reversal: Iterable[Movement]) -> dict[tuple[int, int], Decimal]:
    """What the reversal takes out, net, per `(warehouse, product)`; inbound and zero omitted."""
    net: dict[tuple[int, int], Decimal] = defaultdict(Decimal)
    for movement in reversal:
        net[(movement.warehouse, movement.product)] += movement.quantity
    return {key: -quantity for key, quantity in net.items() if quantity < 0}


def warehouses_to_lock(
    kind: Kind, document: Any, transit_map: dict[int, int], ledger_warehouses: Iterable[int]
) -> list[int]:
    """The warehouse rows a stock step locks, in the order it locks them (R1, R2).

    The transit warehouse of the document's own facility comes first in meaning, not in order: it
    is also the numbering lock (R1). It is the document's facility's, never the warehouse's current
    one, which a warehouse edit could change (R4).
    """
    transit = transit_map.get(document.facility)
    if transit is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Facility {document.facility} has no in-transit location',
        )
    header = {getattr(document, attr) for attr in kind.warehouses}
    return sorted({transit, *header, *ledger_warehouses})


# ── Lookups ───────────────────────────────────────────────────────────────────


async def get_document(db: AsyncSession, kind: Kind, document_id: int) -> Any | None:
    return await db.get(kind.header, document_id)


async def get_line(db: AsyncSession, kind: Kind, document: Any, line_id: int) -> Any | None:
    line = await db.get(kind.line, line_id)
    if line is None or getattr(line, kind.line_fk) != getattr(document, kind.id_attr):
        return None
    return line


async def _lines(db: AsyncSession, kind: Kind, document_id: int) -> list[Any]:
    line_id = getattr(kind.line, kind.line_id_attr)
    rows = await db.execute(
        select(kind.line)
        .where(getattr(kind.line, kind.line_fk) == document_id)
        .order_by(line_id)
        # A line loaded earlier in the request keeps its old values after begin_locked's commit
        # (expire_on_commit=False); re-read it from the row.
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


# ── Locks (R1, R2) ────────────────────────────────────────────────────────────


async def lock_document(db: AsyncSession, kind: Kind, document_id: int) -> Any:
    """The header row, locked, with every attribute re-read from it."""
    document = (
        await db.execute(
            select(kind.header)
            .where(getattr(kind.header, kind.id_attr) == document_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{kind.label} not found')
    return document


async def lock_warehouses(db: AsyncSession, ids: Iterable[int]) -> None:
    """One primary-key range lock, ascending, so two steps never take warehouses crosswise.

    By key only: a lock by `facility` and `in_transit` is planned on mbe_dev as an index merge
    with a filesort, which locks every warehouse of the facility in scan order (R2).
    """
    await db.execute(
        select(Warehouse.warehouse_id)
        .where(Warehouse.warehouse_id.in_(sorted(set(ids))))
        .order_by(Warehouse.warehouse_id)
        .with_for_update()
    )


async def _transit_map(db: AsyncSession) -> dict[int, int]:
    """Facility → its in-transit warehouse. Fixed rows (spec 013), so a plain read is current."""
    rows = await db.execute(
        select(Warehouse.facility, Warehouse.warehouse_id).where(Warehouse.in_transit.is_(True))
    )
    return {facility: warehouse for facility, warehouse in rows.all()}


async def _ledger_warehouses(db: AsyncSession, kind: Kind, document_id: int) -> set[int]:
    """Warehouses of the document's own entries. Append-only, and anything added later goes to
    a header warehouse or its transit, which the lock covers anyway (R2)."""
    rows = await db.execute(
        select(LotSerialTracking.warehouse)
        .where(
            LotSerialTracking.source == int(kind.transaction_type),
            LotSerialTracking.reference == document_id,
        )
        .distinct()
    )
    return set(rows.scalars().all())


async def begin_locked(
    db: AsyncSession, kind: Kind, document_id: int, *, stock: bool = False, ledger: bool = False
) -> Any:
    """Lock the document, and the warehouses when `stock`, before any ordinary read (R2).

    The commit ends the transaction the privilege check and the route's lookup opened; without it
    the reads after the locks would come from a snapshot older than them. The warehouse set is
    worked out from the locked row, never from a copy loaded before.
    """
    transit_map = await _transit_map(db) if stock else {}
    ledger_warehouses = await _ledger_warehouses(db, kind, document_id) if ledger else set()
    await db.commit()
    document = await lock_document(db, kind, document_id)
    if stock:
        await lock_warehouses(
            db, warehouses_to_lock(kind, document, transit_map, ledger_warehouses)
        )
    return document


async def next_serial(db: AsyncSession, model: type, facility: int) -> int:
    """MAX + 1 of the facility's serials on the document's own table (R1).

    Call it only after `begin_locked(stock=True)`. That lock includes the facility's transit
    warehouse, which every numbering step of the facility locks, whatever its warehouses and
    document type, so they queue on it. The read is then an ordinary one: its snapshot opens after
    the lock, so it sees the serial the previous holder committed — the mictlanix/mbe#45 race,
    where a snapshot older than the lock missed it, cannot happen.

    No locking read: a `MAX … FOR UPDATE` on the `(facility, serial)` index takes next-key and gap
    locks that deadlock two confirms of one facility, and a confirm against a step on the newest
    document. No facility row lock either: refunds and delivery stops take warehouse locks before
    the facility's, so taking them the other way round could deadlock with them.
    """
    highest = (
        await db.execute(select(func.max(model.serial)).where(model.facility == facility))
    ).scalar_one_or_none()
    return (highest or 0) + 1


#: MariaDB's deadlock victim and lock wait timeout.
_BUSY = frozenset({1213, 1205})


def _mariadb_code(error: DBAPIError) -> int | None:
    args = getattr(error.orig, 'args', ())
    return args[0] if args and isinstance(args[0], int) else None


@asynccontextmanager
async def busy_as_conflict(db: AsyncSession) -> AsyncIterator[None]:
    """Roll back any failure; a deadlock or lock wait timeout answers 409 (R2, known limits).

    Another writer's ledger insert can take warehouse locks in the opposite order; InnoDB then
    rolls one side back. The caller can retry, so that is a conflict rather than a 500.
    """
    try:
        yield
    except BaseException as error:
        await db.rollback()
        if isinstance(error, DBAPIError) and _mariadb_code(error) in _BUSY:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail='The warehouse is busy, retry'
            ) from error
        raise


# ── Rules ─────────────────────────────────────────────────────────────────────


def assert_facility(current: CurrentUser, *facilities: int) -> None:
    """FR-011, R6: a non-administrator acts only on documents of their own facility."""
    if not current.administrator and current.facility_id not in facilities:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail='Document belongs to another facility'
        )


async def assert_available(db: AsyncSession, required: dict[int, Decimal], warehouse: int) -> None:
    """Refuse when any product would go below what can still be promised in `warehouse`."""
    available = {
        product: await stock_ledger.available(db, product=product, warehouse=warehouse)
        for product in sorted(required)
    }
    short = shortages(required, available)
    if short:
        product, have, need = short[0]
        product_row = await db.get(Product, product)
        warehouse_row = await db.get(Warehouse, warehouse)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Not enough stock of {product_row.code} in {warehouse_row.code}: '
                f'{have} available, {need} required'
            ),
        )


async def _usable_warehouse(db: AsyncSession, warehouse_id: int) -> Warehouse:
    """Unknown is 404 and in transit 422 (FR-009); status is not checked (R9)."""
    await warehouse_service.assert_sellable(db, warehouse_id)
    return await db.get(Warehouse, warehouse_id)


def _assert_distinct(origin: int, destination: int) -> None:
    if origin == destination:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='The origin and destination warehouses must differ',
        )


def _touch(document: Any, current: CurrentUser) -> None:
    document.updater = current.employee_id
    document.modification_time = local_now()


# ── Drafts ────────────────────────────────────────────────────────────────────


async def create_draft(
    db: AsyncSession,
    kind: Kind,
    *,
    warehouse: int,
    warehouse_to: int | None = None,
    comment: str | None,
    current: CurrentUser,
) -> Any:
    origin = await _usable_warehouse(db, warehouse)
    values: dict[str, Any] = {}
    if kind.transit:
        await _usable_warehouse(db, warehouse_to)
        _assert_distinct(warehouse, warehouse_to)
        values['warehouse_to'] = warehouse_to

    now = local_now()
    document = kind.header(
        facility=origin.facility,
        warehouse=warehouse,
        creator=current.employee_id,
        updater=current.employee_id,
        creation_time=now,
        modification_time=now,
        completed=False,
        cancelled=False,
        comment=comment,
        **values,
    )
    db.add(document)
    await db.commit()
    await db.refresh(document)
    return document


async def update_header(
    db: AsyncSession, kind: Kind, document_id: int, data: BaseModel, *, current: CurrentUser
) -> Any:
    """Only the fields sent change. Moving the (origin) warehouse moves the facility with it."""
    changes = data.model_dump(exclude_unset=True)
    async with busy_as_conflict(db):
        document = await begin_locked(db, kind, document_id)
        documents.assert_editable(document)

        origin = changes.get('warehouse')
        destination = changes.get('warehouse_to') if kind.transit else None
        if origin is not None:
            document.facility = (await _usable_warehouse(db, origin)).facility
            document.warehouse = origin
        if destination is not None:
            await _usable_warehouse(db, destination)
            document.warehouse_to = destination
        if kind.transit:
            _assert_distinct(document.warehouse, document.warehouse_to)
        if 'comment' in changes:
            document.comment = changes['comment']

        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


# ── Lines ─────────────────────────────────────────────────────────────────────


async def add_line(
    db: AsyncSession,
    kind: Kind,
    document_id: int,
    *,
    product: int,
    quantity: Decimal,
    current: CurrentUser,
) -> Any:
    """Add to the product's line, or start one with code and name snapshots (FR-003, R9)."""
    async with busy_as_conflict(db):
        document = await begin_locked(db, kind, document_id)
        documents.assert_editable(document)

        row = await db.get(Product, product)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')
        if not row.stockable:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Product {row.code} is not stockable',
            )

        line, total = merge_line_quantity(
            kind, await _lines(db, kind, document_id), product, quantity
        )
        if line is None:
            values: dict[str, Any] = {
                kind.line_fk: document_id,
                'product': product,
                'quantity': total,
                'product_code': row.code,
                'product_name': row.name,
            }
            if kind is RECEIPT:
                # NOT NULL; a hand-added line orders nothing (R7).
                values['quantity_ordered'] = Decimal(0)
            db.add(kind.line(**values))
        else:
            line.quantity = total

        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


async def _locked_line(
    db: AsyncSession, kind: Kind, document_id: int, line_id: int
) -> tuple[Any, Any]:
    document = await begin_locked(db, kind, document_id)
    documents.assert_editable(document)
    line = await get_line(db, kind, document, line_id)
    if line is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    return document, line


async def update_line(
    db: AsyncSession,
    kind: Kind,
    document_id: int,
    line_id: int,
    *,
    quantity: Decimal,
    current: CurrentUser,
) -> Any:
    async with busy_as_conflict(db):
        document, line = await _locked_line(db, kind, document_id, line_id)
        line.quantity = quantity
        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


async def remove_line(
    db: AsyncSession, kind: Kind, document_id: int, line_id: int, *, current: CurrentUser
) -> Any:
    async with busy_as_conflict(db):
        document, line = await _locked_line(db, kind, document_id, line_id)
        await db.delete(line)
        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


# ── Confirm and cancel ────────────────────────────────────────────────────────


async def confirm_simple(
    db: AsyncSession, kind: Kind, document_id: int, *, current: CurrentUser
) -> Any:
    """Number a receipt or an issue and post one entry per line (FR-004 to FR-006).

    Any refusal rolls back, so no serial is consumed. Should the `(facility, serial)` unique index
    ever fire, the global `IntegrityError` handler answers 409 (R1).
    """
    async with busy_as_conflict(db):
        document = await begin_locked(db, kind, document_id, stock=True)
        # Another facility learns nothing about the document's state or lines.
        assert_facility(current, document.facility)
        documents.assert_editable(document)

        lines = await _lines(db, kind, document_id)
        if not lines:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=f'{kind.label} has no lines'
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

        serial = await next_serial(db, kind.header, document.facility)
        outbound = kind is ISSUE
        if outbound:
            await assert_available(db, per_product_totals(lines), document.warehouse)

        for line in lines:
            stock_ledger.post_movement(
                db,
                source=kind.transaction_type,
                reference=document_id,
                product=line.product,
                warehouse=document.warehouse,
                quantity=line.quantity,
                outbound=outbound,
            )

        # Last, after every read, so autoflush cannot write it before the checks pass.
        document.serial = serial
        document.completed = True
        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


async def cancel(db: AsyncSession, kind: Kind, document_id: int, *, current: CurrentUser) -> Any:
    """Cancel a draft, or reverse everything a confirmed document posted (FR-008, R3).

    The ledger pre-read runs for a draft too: it can be confirmed between the pre-read and the
    document lock. Transfer lines keep `quantity_received`; the reversal is in the ledger.
    """
    async with busy_as_conflict(db):
        document = await begin_locked(db, kind, document_id, stock=True, ledger=True)
        assert_facility(current, document.facility)
        if document.cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'{kind.label} is already cancelled',
            )

        if document.completed:
            entries = (
                (
                    await db.execute(
                        select(LotSerialTracking)
                        .where(
                            LotSerialTracking.source == int(kind.transaction_type),
                            LotSerialTracking.reference == document_id,
                        )
                        .order_by(LotSerialTracking.lot_serial_tracking_id)
                    )
                )
                .scalars()
                .all()
            )
            reversal = reversal_entries(entries)

            by_warehouse: dict[int, dict[int, Decimal]] = defaultdict(dict)
            for (warehouse, product), quantity in net_outbound(reversal).items():
                by_warehouse[warehouse][product] = quantity
            for warehouse in sorted(by_warehouse):
                await assert_available(db, by_warehouse[warehouse], warehouse)

            for movement in reversal:
                stock_ledger.post_movement(
                    db,
                    source=kind.transaction_type,
                    reference=document_id,
                    product=movement.product,
                    warehouse=movement.warehouse,
                    quantity=abs(movement.quantity),
                    outbound=movement.quantity < 0,
                )
            if kind.transit:
                document.in_transit = False

        document.cancelled = True
        _touch(document, current)
        await db.commit()
    await db.refresh(document)
    return document


# ── Responses ─────────────────────────────────────────────────────────────────


def attach_status(document: Any) -> Any:
    """The derived `status` of a receipt or an issue, on the object the response is built from."""
    document.__dict__['status'] = derive_status(
        completed=document.completed, cancelled=document.cancelled
    )
    return document


async def attach(db: AsyncSession, kind: Kind, document: Any) -> Any:
    """The document's `status` and its `lines`, oldest first, for the response."""
    attach_status(document)
    document.__dict__['lines'] = await _lines(db, kind, getattr(document, kind.id_attr))
    return document


async def attach_issue(db: AsyncSession, document: Any) -> Any:
    return await attach(db, ISSUE, document)


# ── Lists (R5) ────────────────────────────────────────────────────────────────


def simple_status_clause(kind: Kind, value: str) -> ColumnElement[bool]:
    """The `status` filter of receipts and issues; `paid` and anything else are refused."""
    header = kind.header
    if value == 'draft':
        return and_(header.completed.is_(False), header.cancelled.is_(False))
    if value == 'completed':
        return and_(header.completed.is_(True), header.cancelled.is_(False))
    if value == 'cancelled':
        return header.cancelled.is_(True)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, completed, cancelled',
    )


async def list_documents(
    db: AsyncSession,
    kind: Kind,
    *,
    current: CurrentUser,
    status: str | None = None,
    warehouse: int | None = None,
    skip: int = 0,
    limit: int = 20,
    status_clause: Callable[[Kind, str], ColumnElement[bool]] = simple_status_clause,
) -> tuple[Sequence[Any], int]:
    """Newest first. The default scope is the point of sale's warehouse or the caller's own
    documents; administrators and holders of the delete right see all (R5)."""
    header = kind.header
    clauses: list[ColumnElement[bool]] = []

    wider = current.administrator or await has_any_privilege(
        db, current.user_id, [(kind.system_object, AccessRight.DELETE)]
    )
    if not wider:
        own = header.creator == current.employee_id
        pos = await _pos_warehouse(db, current)
        if pos is None:
            clauses.append(own)
        else:
            clauses.append(or_(own, *(getattr(header, attr) == pos for attr in kind.warehouses)))
    if warehouse is not None:
        clauses.append(or_(*(getattr(header, attr) == warehouse for attr in kind.warehouses)))
    if status is not None:
        clauses.append(status_clause(kind, status))

    total: int = (
        await db.execute(select(func.count()).select_from(header).where(*clauses))
    ).scalar_one()
    items = (
        (
            await db.execute(
                select(header)
                .where(*clauses)
                .order_by(getattr(header, kind.id_attr).desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return items, total
