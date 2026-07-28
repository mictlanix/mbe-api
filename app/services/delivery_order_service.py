"""The delivery order: raising it from a sale, moving it through the flow, settling it.

Every status change goes through `delivery_events.transition()`, which validates the move and
writes the audit row in the same breath. Nothing in this module sets `order.status` directly.

The quantity totals on `delivery_order_detail` are running values maintained here and in
`delivery_itinerary_service`, always inside the transaction that caused the change. They are
denormalised on purpose: the double-assignment guard reads `open_quantity` as arithmetic on the
single row it has locked, so deriving them from itinerary lines would put the values outside the
lock that protects them.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import DeliveryOrderStatus as S
from app.enums import FulfillmentType, PaymentTerms, TransactionType
from app.models.core import Facility, Warehouse
from app.models.logistics import (
    DeliveryOrder,
    DeliveryOrderDetail,
    DeliveryOrderEvent,
    ProofOfDelivery,
)
from app.models.sales import SalesOrder, SalesOrderDetail
from app.services import delivery_events, documents, stock_ledger

TERMINAL = delivery_events.TERMINAL


def _employee(current: CurrentUser) -> int:
    if current.employee_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Your user account is not linked to an employee and cannot author documents',
        )
    return current.employee_id


def open_quantity(line: DeliveryOrderDetail) -> Decimal:
    """Ordered less what is delivered, returned, or already claimed by a trip (FR-026)."""
    return (
        line.quantity - line.delivered_quantity - line.returned_quantity - line.committed_quantity
    )


def assert_editable(order: DeliveryOrder) -> None:
    """Only a draft may be edited (FR-006).

    Deliberately not `documents.assert_editable`: that reads `.completed` and `.cancelled` through
    `getattr` with a `False` default, and migration 008 dropped both columns — so it would wave
    every delivery order through instead of failing loudly (research R8).
    """
    if S(order.status) is not S.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'A delivery order in {S(order.status).name} can no longer be edited',
        )


# ── Creation from a sales order ───────────────────────────────────────────────


async def _covered_quantities(db: AsyncSession, sales_order_id: int) -> dict[int, Decimal]:
    """How much of each sales-order line existing delivery orders already account for.

    Cancelled delivery orders do not count, which is what lets an order cancelled at the 008
    cutover be re-raised and produce the right lines.
    """
    rows = (
        await db.execute(
            select(DeliveryOrderDetail.sales_order_detail, func.sum(DeliveryOrderDetail.quantity))
            .join(
                DeliveryOrder,
                DeliveryOrder.delivery_order_id == DeliveryOrderDetail.delivery_order,
            )
            .where(
                DeliveryOrderDetail.sales_order_detail.is_not(None),
                DeliveryOrder.status != S.CANCELLED,
            )
            .group_by(DeliveryOrderDetail.sales_order_detail)
        )
    ).all()
    covered = {line_id: total for line_id, total in rows if line_id is not None}

    line_ids = {
        row[0]
        for row in (
            await db.execute(
                select(SalesOrderDetail.sales_order_detail_id).where(
                    SalesOrderDetail.sales_order == sales_order_id
                )
            )
        ).all()
    }
    return {k: v for k, v in covered.items() if k in line_ids}


async def _is_facility_address(db: AsyncSession, address: int | None) -> bool:
    if address is None:
        return False
    found = (
        await db.execute(select(Facility.facility_id).where(Facility.address == address))
    ).first()
    return found is not None


async def _fallback_warehouse(db: AsyncSession, facility: int) -> int:
    # `in_transit` is excluded, not merely unlikely to win. Before spec 013 this took MIN over
    # every warehouse in the facility with no exclusion at all, so a facility whose in-transit row
    # held the lowest id would silently dispatch *from* the virtual location (FR-012).
    warehouse = (
        await db.execute(
            select(func.min(Warehouse.warehouse_id)).where(
                Warehouse.facility == facility,
                Warehouse.in_transit.is_(False),
            )
        )
    ).scalar_one_or_none()
    if warehouse is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Facility {facility} has no warehouse to dispatch from',
        )
    return warehouse


async def create_from_sales_order(
    db: AsyncSession,
    sales_order_id: int,
    *,
    current: CurrentUser,
    fulfillment_type: FulfillmentType | None = None,
) -> DeliveryOrder:
    """Raise a delivery order for what the sale still owes the customer (FR-008 – FR-015).

    `fulfillment_type` may be given to override the ship-to detection. One sales order can split
    across both kinds — the customer collects part of it at the counter and has the rest shipped —
    so the type belongs to the delivery order, not to the sale. Detection is the default because
    it is right for the ordinary case, not because it is the rule (FR-005, FR-005a).
    """
    employee = _employee(current)

    order = await db.get(SalesOrder, sales_order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')
    if not order.completed or order.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Only a completed, uncancelled sales order can be delivered',
        )
    if settings.delivery_order_requires_paid_or_credit_sales_order and not (
        order.paid or order.payment_terms == PaymentTerms.NET_D
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='This sales order must be paid or on credit terms before it can be delivered',
        )

    # Every line is deliverable. `docs/specs/06-logistics.md` says the set is the lines flagged
    # `delivery = true`, and FR-012 followed it — but that column is 0 on all 910,891 rows in this
    # database, including the 54,741 the legacy delivery orders were actually raised from, so
    # honouring it makes every call return "already fully delivered". The data says the legacy
    # system took the whole order: of 23,774 sales orders that produced a delivery order, 22,976
    # carried *every* line, and the ~3% left out are spread evenly across stockable and
    # non-stockable products — operational noise, not a rule.
    lines = list(
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == sales_order_id
                )
            )
        )
        .scalars()
        .all()
    )
    covered = await _covered_quantities(db, sales_order_id)

    deliverable = [
        (line, line.quantity - covered.get(line.sales_order_detail_id, Decimal(0)))
        for line in lines
    ]
    deliverable = [(line, remaining) for line, remaining in deliverable if remaining > 0]

    if not deliverable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='This sales order is already fully delivered',
        )

    if fulfillment_type is None:
        detected = await _is_facility_address(db, order.ship_to)
        fulfillment_type = (
            FulfillmentType.COUNTER_PICKUP if detected else FulfillmentType.DELIVERY
        )
    fallback = await _fallback_warehouse(db, order.facility)
    now = datetime.now()

    delivery = DeliveryOrder(
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        facility=order.facility,
        serial=None,
        customer=order.customer,
        ship_to=order.ship_to,
        contact=order.contact,
        date=order.promise_date,
        priority=order.priority,
        comment=None,
        status=S.DRAFT,
        fulfillment_type=fulfillment_type,
    )
    db.add(delivery)
    await db.flush()

    for line, remaining in deliverable:
        db.add(
            DeliveryOrderDetail(
                delivery_order=delivery.delivery_order_id,
                sales_order_detail=line.sales_order_detail_id,
                product=line.product,
                quantity=remaining,
                product_code=line.product_code,
                product_name=line.product_name,
                warehouse=line.warehouse if line.warehouse is not None else fallback,
                committed_quantity=Decimal(0),
                delivered_quantity=Decimal(0),
                returned_quantity=Decimal(0),
            )
        )

    delivery_events.record_creation(db, delivery, employee=employee)
    await db.commit()
    await db.refresh(delivery)
    return delivery


# ── Reading ───────────────────────────────────────────────────────────────────


async def get_order(db: AsyncSession, delivery_order_id: int) -> DeliveryOrder | None:
    return await db.get(DeliveryOrder, delivery_order_id)


async def lines_of(db: AsyncSession, delivery_order_id: int) -> Sequence[DeliveryOrderDetail]:
    return (
        (
            await db.execute(
                select(DeliveryOrderDetail).where(
                    DeliveryOrderDetail.delivery_order == delivery_order_id
                )
            )
        )
        .scalars()
        .all()
    )


async def list_orders(
    db: AsyncSession,
    *,
    current: CurrentUser,
    order_status: S | None = None,
    customer: int | None = None,
    facility: int | None = None,
    fulfillment_type: FulfillmentType | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    mine: bool = False,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[DeliveryOrder], int]:
    base: Select = select(DeliveryOrder)
    count_q: Select = select(func.count()).select_from(DeliveryOrder)

    def both(clause):  # noqa: ANN001, ANN202 — local helper, mirrors existing services
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    both(DeliveryOrder.facility == (facility if facility is not None else current.facility_id))

    if order_status is not None:
        both(DeliveryOrder.status == order_status)
    if customer is not None:
        both(DeliveryOrder.customer == customer)
    if fulfillment_type is not None:
        both(DeliveryOrder.fulfillment_type == fulfillment_type)
    if date_from is not None:
        both(DeliveryOrder.date >= date_from)
    if date_to is not None:
        both(DeliveryOrder.date <= date_to)
    if mine and current.employee_id is not None:
        # How an author finds a rejected draft: no notification is sent (FR-067)
        both(
            or_(
                DeliveryOrder.creator == current.employee_id,
                DeliveryOrder.updater == current.employee_id,
            )
        )
    if search:
        term = search.strip()
        if term.isdigit():
            both(
                or_(
                    DeliveryOrder.serial == int(term),
                    DeliveryOrder.delivery_order_id == int(term),
                )
            )

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(DeliveryOrder.delivery_order_id.desc()).offset(skip).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows, total


async def events_of(db: AsyncSession, delivery_order_id: int) -> Sequence[DeliveryOrderEvent]:
    """The full transition history, oldest first (FR-064)."""
    return (
        (
            await db.execute(
                select(DeliveryOrderEvent)
                .where(DeliveryOrderEvent.delivery_order == delivery_order_id)
                .order_by(DeliveryOrderEvent.delivery_order_event_id)
            )
        )
        .scalars()
        .all()
    )


# ── Editing ───────────────────────────────────────────────────────────────────


async def update_order(
    db: AsyncSession, order: DeliveryOrder, data: object, *, current: CurrentUser
) -> DeliveryOrder:
    assert_editable(order)
    employee = _employee(current)

    for field in ('date', 'priority', 'ship_to', 'contact', 'comment'):
        value = getattr(data, field, None)
        if value is not None:
            setattr(order, field, value)

    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def update_line(
    db: AsyncSession, order: DeliveryOrder, line_id: int, quantity: Decimal
) -> DeliveryOrderDetail:
    """Adjust an ordered quantity, refusing anything the sale does not still owe (FR-016)."""
    assert_editable(order)

    line = await db.get(DeliveryOrderDetail, line_id)
    if line is None or line.delivery_order != order.delivery_order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')

    if line.sales_order_detail is not None:
        sales_line = await db.get(SalesOrderDetail, line.sales_order_detail)
        if sales_line is not None:
            covered = await _covered_quantities(db, sales_line.sales_order)
            elsewhere = covered.get(line.sales_order_detail, Decimal(0)) - line.quantity
            if elsewhere + quantity > sales_line.quantity:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=(
                        f'The sales order line has {sales_line.quantity - elsewhere} left to '
                        f'deliver; {quantity} was requested'
                    ),
                )

    line.quantity = quantity
    await db.commit()
    await db.refresh(line)
    return line


async def delete_line(db: AsyncSession, order: DeliveryOrder, line_id: int) -> None:
    assert_editable(order)
    line = await db.get(DeliveryOrderDetail, line_id)
    if line is None or line.delivery_order != order.delivery_order_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Line not found')
    await db.delete(line)
    await db.commit()


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def _branch_target(order: DeliveryOrder) -> S:
    """Where an approved order lands, by type.

    One transition, not two: `APPROVED` is where a counter pickup rests, and is never written as
    a transient step for a delivery (FR-024).
    """
    return (
        S.APPROVED
        if FulfillmentType(order.fulfillment_type) is FulfillmentType.COUNTER_PICKUP
        else S.IN_PREPARATION
    )


async def confirm(
    db: AsyncSession, order: DeliveryOrder, *, current: CurrentUser
) -> DeliveryOrder:
    """Number the document and put it into the flow (FR-017 – FR-020)."""
    assert_editable(order)
    employee = _employee(current)

    lines = await lines_of(db, order.delivery_order_id)
    if not lines:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Cannot confirm a delivery order with no lines',
        )

    if settings.min_span_hours_for_deliveries and not current.administrator:
        earliest = datetime.now() + timedelta(hours=settings.min_span_hours_for_deliveries)
        if order.date is not None and order.date < earliest:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f'Deliveries need {settings.min_span_hours_for_deliveries} hours notice; '
                    f'the earliest available date is {earliest:%Y-%m-%d %H:%M}'
                ),
            )

    order.serial = await documents.assign_folio(db, DeliveryOrder, facility=order.facility)
    order.rejection_reason = None

    target = (
        S.PENDING_APPROVAL
        if settings.delivery_order_approval_required
        else _branch_target(order)
    )
    delivery_events.transition(db, order, target, employee=employee)

    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def approve(db: AsyncSession, order: DeliveryOrder, *, current: CurrentUser) -> DeliveryOrder:
    """Approval branches on fulfilment type in a single transition (FR-022, FR-024)."""
    employee = _employee(current)
    if S(order.status) is not S.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Only an order awaiting approval can be approved; this one is '
            f'{S(order.status).name}',
        )

    delivery_events.transition(db, order, _branch_target(order), employee=employee)
    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def reject(
    db: AsyncSession, order: DeliveryOrder, reason: str, *, current: CurrentUser
) -> DeliveryOrder:
    """Send it back to its author with a stated reason — never leave it in limbo (FR-023)."""
    employee = _employee(current)
    if S(order.status) is not S.PENDING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Only an order awaiting approval can be rejected; this one is '
            f'{S(order.status).name}',
        )

    delivery_events.transition(db, order, S.DRAFT, employee=employee, reason=reason)
    order.rejection_reason = reason.strip()
    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def mark_ready_for_pickup(
    db: AsyncSession, order: DeliveryOrder, *, current: CurrentUser
) -> DeliveryOrder:
    employee = _employee(current)
    delivery_events.transition(db, order, S.READY_FOR_PICKUP, employee=employee)
    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def requeue(db: AsyncSession, order: DeliveryOrder, *, current: CurrentUser) -> DeliveryOrder:
    """Put a failed delivery back in the loading queue (FR-051, FR-051a).

    The returned quantity goes back into the open pool. Without that transfer the re-queued order
    would have no open quantity and could never be dispatched — the goods came back and are
    available again.
    """
    employee = _employee(current)
    if S(order.status) is not S.FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Only a failed delivery can be re-queued; this one is {S(order.status).name}',
        )

    for line in await lines_of(db, order.delivery_order_id):
        line.returned_quantity = Decimal(0)

    delivery_events.transition(db, order, S.IN_PREPARATION, employee=employee)
    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def cancel(
    db: AsyncSession, order: DeliveryOrder, reason: str, *, current: CurrentUser
) -> DeliveryOrder:
    """Retire the order, releasing anything it was holding (FR-007)."""
    employee = _employee(current)

    for line in await lines_of(db, order.delivery_order_id):
        line.committed_quantity = Decimal(0)

    delivery_events.transition(db, order, S.CANCELLED, employee=employee, reason=reason)
    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


# ── Proof of delivery ─────────────────────────────────────────────────────────


def build_proof(
    db: AsyncSession,
    *,
    receiver_name: str,
    receiver_id_shown: str,
    image_file: str,
    employee: int,
) -> ProofOfDelivery:
    """Stage the evidence. Required at every terminal handover (FR-043)."""
    if not receiver_name.strip() or not receiver_id_shown.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Proof of delivery needs the receiver name and the identification shown',
        )

    proof = ProofOfDelivery(
        receiver_name=receiver_name.strip(),
        receiver_id_shown=receiver_id_shown.strip(),
        captured_time=datetime.now(),
        captured_by=employee,
        image_file=image_file,
    )
    db.add(proof)
    return proof


async def confirm_pickup(
    db: AsyncSession,
    order: DeliveryOrder,
    *,
    receiver_name: str,
    receiver_id_shown: str,
    image_file: str,
    current: CurrentUser,
) -> DeliveryOrder:
    """Hand the goods over the counter, to the same evidentiary standard as a delivery.

    Stock is consumed straight from the store warehouse: there is no in-transit step because the
    goods never travelled (FR-060).
    """
    employee = _employee(current)

    proof = build_proof(
        db,
        receiver_name=receiver_name,
        receiver_id_shown=receiver_id_shown,
        image_file=image_file,
        employee=employee,
    )
    await db.flush()

    lines = await lines_of(db, order.delivery_order_id)
    stocked = await _stocked_products(db, {line.product for line in lines})
    sales_orders: set[int] = set()

    for line in lines:
        line.delivered_quantity = line.quantity
        line.committed_quantity = Decimal(0)
        if line.product in stocked:
            stock_ledger.post_movement(
                db,
                source=TransactionType.DELIVERY_ORDER,
                reference=order.delivery_order_id,
                product=line.product,
                warehouse=line.warehouse,
                quantity=line.quantity,
                outbound=True,
            )
        if line.sales_order_detail is not None:
            sales_line = await db.get(SalesOrderDetail, line.sales_order_detail)
            if sales_line is not None:
                sales_orders.add(sales_line.sales_order)
                # Only this line's claim: a pickup may cover part of a sales order, and the rest
                # must keep its reservation or it becomes sellable twice.
                if line.product in stocked and sales_line.warehouse is not None:
                    await stock_ledger.release_reservation(
                        db,
                        sales_order=sales_line.sales_order,
                        product=line.product,
                        warehouse=sales_line.warehouse,
                        quantity=line.quantity,
                    )

    order.proof_of_delivery = proof.proof_of_delivery_id
    delivery_events.transition(db, order, S.PICKED_UP, employee=employee)
    order.updater = employee
    order.modification_time = datetime.now()

    await db.commit()
    for sales_order_id in sales_orders:
        await refresh_sales_order_delivered(db, sales_order_id)
    await db.refresh(order)
    return order


# ── Sales-order coupling ──────────────────────────────────────────────────────


async def _stocked_products(db: AsyncSession, product_ids: set[int]) -> set[int]:
    """Products whose movements the ledger records (FR-061)."""
    if not product_ids:
        return set()
    from app.models.product import Product

    rows = (
        await db.execute(
            select(Product.product_id).where(
                Product.product_id.in_(product_ids),
                Product.stockable.is_(True),
            )
        )
    ).all()
    return {row[0] for row in rows}


async def refresh_sales_order_delivered(db: AsyncSession, sales_order_id: int) -> None:
    """Mark the sale delivered once every deliverable line is (FR-071).

    The per-line coverage figures stay derived (FR-070); this whole-order terminal condition is
    the one fulfilment fact worth storing.
    """
    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == sales_order_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        return

    delivered = {
        line_id: total
        for line_id, total in (
            await db.execute(
                select(
                    DeliveryOrderDetail.sales_order_detail,
                    func.sum(DeliveryOrderDetail.delivered_quantity),
                )
                .where(DeliveryOrderDetail.sales_order_detail.is_not(None))
                .group_by(DeliveryOrderDetail.sales_order_detail)
            )
        ).all()
        if line_id is not None
    }

    complete = all(
        delivered.get(line.sales_order_detail_id, Decimal(0)) >= line.quantity for line in lines
    )
    if complete:
        order = await db.get(SalesOrder, sales_order_id)
        if order is not None and not order.delivered:
            order.delivered = True
            await db.commit()


async def delivery_coverage(db: AsyncSession, sales_order_id: int) -> list[dict[str, object]]:
    """Per-line ordered / covered / delivered / outstanding, computed not stored (FR-070)."""
    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == sales_order_id
                )
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        return []

    rows = (
        await db.execute(
            select(
                DeliveryOrderDetail.sales_order_detail,
                func.sum(DeliveryOrderDetail.quantity),
                func.sum(DeliveryOrderDetail.delivered_quantity),
            )
            .join(
                DeliveryOrder,
                DeliveryOrder.delivery_order_id == DeliveryOrderDetail.delivery_order,
            )
            .where(
                DeliveryOrderDetail.sales_order_detail.is_not(None),
                DeliveryOrder.status != S.CANCELLED,
            )
            .group_by(DeliveryOrderDetail.sales_order_detail)
        )
    ).all()
    totals = {line_id: (covered, delivered) for line_id, covered, delivered in rows if line_id}

    coverage = []
    for line in lines:
        covered, delivered = totals.get(line.sales_order_detail_id, (Decimal(0), Decimal(0)))
        coverage.append(
            {
                'sales_order_detail': line.sales_order_detail_id,
                'ordered': line.quantity,
                'covered': covered,
                'delivered': delivered,
                'outstanding': line.quantity - delivered,
            }
        )
    return coverage


__all__ = [
    'approve',
    'assert_editable',
    'build_proof',
    'cancel',
    'confirm',
    'confirm_pickup',
    'create_from_sales_order',
    'delete_line',
    'delivery_coverage',
    'events_of',
    'get_order',
    'lines_of',
    'list_orders',
    'mark_ready_for_pickup',
    'open_quantity',
    'refresh_sales_order_delivered',
    'reject',
    'requeue',
    'update_line',
    'update_order',
]
