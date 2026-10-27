"""Purchase requests and their approval (spec 031, US1).

**States (research R1)** come from the legacy flags, NULL counting as false: draft, submitted
(`completed`), approved (`completed` and `approved`), cancelled.

**Locking (research R2, R3)** follows spec 029's pattern: plain pre-reads, a commit, the locking
reads, then ordinary reads, whose snapshot opens after the last lock. `purchase_request` has no
facility column, so steps that take a lock after the request row (submit, cancel) look the
facility up in a warehouse → facility map read before the commit, and re-check it once every lock
is held. Steps whose only lock is the request row read it after that lock.
"""

from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import SourceType
from app.models.core import Warehouse
from app.models.product import Product
from app.models.purchases import (
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseRequest,
    PurchaseRequestDetail,
)
from app.schemas.purchases import (
    PurchaseRequestCreate,
    PurchaseRequestUpdate,
    RejectRequest,
    RequestLineAdd,
    RequestLineUpdate,
    RequestStatus,
)
from app.schemas.sales_order import derive_status
from app.services import incidences, warehouse_service
from app.services import inventory_documents as inv
from app.services.delivery_itinerary_service import _pos_warehouse

# ── States (R1) ───────────────────────────────────────────────────────────────


def request_status(
    completed: bool | None, approved: bool | None, cancelled: bool | None
) -> RequestStatus:
    if cancelled:
        return RequestStatus.CANCELLED
    if not completed:
        return RequestStatus.DRAFT
    if approved:
        return RequestStatus.APPROVED
    return RequestStatus.SUBMITTED


def _status_of(request: PurchaseRequest) -> RequestStatus:
    return request_status(request.completed, request.approved, request.cancelled)


def _flag(column) -> ColumnElement[bool]:  # noqa: ANN001
    return func.coalesce(column, false())


def request_status_clause(value: str) -> ColumnElement[bool]:
    header = PurchaseRequest
    live = _flag(header.cancelled) == false()
    if value == RequestStatus.DRAFT:
        return and_(live, _flag(header.completed) == false())
    if value == RequestStatus.SUBMITTED:
        return and_(live, _flag(header.completed) == true(), _flag(header.approved) == false())
    if value == RequestStatus.APPROVED:
        return and_(live, _flag(header.completed) == true(), _flag(header.approved) == true())
    if value == RequestStatus.CANCELLED:
        return _flag(header.cancelled) == true()
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, submitted, approved, cancelled',
    )


# ── Pure rules ────────────────────────────────────────────────────────────────


def next_request_serial(highest: int | None) -> int:
    return (highest or 0) + 1


def _plain(quantity: Decimal) -> str:
    return f'{quantity.normalize():f}'


def submit_refusal(state: RequestStatus, lines: Sequence[Any]) -> tuple[int, str] | None:
    """`(status code, reason)` when a request cannot be submitted (FR-004)."""
    if state is not RequestStatus.DRAFT:
        return status.HTTP_409_CONFLICT, 'Only a draft request can be submitted'
    if not lines:
        return status.HTTP_409_CONFLICT, 'The request has no lines'
    for line in lines:
        if line.quantity <= 0:
            return (
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f'Line {line.product_name} has no quantity',
            )
    return None


def below_minimum_warning(code: str, quantity: Decimal, min_order_qty: int | None) -> str | None:
    """FR-003: a quantity under the product's minimum order quantity is saved, with a warning."""
    if not min_order_qty or min_order_qty <= 1 or quantity >= min_order_qty:
        return None
    return (
        f'{code}: quantity {_plain(quantity)} is below the minimum order quantity of '
        f'{min_order_qty}'
    )


def default_quantity(min_order_qty: int | None) -> Decimal:
    """FR-002: the minimum order quantity, or 1 when it is 0 (259 purchasable products)."""
    return Decimal(min_order_qty) if min_order_qty and min_order_qty >= 1 else Decimal(1)


def facility_move_refusal(serial: int | None, old_facility: int, new_facility: int) -> str | None:
    """FR-002: once numbered, a request's serial belongs to its facility."""
    if serial is not None and old_facility != new_facility:
        return 'A numbered request cannot move to another facility'
    return None


def _decision_refusal(state: RequestStatus) -> str | None:
    if state is not RequestStatus.SUBMITTED:
        return 'Only a submitted request can be approved or rejected'
    return None


def approve_refusal(state: RequestStatus) -> str | None:
    return _decision_refusal(state)


def reject_refusal(state: RequestStatus) -> str | None:
    return _decision_refusal(state)


def cancel_refusal(state: RequestStatus, lines_on_live_po: int) -> str | None:
    """FR-009: from any live state, unless a line is on a live purchase order."""
    if state is RequestStatus.CANCELLED:
        return 'The request is already cancelled'
    if lines_on_live_po:
        noun = 'line' if lines_on_live_po == 1 else 'lines'
        verb = 'is' if lines_on_live_po == 1 else 'are'
        return f'{lines_on_live_po} {noun} of the request {verb} on a live purchase order'
    return None


def mark_refusal(state: RequestStatus, to_purchase: bool, *, on_live_po: bool) -> str | None:
    """FR-008: only submitted and approved requests, and never unmark a line on a live PO."""
    if state not in (RequestStatus.SUBMITTED, RequestStatus.APPROVED):
        return 'Lines can be marked only on a submitted or approved request'
    if not to_purchase and on_live_po:
        return 'The line is on a live purchase order'
    return None


def _conflict(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


# ── Reads ─────────────────────────────────────────────────────────────────────


async def get_request(db: AsyncSession, request_id: int) -> PurchaseRequest | None:
    return await db.get(PurchaseRequest, request_id)


async def _facility_map(db: AsyncSession) -> dict[int, int]:
    """Warehouse → facility, every warehouse (a few dozen rows), for steps that lock later."""
    rows = await db.execute(select(Warehouse.warehouse_id, Warehouse.facility))
    return dict(rows.all())


async def _facility_of(db: AsyncSession, warehouse_id: int) -> int:
    return (
        await db.execute(select(Warehouse.facility).where(Warehouse.warehouse_id == warehouse_id))
    ).scalar_one()


async def _lines(db: AsyncSession, request_id: int) -> list[PurchaseRequestDetail]:
    rows = await db.execute(
        select(PurchaseRequestDetail)
        .where(PurchaseRequestDetail.purchase_request == request_id)
        .order_by(PurchaseRequestDetail.purchase_request_detail_id)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars().all())


async def live_order_lines(
    db: AsyncSession, request_lines: Iterable[int]
) -> dict[int, tuple[int, int, bool, bool]]:
    """Per request line, its live PO line: `(PO line, PO, completed, cancelled)` (FR-010).

    Legacy data has 109 request lines on two live PO lines; the lowest id is shown.
    """
    ids = set(request_lines)
    if not ids:
        return {}
    rows = await db.execute(
        select(
            PurchaseOrderDetail.purchase_request_detail,
            PurchaseOrderDetail.purchase_order_detail_id,
            PurchaseOrder.purchase_order_id,
            PurchaseOrder.completed,
            PurchaseOrder.cancelled,
        )
        .join(PurchaseOrder, PurchaseOrder.purchase_order_id == PurchaseOrderDetail.purchase_order)
        .where(
            PurchaseOrderDetail.purchase_request_detail.in_(ids),
            PurchaseOrder.cancelled.is_(False),
        )
        .order_by(PurchaseOrderDetail.purchase_order_detail_id)
    )
    found: dict[int, tuple[int, int, bool, bool]] = {}
    for request_line, order_line, order, completed, cancelled in rows.all():
        found.setdefault(request_line, (order_line, order, completed, cancelled))
    return found


# ── Locks (R2, R3) ────────────────────────────────────────────────────────────


async def lock_request(db: AsyncSession, request_id: int) -> PurchaseRequest:
    """The request row, locked, with every attribute re-read from it."""
    request = (
        await db.execute(
            select(PurchaseRequest)
            .where(PurchaseRequest.purchase_request_id == request_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Purchase request not found'
        )
    return request


async def lock_request_lines(db: AsyncSession, ids: Iterable[int]) -> None:
    """Request-line rows `FOR UPDATE`, ascending, so two steps never take them crosswise (R3)."""
    ordered = sorted(set(ids))
    if not ordered:
        return
    await db.execute(
        select(PurchaseRequestDetail.purchase_request_detail_id)
        .where(PurchaseRequestDetail.purchase_request_detail_id.in_(ordered))
        .order_by(PurchaseRequestDetail.purchase_request_detail_id)
        .with_for_update()
    )


async def _begin(db: AsyncSession, request_id: int) -> PurchaseRequest:
    await db.commit()
    return await lock_request(db, request_id)


def _assert_mapped_facility(mapped: int | None, current_facility: int) -> None:
    """R2 step 4: the warehouse's facility read after the locks must be the pre-read one."""
    if mapped != current_facility:
        raise _conflict('The warehouse changed facility, retry')


def _touch(request: PurchaseRequest, current: CurrentUser) -> None:
    request.updater = current.employee_id
    request.modification_time = local_now()


def _assert_draft(request: PurchaseRequest) -> None:
    if _status_of(request) is not RequestStatus.DRAFT:
        raise _conflict('Only a draft request can be changed')


async def _product(db: AsyncSession, product_id: int) -> Product:
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')
    if not product.purchasable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Product {product.code} is not purchasable',
        )
    return product


# ── Drafts (FR-001, FR-002) ───────────────────────────────────────────────────


async def create(
    db: AsyncSession, data: PurchaseRequestCreate, *, current: CurrentUser
) -> PurchaseRequest:
    warehouse = data.warehouse or await _pos_warehouse(db, current)
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail='A warehouse is required'
        )
    await warehouse_service.assert_sellable(db, warehouse)
    now = local_now()
    request = PurchaseRequest(
        creator=current.employee_id,
        updater=current.employee_id,
        warehouse=warehouse,
        comment=data.comment,
        serial=None,
        date=now,
        creation_time=now,
        modification_time=now,
        completed=False,
        approved=False,
        cancelled=False,
    )
    db.add(request)
    await db.commit()
    await db.refresh(request)
    return request


async def _begin_edit(db: AsyncSession, request_id: int, current: CurrentUser) -> PurchaseRequest:
    """Lock, then the facility rule and the draft check. The request row is the only lock, so
    the facility is read after it (R3)."""
    request = await _begin(db, request_id)
    inv.assert_facility(current, await _facility_of(db, request.warehouse))
    _assert_draft(request)
    return request


async def update(
    db: AsyncSession, request_id: int, data: PurchaseRequestUpdate, *, current: CurrentUser
) -> PurchaseRequest:
    changes = data.model_dump(exclude_unset=True)
    async with inv.busy_as_conflict(db):
        request = await _begin_edit(db, request_id, current)
        target = changes.get('warehouse')
        if target is not None and target != request.warehouse:
            await warehouse_service.assert_sellable(db, target)
            reason = facility_move_refusal(
                request.serial,
                await _facility_of(db, request.warehouse),
                await _facility_of(db, target),
            )
            if reason is not None:
                raise _conflict(reason)
            request.warehouse = target
        if 'comment' in changes:
            request.comment = changes['comment']
        _touch(request, current)
        await db.commit()
    return request


def _warn(request: PurchaseRequest, product: Product, quantity: Decimal) -> None:
    warning = below_minimum_warning(product.code, quantity, product.min_order_qty)
    request.__dict__['warnings'] = [warning] if warning else []


async def add_line(
    db: AsyncSession, request_id: int, data: RequestLineAdd, *, current: CurrentUser
) -> PurchaseRequest:
    async with inv.busy_as_conflict(db):
        request = await _begin_edit(db, request_id, current)
        product = await _product(db, data.product)
        warehouse = data.warehouse or request.warehouse
        await warehouse_service.assert_sellable(db, warehouse)
        quantity = data.quantity or default_quantity(product.min_order_qty)
        db.add(
            PurchaseRequestDetail(
                purchase_request=request_id,
                product=product.product_id,
                product_name=data.product_name or product.name,
                quantity=quantity,
                warehouse=warehouse,
                # Written explicitly: the columns' defaults are 0 and NULL (FR-008, FR-013).
                to_purchase=True,
                customer=settings.default_customer_id,
                accepted=False,
            )
        )
        _touch(request, current)
        await db.commit()
    _warn(request, product, quantity)
    return request


async def _locked_line(
    db: AsyncSession, request_id: int, line_id: int, current: CurrentUser
) -> tuple[PurchaseRequest, PurchaseRequestDetail]:
    request = await _begin_edit(db, request_id, current)
    line = (
        await db.execute(
            select(PurchaseRequestDetail)
            .where(PurchaseRequestDetail.purchase_request_detail_id == line_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if line is None or line.purchase_request != request_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    return request, line


async def update_line(
    db: AsyncSession,
    request_id: int,
    line_id: int,
    data: RequestLineUpdate,
    *,
    current: CurrentUser,
) -> PurchaseRequest:
    changes = data.model_dump(exclude_unset=True)
    async with inv.busy_as_conflict(db):
        request, line = await _locked_line(db, request_id, line_id, current)
        if changes.get('warehouse') is not None:
            await warehouse_service.assert_sellable(db, changes['warehouse'])
            line.warehouse = changes['warehouse']
        if changes.get('quantity') is not None:
            line.quantity = changes['quantity']
        if changes.get('product_name') is not None:
            line.product_name = changes['product_name']
        product = await db.get(Product, line.product)
        quantity = line.quantity
        _touch(request, current)
        await db.commit()
    _warn(request, product, quantity)
    return request


async def remove_line(
    db: AsyncSession, request_id: int, line_id: int, *, current: CurrentUser
) -> PurchaseRequest:
    async with inv.busy_as_conflict(db):
        request, line = await _locked_line(db, request_id, line_id, current)
        await db.delete(line)
        _touch(request, current)
        await db.commit()
    return request


# ── Submit (FR-004, R2) ───────────────────────────────────────────────────────


async def submit(db: AsyncSession, request_id: int, *, current: CurrentUser) -> PurchaseRequest:
    """Number the request on its first submit, under its facility's transit-warehouse lock.

    That is the lock every numbering step of the facility queues on (spec 029, R1), so the
    `MAX(serial)` read after it sees the serial the previous holder committed. Between the commit
    and that lock nothing is read except under a lock: the facility comes from the pre-read map,
    and is re-read once the lock is held. A refusal rolls back, so no number is used.
    """
    async with inv.busy_as_conflict(db):
        transit_map = await inv._transit_map(db)
        facility_map = await _facility_map(db)
        request = await _begin(db, request_id)
        facility = facility_map.get(request.warehouse)
        if facility is None:
            raise _conflict('The warehouse changed facility, retry')
        inv.assert_facility(current, facility)
        transit = transit_map.get(facility)
        if transit is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Facility {facility} has no in-transit location',
            )
        await inv.lock_warehouses(db, [transit])

        _assert_mapped_facility(facility, await _facility_of(db, request.warehouse))
        refusal = submit_refusal(_status_of(request), await _lines(db, request_id))
        if refusal is not None:
            code, reason = refusal
            raise HTTPException(status_code=code, detail=reason)

        serial = request.serial
        if serial is None:
            highest = (
                await db.execute(
                    select(func.max(PurchaseRequest.serial))
                    .join(Warehouse, Warehouse.warehouse_id == PurchaseRequest.warehouse)
                    .where(Warehouse.facility == facility)
                )
            ).scalar_one_or_none()
            serial = next_request_serial(highest)

        # Last, after every read, so autoflush cannot write it before the checks pass.
        request.serial = serial
        request.completed = True
        request.approved = False
        _touch(request, current)
        await db.commit()
    return request


# ── Approve and reject (FR-005, FR-006) ───────────────────────────────────────


async def approve(db: AsyncSession, request_id: int, *, current: CurrentUser) -> PurchaseRequest:
    async with inv.busy_as_conflict(db):
        request = await _begin(db, request_id)
        reason = approve_refusal(_status_of(request))
        if reason is not None:
            raise _conflict(reason)
        now = local_now()
        request.approved = True
        request.approver = current.employee_id
        request.approval_time = now
        request.updater = current.employee_id
        request.modification_time = now
        await db.commit()
    return request


async def approve_many(
    db: AsyncSession, ids: Sequence[int], *, current: CurrentUser
) -> list[dict[str, Any]]:
    """Each id in its own locked transaction, in the order sent; a refusal stops none of them."""
    results: list[dict[str, Any]] = []
    for request_id in dict.fromkeys(ids):
        try:
            await approve(db, request_id, current=current)
        except HTTPException as refused:
            results.append(
                {'purchase_request_id': request_id, 'approved': False, 'reason': refused.detail}
            )
        else:
            results.append({'purchase_request_id': request_id, 'approved': True, 'reason': None})
    return results


async def reject(
    db: AsyncSession, request_id: int, data: RejectRequest, *, current: CurrentUser
) -> PurchaseRequest:
    """Back to draft with its serial; the reason goes to the incidence log (FR-006)."""
    async with inv.busy_as_conflict(db):
        request = await _begin(db, request_id)
        reason = reject_refusal(_status_of(request))
        if reason is not None:
            raise _conflict(reason)
        incidences.record(
            db,
            source=SourceType.PURCHASE_REQUEST,
            instance_id=request_id,
            updater=current.employee_id,
            reason=data.reason,
            context='Purchase request rejected',
        )
        request.completed = False
        request.approved = False
        request.approver = None
        request.approval_time = None
        _touch(request, current)
        await db.commit()
    return request


# ── Marking (FR-008, R3) ──────────────────────────────────────────────────────


async def mark(
    db: AsyncSession, request_id: int, line_id: int, *, to_purchase: bool, current: CurrentUser
) -> PurchaseRequest:
    """Request row, then the line row, the same order as cancel (R3). The request header is not
    touched: marking is the purchasing agent's, not an edit of the request."""
    async with inv.busy_as_conflict(db):
        request = await _begin(db, request_id)
        line = (
            await db.execute(
                select(PurchaseRequestDetail)
                .where(PurchaseRequestDetail.purchase_request_detail_id == line_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if line is None or line.purchase_request != request_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
        on_live_po = line_id in await live_order_lines(db, [line_id])
        reason = mark_refusal(_status_of(request), to_purchase, on_live_po=on_live_po)
        if reason is not None:
            raise _conflict(reason)
        line.to_purchase = to_purchase
        await db.commit()
    return request


# ── Cancel (FR-009, R3) ───────────────────────────────────────────────────────


async def cancel(db: AsyncSession, request_id: int, *, current: CurrentUser) -> PurchaseRequest:
    """Request row, then its line rows ascending, so an adder holding them finishes first and the
    live-PO check below sees its lines (R3). No ordinary read runs before the line locks."""
    async with inv.busy_as_conflict(db):
        facility_map = await _facility_map(db)
        line_ids = (
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
        request = await _begin(db, request_id)
        facility = facility_map.get(request.warehouse)
        if facility is None:
            raise _conflict('The warehouse changed facility, retry')
        inv.assert_facility(current, facility)
        await lock_request_lines(db, line_ids)

        _assert_mapped_facility(facility, await _facility_of(db, request.warehouse))
        lines = await _lines(db, request_id)
        live = await live_order_lines(db, [line.purchase_request_detail_id for line in lines])
        reason = cancel_refusal(_status_of(request), len(live))
        if reason is not None:
            raise _conflict(reason)
        request.cancelled = True
        _touch(request, current)
        await db.commit()
    return request


# ── Responses ─────────────────────────────────────────────────────────────────


async def _summaries(db: AsyncSession, requests: Sequence[PurchaseRequest]) -> None:
    """`status`, `facility` and `line_count` on each request, with two grouped reads."""
    if not requests:
        return
    warehouses = {request.warehouse for request in requests}
    facilities = dict(
        (
            await db.execute(
                select(Warehouse.warehouse_id, Warehouse.facility).where(
                    Warehouse.warehouse_id.in_(warehouses)
                )
            )
        ).all()
    )
    counts = dict(
        (
            await db.execute(
                select(PurchaseRequestDetail.purchase_request, func.count())
                .where(
                    PurchaseRequestDetail.purchase_request.in_(
                        [request.purchase_request_id for request in requests]
                    )
                )
                .group_by(PurchaseRequestDetail.purchase_request)
            )
        ).all()
    )
    for request in requests:
        request.__dict__['status'] = _status_of(request)
        request.__dict__['facility'] = facilities.get(request.warehouse)
        request.__dict__['line_count'] = counts.get(request.purchase_request_id, 0)


async def attach(db: AsyncSession, request: PurchaseRequest) -> PurchaseRequest:
    """The summary fields, `warnings`, and the lines with their product and live PO line."""
    await _summaries(db, [request])
    request.__dict__.setdefault('warnings', [])
    lines = await _lines(db, request.purchase_request_id)
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
    live = await live_order_lines(db, [line.purchase_request_detail_id for line in lines])
    for line in lines:
        product = products.get(line.product)
        line.__dict__['product_code'] = product.code if product else None
        line.__dict__['min_order_qty'] = product.min_order_qty if product else None
        order_line = live.get(line.purchase_request_detail_id)
        if order_line is None:
            line.__dict__.update(
                purchase_order_detail=None, purchase_order=None, purchase_order_status=None
            )
        else:
            detail, order, completed, cancelled = order_line
            line.__dict__.update(
                purchase_order_detail=detail,
                purchase_order=order,
                purchase_order_status=derive_status(completed=completed, cancelled=cancelled),
            )
    request.__dict__['lines'] = lines
    return request


# ── Lists (FR-007, FR-012, R11) ───────────────────────────────────────────────


async def _page(
    db: AsyncSession, clauses: list[ColumnElement[bool]], order: Any, skip: int, limit: int
) -> tuple[list[PurchaseRequest], int]:
    base = select(PurchaseRequest).join(
        Warehouse, Warehouse.warehouse_id == PurchaseRequest.warehouse
    )
    total: int = (
        await db.execute(select(func.count()).select_from(base.where(*clauses).subquery()))
    ).scalar_one()
    items = list(
        (await db.execute(base.where(*clauses).order_by(order).offset(skip).limit(limit)))
        .scalars()
        .all()
    )
    await _summaries(db, items)
    return items, total


async def list_requests(
    db: AsyncSession,
    *,
    current: CurrentUser,
    everyone: bool = False,
    state: str | None = None,
    warehouse: int | None = None,
    facility: int | None = None,
    creator: int | None = None,
    serial: int | None = None,
    request_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[PurchaseRequest], int]:
    """Newest first. By default the caller's own (created or updated), without cancelled."""
    header = PurchaseRequest
    clauses: list[ColumnElement[bool]] = []
    if not everyone:
        clauses.append(
            or_(header.creator == current.employee_id, header.updater == current.employee_id)
        )
    if state is None:
        clauses.append(_flag(header.cancelled) == false())
    else:
        clauses.append(request_status_clause(state))
    if warehouse is not None:
        clauses.append(header.warehouse == warehouse)
    if facility is not None:
        clauses.append(Warehouse.facility == facility)
    if creator is not None:
        clauses.append(header.creator == creator)
    if serial is not None:
        clauses.append(header.serial == serial)
    if request_id is not None:
        clauses.append(header.purchase_request_id == request_id)
    if date_from is not None:
        clauses.append(header.date >= date_from)
    if date_to is not None:
        clauses.append(header.date < date_to + timedelta(days=1))
    return await _page(db, clauses, header.purchase_request_id.desc(), skip, limit)


async def approval_queue(
    db: AsyncSession, *, facility: int | None = None, skip: int = 0, limit: int = 20
) -> tuple[list[PurchaseRequest], int]:
    """Submitted requests of every facility, oldest first (FR-007)."""
    clauses = [request_status_clause(RequestStatus.SUBMITTED)]
    if facility is not None:
        clauses.append(Warehouse.facility == facility)
    return await _page(db, clauses, PurchaseRequest.purchase_request_id, skip, limit)
