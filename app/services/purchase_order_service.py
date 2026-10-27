"""Purchase orders: drafts, lines from requests, confirm into receipts, PDF and email (spec 031,
US2, US3 and US5).

**States (research R1)**: draft, `completed` (the spec's "confirmed") and cancelled, the same values
receipts use.

**Locking (research R3, R4).** Every write commits, locks the PO row `FOR UPDATE` and then checks
it is a draft, so no edit lands after a concurrent confirm. A step that puts request lines on a PO
also locks those request-line rows, ascending, before deciding whether each is still pending; the
ids to lock are read before the commit, and nothing else is read between the commit and the last
lock, so the pending check sees every PO line another holder committed (FR-026).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.clock import local_now
from app.core.constants import COST_PRICE_LIST_ID
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, SourceType
from app.models.core import Employee, Warehouse
from app.models.inventory import InventoryReceipt, InventoryReceiptDetail
from app.models.product import Product, ProductPrice
from app.models.purchases import (
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseRequest,
    PurchaseRequestDetail,
)
from app.models.supplier import Supplier
from app.schemas.purchases import (
    OrderLineAdd,
    OrderLineUpdate,
    PurchaseOrderCreate,
    PurchaseOrderUpdate,
    RequestStatus,
)
from app.schemas.sales_order import DocumentStatus, derive_status
from app.services import incidences, mail, print_contexts, totals, warehouse_service
from app.services import inventory_documents as inv
from app.services import purchase_request_service as requests
from app.services.delivery_itinerary_service import _pos_warehouse
from app.services.sales_quote_service import _recipients_context

# ── States (R1) ───────────────────────────────────────────────────────────────


def order_status(completed: bool | None, cancelled: bool | None) -> DocumentStatus:
    return derive_status(completed=bool(completed), cancelled=bool(cancelled))


def order_status_clause(value: str) -> ColumnElement[bool]:
    header = PurchaseOrder
    if value == DocumentStatus.DRAFT:
        return and_(header.completed.is_(False), header.cancelled.is_(False))
    if value == DocumentStatus.COMPLETED:
        return and_(header.completed.is_(True), header.cancelled.is_(False))
    if value == DocumentStatus.CANCELLED:
        return header.cancelled.is_(True)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, completed, cancelled',
    )


# ── Pure rules: lines and requests (US2) ──────────────────────────────────────

TAX_PLACES = Decimal('0.0001')


def is_pending(request_state: RequestStatus, to_purchase: bool, live_po_line: int | None) -> bool:
    """A request line feeds POs when its request is approved, it is marked, and no live PO line
    holds it (FR-023, FR-024)."""
    return request_state is RequestStatus.APPROVED and bool(to_purchase) and live_po_line is None


def group_key(
    product_supplier: int | None, line_facility: int | None, header_facility: int
) -> tuple[int | None, int]:
    """`(supplier, facility)`; a legacy line without a warehouse goes by its header's (R8)."""
    return product_supplier, line_facility if line_facility is not None else header_facility


def group_lines(rows: Iterable[Any]) -> list[dict[str, Any]]:
    """Groups by `(supplier, facility)`, by facility then supplier name, no supplier last.

    Each row carries `supplier`, `supplier_name`, `facility`, `quantity` and `cost` (None when the
    product has none, counted as 0); the rows keep their order inside a group.
    """
    groups: dict[tuple[int | None, int], dict[str, Any]] = {}
    for row in rows:
        row.cost = row.cost if row.cost is not None else Decimal(0)
        key = (row.supplier, row.facility)
        group = groups.setdefault(
            key,
            {
                'supplier': row.supplier,
                'supplier_name': row.supplier_name,
                'facility': row.facility,
                'line_count': 0,
                'estimated_total': Decimal(0),
                'lines': [],
            },
        )
        group['line_count'] += 1
        group['estimated_total'] += row.quantity * row.cost
        group['lines'].append(row)
    return sorted(
        groups.values(),
        key=lambda g: (g['facility'], g['supplier'] is None, g['supplier_name'] or ''),
    )


def line_values(
    product: Any, cost: Decimal | None, quantity: Decimal, warehouse: int
) -> tuple[dict[str, Any], str | None]:
    """A PO line's columns, priced from cost, with the fixed defaults (FR-021, FR-025)."""
    warning = None if cost is not None else f'{product.code} has no cost; priced at 0'
    return {
        'product': product.product_id,
        'product_code': product.code,
        'product_name': product.name,
        'warehouse': warehouse,
        'quantity': quantity,
        'price': cost if cost is not None else Decimal(0),
        'tax_rate': Decimal(product.tax_rate).quantize(TAX_PLACES),
        'discount': Decimal(0),
        'currency': CurrencyCode.MXN,
        'exchange_rate': Decimal(1),
        'tax_included': False,
    }, warning


def supplier_mismatch_warning(
    code: str, product_supplier: int | None, order_supplier: int | None
) -> str | None:
    if product_supplier is not None and product_supplier != order_supplier:
        return f'{code} is supplied by another supplier'
    return None


def split_taken(requested: Sequence[int], still_pending: set[int]) -> tuple[list[int], list[int]]:
    """`(taken, skipped)`, each in the order requested (FR-026)."""
    taken = [line for line in requested if line in still_pending]
    skipped = [line for line in requested if line not in still_pending]
    return taken, skipped


# ── Pure rules: confirm and progress (US3) ────────────────────────────────────


def confirm_refusal(
    state: DocumentStatus, supplier: int | None, lines: Sequence[Any]
) -> str | None:
    if state is not DocumentStatus.DRAFT:
        return 'Only a draft purchase order can be confirmed'
    if supplier is None:
        return 'The purchase order has no supplier'
    if not lines:
        return 'The purchase order has no lines'
    for line in lines:
        if line.quantity <= 0:
            return f'Line {line.product_code} has no quantity'
    return None


def zero_price_warnings(lines: Iterable[Any]) -> list[str]:
    """FR-027, FR-035: confirmed, but its receipts will not change cost."""
    return [f'{line.product_code} is priced at 0' for line in lines if line.price == 0]


def receipts_to_generate(
    lines: Iterable[Any], *, stockable: set[int], held: dict[int, Decimal]
) -> dict[int, list[tuple[Any, Decimal]]]:
    """Per destination warehouse, ascending, `(line, pending)` for its stockable lines with
    something pending: ordered minus what live receipts hold (FR-027, R4)."""
    by_warehouse: dict[int, list[tuple[Any, Decimal]]] = defaultdict(list)
    for line in lines:
        if line.product not in stockable:
            continue
        pending = line.quantity - held.get(line.purchase_order_detail_id, Decimal(0))
        if pending > 0:
            by_warehouse[line.warehouse].append((line, pending))
    return {warehouse: by_warehouse[warehouse] for warehouse in sorted(by_warehouse)}


def progress(
    lines: Iterable[Any], received: dict[int, Decimal]
) -> dict[int, tuple[Decimal, Decimal]]:
    """Per line, `(received, pending)`, pending floored at 0 (FR-030)."""
    result = {}
    for line in lines:
        got = received.get(line.purchase_order_detail_id, Decimal(0))
        result[line.purchase_order_detail_id] = (got, max(line.quantity - got, Decimal(0)))
    return result


def _totals_line(line: Any) -> totals.Line:
    # Prices are before tax, whatever a legacy line's flag says (FR-034, clarification Q1).
    return totals.Line(
        quantity=line.quantity,
        price=line.price,
        discount_rate=line.discount,
        tax_rate=line.tax_rate,
        tax_included=False,
    )


def line_amount(line: Any) -> Decimal:
    subtotal, _ = totals.line_amounts(
        quantity=line.quantity,
        price=line.price,
        discount_rate=line.discount,
        tax_rate=line.tax_rate,
        tax_included=False,
    )
    return subtotal.quantize(totals.CENTS)


def order_totals(lines: Iterable[Any]) -> tuple[Decimal, Decimal, Decimal]:
    """`(subtotal, tax, total)`: tax added at each line's rate, rounded once (FR-040)."""
    computed = totals.document_totals([_totals_line(line) for line in lines])
    return computed.subtotal, computed.tax_total, computed.total


def _conflict(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


# ── Reads ─────────────────────────────────────────────────────────────────────


async def get_order(db: AsyncSession, order_id: int) -> PurchaseOrder | None:
    return await db.get(PurchaseOrder, order_id)


async def _lines(db: AsyncSession, order_id: int) -> list[PurchaseOrderDetail]:
    rows = await db.execute(
        select(PurchaseOrderDetail)
        .where(PurchaseOrderDetail.purchase_order == order_id)
        .order_by(PurchaseOrderDetail.purchase_order_detail_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def costs(db: AsyncSession, products: Iterable[int]) -> dict[int, Decimal]:
    """The cost list's price per product; a product without a row is absent."""
    ids = set(products)
    if not ids:
        return {}
    rows = await db.execute(
        select(ProductPrice.product, ProductPrice.price).where(
            ProductPrice.price_list == COST_PRICE_LIST_ID, ProductPrice.product.in_(ids)
        )
    )
    return dict(rows.all())


async def _supplier(db: AsyncSession, supplier_id: int) -> Supplier:
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    return supplier


async def _purchasable(db: AsyncSession, product_id: int) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')
    if not product.purchasable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Product {product.code} is not purchasable',
        )
    return product


# ── Locks (R3, R4) ────────────────────────────────────────────────────────────


async def lock_order(db: AsyncSession, order_id: int) -> PurchaseOrder:
    order = (
        await db.execute(
            select(PurchaseOrder)
            .where(PurchaseOrder.purchase_order_id == order_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Purchase order not found'
        )
    return order


async def _begin_draft(db: AsyncSession, order_id: int) -> PurchaseOrder:
    await db.commit()
    order = await lock_order(db, order_id)
    if order_status(order.completed, order.cancelled) is not DocumentStatus.DRAFT:
        raise _conflict('Only a draft purchase order can be changed')
    return order


def _touch(order: PurchaseOrder, current: CurrentUser) -> None:
    order.updater = current.employee_id
    order.modification_time = local_now()


def _midnight(value: date | None) -> datetime | None:
    return None if value is None else datetime.combine(value, time())


# ── Drafts (FR-020 to FR-022) ─────────────────────────────────────────────────


async def create(
    db: AsyncSession, data: PurchaseOrderCreate, *, current: CurrentUser
) -> PurchaseOrder:
    await _supplier(db, data.supplier)
    order = _new_order(data.supplier, current)
    order.estimated_receipt_date = _midnight(data.estimated_receipt_date)
    order.comment = data.comment
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return order


def _new_order(supplier: int, current: CurrentUser) -> PurchaseOrder:
    now = local_now()
    return PurchaseOrder(
        supplier=supplier,
        creator=current.employee_id,
        updater=current.employee_id,
        creation_time=now,
        modification_time=now,
        completed=False,
        cancelled=False,
        # No PO approval (FR-029); the invoice number is never used (FR-021).
        approved=False,
        approver=None,
        invoice_number=None,
    )


async def update(
    db: AsyncSession, order_id: int, data: PurchaseOrderUpdate, *, current: CurrentUser
) -> PurchaseOrder:
    changes = data.model_dump(exclude_unset=True)
    async with inv.busy_as_conflict(db):
        order = await _begin_draft(db, order_id)
        if changes.get('supplier') is not None:
            await _supplier(db, changes['supplier'])
            order.supplier = changes['supplier']
        if 'estimated_receipt_date' in changes:
            order.estimated_receipt_date = _midnight(changes['estimated_receipt_date'])
        if 'comment' in changes:
            order.comment = changes['comment']
        _touch(order, current)
        await db.commit()
    return order


async def add_line(
    db: AsyncSession, order_id: int, data: OrderLineAdd, *, current: CurrentUser
) -> PurchaseOrder:
    async with inv.busy_as_conflict(db):
        order = await _begin_draft(db, order_id)
        product = await _purchasable(db, data.product)
        warehouse = data.warehouse or await _pos_warehouse(db, current)
        if warehouse is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail='A warehouse is required',
            )
        await warehouse_service.assert_sellable(db, warehouse)
        cost = (await costs(db, [product.product_id])).get(product.product_id)
        values, warning = line_values(product, cost, data.quantity, warehouse)
        if data.price is not None:
            values['price'], warning = data.price, None
        if data.tax_rate is not None:
            values['tax_rate'] = data.tax_rate
        db.add(PurchaseOrderDetail(purchase_order=order_id, **values))
        _touch(order, current)
        await db.commit()
    order.__dict__['warnings'] = [warning] if warning else []
    return order


async def _locked_line(
    db: AsyncSession, order_id: int, line_id: int
) -> tuple[PurchaseOrder, PurchaseOrderDetail]:
    order = await _begin_draft(db, order_id)
    line = (
        await db.execute(
            select(PurchaseOrderDetail)
            .where(PurchaseOrderDetail.purchase_order_detail_id == line_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if line is None or line.purchase_order != order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    return order, line


async def update_line(
    db: AsyncSession, order_id: int, line_id: int, data: OrderLineUpdate, *, current: CurrentUser
) -> PurchaseOrder:
    changes = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
    async with inv.busy_as_conflict(db):
        order, line = await _locked_line(db, order_id, line_id)
        if 'warehouse' in changes:
            await warehouse_service.assert_sellable(db, changes['warehouse'])
        for field, value in changes.items():
            setattr(line, field, value)
        _touch(order, current)
        await db.commit()
    return order


async def remove_line(
    db: AsyncSession, order_id: int, line_id: int, *, current: CurrentUser
) -> PurchaseOrder:
    """Removing a line linked to a request line frees it; the delete is its own lock (R3)."""
    async with inv.busy_as_conflict(db):
        order, line = await _locked_line(db, order_id, line_id)
        await db.delete(line)
        _touch(order, current)
        await db.commit()
    return order


async def cancel(db: AsyncSession, order_id: int, *, current: CurrentUser) -> PurchaseOrder:
    """Drafts only. The lines stay as history; their request lines are pending again (FR-028)."""
    async with inv.busy_as_conflict(db):
        await db.commit()
        order = await lock_order(db, order_id)
        state = order_status(order.completed, order.cancelled)
        if state is DocumentStatus.CANCELLED:
            raise _conflict('The purchase order is already cancelled')
        if state is DocumentStatus.COMPLETED:
            raise _conflict('A confirmed purchase order cannot be cancelled')
        order.cancelled = True
        _touch(order, current)
        await db.commit()
    return order


# ── Lines from requests (FR-023 to FR-026, R3, R8) ────────────────────────────


def _pending_query(*clauses: ColumnElement[bool]) -> Any:
    """Pending request lines with what grouping and pricing need, oldest first (R8)."""
    line = PurchaseRequestDetail
    header = PurchaseRequest
    line_warehouse = Warehouse.__table__.alias('line_warehouse')
    header_warehouse = Warehouse.__table__.alias('header_warehouse')
    live = (
        select(PurchaseOrderDetail.purchase_order_detail_id)
        .join(PurchaseOrder, PurchaseOrder.purchase_order_id == PurchaseOrderDetail.purchase_order)
        .where(
            PurchaseOrderDetail.purchase_request_detail == line.purchase_request_detail_id,
            PurchaseOrder.cancelled.is_(False),
        )
    )
    facility = func.coalesce(line_warehouse.c.facility, header_warehouse.c.facility)
    return (
        select(
            line.purchase_request_detail_id,
            line.purchase_request,
            header.serial,
            line.product,
            Product.code.label('product_code'),
            line.product_name,
            line.quantity,
            func.coalesce(line.warehouse, header.warehouse).label('warehouse'),
            facility.label('facility'),
            Product.supplier,
            Supplier.name.label('supplier_name'),
            ProductPrice.price.label('cost'),
        )
        .join(header, header.purchase_request_id == line.purchase_request)
        .join(Product, Product.product_id == line.product)
        .join(header_warehouse, header_warehouse.c.warehouse_id == header.warehouse)
        .outerjoin(line_warehouse, line_warehouse.c.warehouse_id == line.warehouse)
        .outerjoin(Supplier, Supplier.supplier_id == Product.supplier)
        .outerjoin(
            ProductPrice,
            and_(
                ProductPrice.product == line.product,
                ProductPrice.price_list == COST_PRICE_LIST_ID,
            ),
        )
        .where(
            requests.request_status_clause(RequestStatus.APPROVED),
            line.to_purchase.is_(True),
            ~exists(live),
            *clauses,
        )
        .order_by(line.purchase_request_detail_id)
    ), facility


class _Row:
    """A pending line as `group_lines` and the response read it; `cost` is writable."""

    def __init__(self, mapping: Any) -> None:
        self.__dict__.update(dict(mapping))


async def to_purchase_groups(
    db: AsyncSession, *, facility: int | None = None
) -> list[dict[str, Any]]:
    query, facility_column = _pending_query()
    if facility is not None:
        query = query.where(facility_column == facility)
    return group_lines([_Row(row) for row in (await db.execute(query)).mappings().all()])


def _skip_reason(state: RequestStatus, live: Any) -> str:
    if live is not None:
        return f'Already on purchase order {live[1]}'
    if state is not RequestStatus.APPROVED:
        return 'The request is not approved'
    return 'Not marked to purchase'


async def _still_pending(
    db: AsyncSession,
    lines: Sequence[PurchaseRequestDetail],
    states: dict[int, RequestStatus],
) -> tuple[list[PurchaseRequestDetail], list[dict[str, Any]]]:
    """Re-check each locked request line (R3): `(pending, skipped)`."""
    live = await requests.live_order_lines(db, [line.purchase_request_detail_id for line in lines])
    pending: list[PurchaseRequestDetail] = []
    skipped: list[dict[str, Any]] = []
    for line in lines:
        state = states[line.purchase_request]
        found = live.get(line.purchase_request_detail_id)
        if is_pending(state, line.to_purchase, found and found[0]):
            pending.append(line)
        else:
            skipped.append(
                {
                    'purchase_request_detail': line.purchase_request_detail_id,
                    'reason': _skip_reason(state, found),
                }
            )
    return pending, skipped


async def _add_pending_lines(
    db: AsyncSession,
    order: PurchaseOrder,
    lines: Sequence[PurchaseRequestDetail],
    header_warehouses: dict[int, int],
) -> list[str]:
    """Put the re-checked pending request lines on `order`; returns the warnings."""
    products = {
        product.product_id: product
        for product in (
            await db.execute(
                select(Product).where(Product.product_id.in_({line.product for line in lines}))
            )
        )
        .scalars()
        .all()
    }
    cost_of = await costs(db, products)
    warnings: list[str] = []
    for line in lines:
        product = products[line.product]
        warehouse = line.warehouse or header_warehouses[line.purchase_request]
        values, warning = line_values(
            product, cost_of.get(product.product_id), line.quantity, warehouse
        )
        db.add(
            PurchaseOrderDetail(
                purchase_order=order.purchase_order_id,
                purchase_request_detail=line.purchase_request_detail_id,
                **values,
            )
        )
        for message in (
            warning,
            supplier_mismatch_warning(product.code, product.supplier, order.supplier),
        ):
            if message is not None:
                warnings.append(message)
    return warnings


async def _locked_request_lines(
    db: AsyncSession, ids: Sequence[int]
) -> tuple[list[PurchaseRequestDetail], dict[int, int], dict[int, RequestStatus]]:
    """The request lines, re-read after their rows are locked, with their headers' warehouse and
    state (ordinary reads, so they see what earlier lock holders committed)."""
    lines = list(
        (
            await db.execute(
                select(PurchaseRequestDetail)
                .where(PurchaseRequestDetail.purchase_request_detail_id.in_(ids))
                .order_by(PurchaseRequestDetail.purchase_request_detail_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    headers = (
        (
            await db.execute(
                select(PurchaseRequest)
                .where(
                    PurchaseRequest.purchase_request_id.in_(
                        {line.purchase_request for line in lines}
                    )
                )
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    return (
        lines,
        {header.purchase_request_id: header.warehouse for header in headers},
        {
            header.purchase_request_id: requests.request_status(
                header.completed, header.approved, header.cancelled
            )
            for header in headers
        },
    )


async def add_request_lines(
    db: AsyncSession, order_id: int, request_id: int, *, current: CurrentUser
) -> PurchaseOrder:
    """A request's pending lines onto a draft PO: PO row, then its line rows (FR-023, R3)."""
    async with inv.busy_as_conflict(db):
        if await db.get(PurchaseRequest, request_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail='Purchase request not found'
            )
        ids = (
            (
                await db.execute(
                    select(PurchaseRequestDetail.purchase_request_detail_id).where(
                        PurchaseRequestDetail.purchase_request == request_id
                    )
                )
            )
            .scalars()
            .all()
        )
        order = await _begin_draft(db, order_id)
        await requests.lock_request_lines(db, ids)

        lines, header_warehouses, states = await _locked_request_lines(db, ids)
        if states.get(request_id, RequestStatus.APPROVED) is not RequestStatus.APPROVED:
            raise _conflict("Only an approved request's lines can be ordered")
        pending, skipped = await _still_pending(db, lines, states)
        if not pending:
            raise _conflict('Nothing left to add from this request')
        warnings = await _add_pending_lines(db, order, pending, header_warehouses)
        _touch(order, current)
        await db.commit()
    order.__dict__['warnings'] = warnings
    order.__dict__['skipped'] = skipped
    return order


async def create_from_group(
    db: AsyncSession, supplier: int, facility: int, *, current: CurrentUser
) -> PurchaseOrder:
    """One draft PO for a supplier and facility from all of that group's pending lines (FR-025).

    The group's line ids are read before the commit; their rows are then locked ascending, and
    re-checked. The new PO does not exist yet, so the line rows are the only locks (R3, R8).
    """
    async with inv.busy_as_conflict(db):
        await _supplier(db, supplier)
        query, facility_column = _pending_query(Product.supplier == supplier)
        ids = [
            row.purchase_request_detail_id
            for row in (await db.execute(query.where(facility_column == facility))).all()
        ]
        if not ids:
            raise _conflict('Nothing is pending for this supplier and facility')
        await db.commit()
        await requests.lock_request_lines(db, ids)

        lines, header_warehouses, states = await _locked_request_lines(db, ids)
        # Re-checked before the PO exists, so a call that lost the race uses up no PO number.
        pending, skipped = await _still_pending(db, lines, states)
        if not pending:
            raise _conflict('Nothing is pending for this supplier and facility')
        order = _new_order(supplier, current)
        db.add(order)
        await db.flush()
        warnings = await _add_pending_lines(db, order, pending, header_warehouses)
        await db.commit()
    order.__dict__['warnings'] = warnings
    order.__dict__['skipped'] = skipped
    return order


# ── Confirm (FR-027, R4) ──────────────────────────────────────────────────────


async def held_by_live_receipts(db: AsyncSession, order_lines: Iterable[int]) -> dict[int, Decimal]:
    """Per PO line, what non-cancelled receipts, draft or confirmed, hold of it."""
    ids = set(order_lines)
    if not ids:
        return {}
    detail = InventoryReceiptDetail
    rows = await db.execute(
        select(detail.purchase_order_detail, func.sum(detail.quantity))
        .join(InventoryReceipt, InventoryReceipt.inventory_receipt_id == detail.receipt)
        .where(detail.purchase_order_detail.in_(ids), InventoryReceipt.cancelled.is_(False))
        .group_by(detail.purchase_order_detail)
    )
    return dict(rows.all())


async def confirm(db: AsyncSession, order_id: int, *, current: CurrentUser) -> PurchaseOrder:
    """Confirm, creating one draft receipt per destination warehouse, all or nothing (R4).

    The PO row lock with the state check is what keeps the receipts from being created twice: a
    second or concurrent confirm finds the PO completed. Drafts take no serial and post no stock,
    so no warehouse is locked.
    """
    async with inv.busy_as_conflict(db):
        await db.commit()
        order = await lock_order(db, order_id)
        lines = await _lines(db, order_id)
        reason = confirm_refusal(
            order_status(order.completed, order.cancelled), order.supplier, lines
        )
        if reason is not None:
            raise _conflict(reason)

        products = {
            product.product_id: product
            for product in (
                await db.execute(
                    select(Product).where(Product.product_id.in_({line.product for line in lines}))
                )
            )
            .scalars()
            .all()
        }
        stockable = {product_id for product_id, row in products.items() if row.stockable}
        held = await held_by_live_receipts(db, [line.purchase_order_detail_id for line in lines])
        plan = receipts_to_generate(lines, stockable=stockable, held=held)
        facilities = dict(
            (
                await db.execute(
                    select(Warehouse.warehouse_id, Warehouse.facility).where(
                        Warehouse.warehouse_id.in_(plan)
                    )
                )
            ).all()
        )

        now = local_now()
        generated = []
        for warehouse, pending_lines in plan.items():
            receipt = InventoryReceipt(
                facility=facilities[warehouse],
                warehouse=warehouse,
                purchase_order=order_id,
                creator=current.employee_id,
                updater=current.employee_id,
                creation_time=now,
                modification_time=now,
                completed=False,
                cancelled=False,
            )
            db.add(receipt)
            await db.flush()
            for line, pending in pending_lines:
                product = products[line.product]
                db.add(
                    InventoryReceiptDetail(
                        receipt=receipt.inventory_receipt_id,
                        product=line.product,
                        purchase_order_detail=line.purchase_order_detail_id,
                        quantity=pending,
                        quantity_ordered=line.quantity,
                        product_code=product.code,
                        product_name=product.name,
                    )
                )
            generated.append(
                {
                    'inventory_receipt_id': receipt.inventory_receipt_id,
                    'warehouse': warehouse,
                    'facility': facilities[warehouse],
                    'line_count': len(pending_lines),
                }
            )

        warnings = zero_price_warnings(lines)
        order.completed = True
        order.updater = current.employee_id
        order.modification_time = now
        await db.commit()
    order.__dict__['warnings'] = warnings
    order.__dict__['receipts'] = generated
    return order


# ── Responses ─────────────────────────────────────────────────────────────────


async def received_on_confirmed(db: AsyncSession, order_lines: Iterable[int]) -> dict[int, Decimal]:
    """Per PO line, what confirmed, non-cancelled receipts hold (FR-030)."""
    ids = set(order_lines)
    if not ids:
        return {}
    detail = InventoryReceiptDetail
    rows = await db.execute(
        select(detail.purchase_order_detail, func.sum(detail.quantity))
        .join(InventoryReceipt, InventoryReceipt.inventory_receipt_id == detail.receipt)
        .where(
            detail.purchase_order_detail.in_(ids),
            InventoryReceipt.completed.is_(True),
            InventoryReceipt.cancelled.is_(False),
        )
        .group_by(detail.purchase_order_detail)
    )
    return dict(rows.all())


async def _summaries(db: AsyncSession, orders: Sequence[PurchaseOrder]) -> None:
    """`status`, `supplier_name`, `line_count` and the totals, with two grouped reads."""
    if not orders:
        return
    names = dict(
        (
            await db.execute(
                select(Supplier.supplier_id, Supplier.name).where(
                    Supplier.supplier_id.in_({order.supplier for order in orders} - {None})
                )
            )
        ).all()
    )
    grouped: dict[int, list[PurchaseOrderDetail]] = defaultdict(list)
    for line in (
        await db.execute(
            select(PurchaseOrderDetail).where(
                PurchaseOrderDetail.purchase_order.in_([o.purchase_order_id for o in orders])
            )
        )
    ).scalars():
        grouped[line.purchase_order].append(line)
    for order in orders:
        lines = grouped.get(order.purchase_order_id, [])
        subtotal, tax, total = order_totals(lines)
        order.__dict__.update(
            status=order_status(order.completed, order.cancelled),
            supplier_name=names.get(order.supplier),
            line_count=len(lines),
            subtotal=subtotal,
            tax=tax,
            total=total,
        )


async def attach(db: AsyncSession, order: PurchaseOrder) -> PurchaseOrder:
    """Summary fields, lines with amounts, request links and receiving progress, and the
    step's `warnings`, `skipped` and `receipts` (empty unless the step set them)."""
    await _summaries(db, [order])
    for key in ('warnings', 'skipped', 'receipts'):
        order.__dict__.setdefault(key, [])
    lines = await _lines(db, order.purchase_order_id)
    request_of = dict(
        (
            await db.execute(
                select(
                    PurchaseRequestDetail.purchase_request_detail_id,
                    PurchaseRequestDetail.purchase_request,
                ).where(
                    PurchaseRequestDetail.purchase_request_detail_id.in_(
                        {line.purchase_request_detail for line in lines} - {None}
                    )
                )
            )
        ).all()
    )
    stockable = set(
        (
            await db.execute(
                select(Product.product_id).where(
                    Product.product_id.in_({line.product for line in lines}),
                    Product.stockable.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    figures = progress(
        lines, await received_on_confirmed(db, [ln.purchase_order_detail_id for ln in lines])
    )
    for line in lines:
        received, pending = figures[line.purchase_order_detail_id]
        if line.product not in stockable:
            received = pending = None
        line.__dict__.update(
            amount=line_amount(line),
            purchase_request=request_of.get(line.purchase_request_detail),
            quantity_received=received,
            quantity_pending=pending,
        )
    order.__dict__['lines'] = lines
    return order


# ── PDF and email (FR-040 to FR-043, R9) ──────────────────────────────────────


def assert_printable(order: PurchaseOrder) -> None:
    """FR-043: only a confirmed purchase order is printed or emailed."""
    if order_status(order.completed, order.cancelled) is not DocumentStatus.COMPLETED:
        raise _conflict('Only a confirmed purchase order can be printed or emailed')


async def document(db: AsyncSession, order: PurchaseOrder) -> tuple[bytes, dict]:
    """The PO's PDF, and the print context it was rendered from."""
    assert_printable(order)
    await attach(db, order)
    template, context = await print_contexts.purchase_order_context(db, order)
    return await rendering.render_pdf(template, context), context


def order_email_message(
    *, order_id: int, company: str, sender: str, to: Sequence[str], note: str | None, pdf: bytes
) -> mail.Message:
    """The quote email's shape (spec 024), with the user's message above the signature."""
    body = f'Le enviamos la orden de compra {order_id}.\n'
    if note:
        body += f'{note}\n'
    body += f'Atte: {sender}.'
    return mail.Message(
        to=list(to),
        subject=f'{company} Orden de compra {order_id}',
        body=body,
        attachments=[mail.Attachment(f'orden-compra-{order_id:08d}.pdf', 'application/pdf', pdf)],
    )


async def email_order(
    db: AsyncSession,
    order: PurchaseOrder,
    to: Sequence[str],
    note: str | None,
    *,
    mailer: mail.Mailer,
    current: CurrentUser,
) -> datetime:
    """Send a confirmed PO's PDF and record the send; returns when it was sent (FR-041, FR-042).

    As for quotes, the incidence is written only after the server accepted the message, so every
    recorded send happened.
    """
    pdf, context = await document(db, order)
    sender = await db.get(Employee, current.employee_id)
    message = order_email_message(
        order_id=order.purchase_order_id,
        company=context['header']['name'],
        sender=f'{sender.first_name} {sender.last_name}'.strip(),
        to=to,
        note=note,
        pdf=pdf,
    )
    try:
        await mailer.send(message)
    except mail.MailError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'The email could not be sent: {exc}',
        ) from exc

    sent_at = local_now()
    incidences.record(
        db,
        source=SourceType.PURCHASE_ORDER,
        instance_id=order.purchase_order_id,
        updater=current.employee_id,
        reason='Purchase order emailed',
        context=_recipients_context(to),
    )
    await db.commit()
    return sent_at


# ── Lists (FR-030, R11) ───────────────────────────────────────────────────────


async def list_orders(
    db: AsyncSession,
    *,
    current: CurrentUser,
    everyone: bool = False,
    state: str | None = None,
    supplier: int | None = None,
    creator: int | None = None,
    order_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[PurchaseOrder], int]:
    """Newest first. By default the caller's own (created or updated), without cancelled."""
    header = PurchaseOrder
    clauses: list[ColumnElement[bool]] = []
    if not everyone:
        clauses.append(
            or_(header.creator == current.employee_id, header.updater == current.employee_id)
        )
    clauses.append(header.cancelled.is_(False) if state is None else order_status_clause(state))
    if supplier is not None:
        clauses.append(header.supplier == supplier)
    if creator is not None:
        clauses.append(header.creator == creator)
    if order_id is not None:
        clauses.append(header.purchase_order_id == order_id)
    if date_from is not None:
        clauses.append(header.creation_time >= date_from)
    if date_to is not None:
        clauses.append(header.creation_time < date_to + timedelta(days=1))
    total: int = (
        await db.execute(select(func.count()).select_from(header).where(*clauses))
    ).scalar_one()
    items = list(
        (
            await db.execute(
                select(header)
                .where(*clauses)
                .order_by(header.purchase_order_id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    await _summaries(db, items)
    return items, total
