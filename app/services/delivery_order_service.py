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
from app.schemas.delivery_order import DeliveryOrderLineRequest
from app.services import delivery_events, documents, stock_ledger

TERMINAL = delivery_events.TERMINAL



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


def narrow_to_requested(
    deliverable: Sequence[tuple[SalesOrderDetail, Decimal]],
    requested: Sequence[DeliveryOrderLineRequest],
) -> list[tuple[SalesOrderDetail, Decimal]]:
    """Narrow "everything the sale still owes" to a named subset, or refuse and say why (#138).

    Without this, splitting one sale across several destinations meant create-then-trim: create
    (which claimed every uncovered quantity), `PUT`/`DELETE` its lines down to what that
    destination should carry, then create the next against whatever was left. That forced every
    destination's writes to serialise — the next create would otherwise claim what the previous one
    was supposed to keep — and put the arithmetic that must sum exactly to the ordered amount in
    the client.

    Pure, and it takes the already-computed uncovered quantities rather than reading them, so the
    arithmetic is testable without a database. The bound is the same `_covered_quantities` figure
    the default path uses; only the scope differs.
    """
    uncovered = {line.sales_order_detail_id: (line, remaining) for line, remaining in deliverable}

    chosen: list[tuple[SalesOrderDetail, Decimal]] = []
    seen: set[int] = set()
    for item in requested:
        if item.sales_order_detail in seen:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Line {item.sales_order_detail} was requested more than once',
            )
        seen.add(item.sales_order_detail)

        found = uncovered.get(item.sales_order_detail)
        if found is None:
            # Covers both "not a line of this sale" and "already fully covered elsewhere". The
            # client cannot act differently on the two, and distinguishing them would leak which
            # ids exist.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f'Line {item.sales_order_detail} is not an undelivered line of this sales '
                    f'order'
                ),
            )
        line, remaining = found
        if item.quantity > remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f'Line {item.sales_order_detail} has {remaining} undelivered, '
                    f'{item.quantity} requested'
                ),
            )
        chosen.append((line, item.quantity))
    return chosen


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
    lines: Sequence[DeliveryOrderLineRequest] | None = None,
    ship_to: int | None = None,
    contact: int | None = None,
    date: datetime | None = None,
    comment: str | None = None,
) -> DeliveryOrder:
    """Raise a delivery order for what the sale still owes the customer (FR-008 – FR-015).

    `fulfillment_type` may be given to override the ship-to detection. One sales order can split
    across both kinds — the customer collects part of it at the counter and has the rest shipped —
    so the type belongs to the delivery order, not to the sale. Detection is the default because
    it is right for the ordinary case, not because it is the rule (FR-005, FR-005a).

    `lines` narrows the delivery to a named subset of the sale's undelivered quantities, for the
    same reason: one sale can split across several destinations. Omitting it keeps the original
    behaviour of claiming everything uncovered, so existing callers are unaffected (#138). An empty
    list narrows to nothing and creates the destination carrying no lines, to be filled afterwards
    with `add_line` — the point-of-sale delivery step creates a destination from its address and
    date before any quantity has been assigned to it (#165). The test below is `is not None` and
    must stay that way: `if lines:` would read the empty list as omitted and claim the whole sale.

    `ship_to`, `contact`, `date` and `comment` are the destination's own header, for that same
    split: each destination needs its own address, and often its own contact, date and
    instructions. Each falls back to the sale's value when omitted, so existing callers are
    unaffected, and each is the field `update_order` already accepts — this only removes the
    follow-up `PUT` that used to be the sole way to set them, and the window in which a draft
    holding committed quantities pointed at the wrong address (#146).
    """
    employee = current.employee_id

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
    # Not `lines`: that is the caller's requested subset, and rebinding it here made the narrowing
    # below read the sale's own lines instead — every create then failed on the first request
    # object it expected and did not have.
    sales_lines = list(
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
        for line in sales_lines
    ]
    deliverable = [(line, remaining) for line, remaining in deliverable if remaining > 0]

    if not deliverable:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='This sales order is already fully delivered',
        )

    if lines is not None:
        deliverable = narrow_to_requested(deliverable, lines)

    destination = ship_to if ship_to is not None else order.ship_to

    if fulfillment_type is None:
        # Detection reads the destination this delivery is actually going to, which is the supplied
        # address when there is one: a counter pickup is a counter pickup because of where the goods
        # end up, not because of what the sale's header happened to say.
        detected = await _is_facility_address(db, destination)
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
        ship_to=destination,
        contact=contact if contact is not None else order.contact,
        date=date if date is not None else order.promise_date,
        priority=order.priority,
        comment=comment,
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


async def sales_orders_of(
    db: AsyncSession, delivery_order_ids: Sequence[int]
) -> dict[int, list[int]]:
    """Which sales each delivery order draws on, keyed by delivery order id (#147).

    Derived rather than stored: the link lives on the lines, and a child order raised by a partial
    delivery inherits it with its lines, so there is nothing to keep in step. One query for a whole
    page, not one per row.

    A list, because a delivery order and a sales order are many-to-many: one sale splits across
    destinations, and one shipment consolidates several of a customer's sales. This took `func.min`
    until the plural landed, which answered a consolidated shipment with the lower id and dropped
    the rest — silently, since a caller reading one int cannot tell a complete answer from a
    truncated one. 261 of the 27,921 sale-linked delivery orders in this database carry two or
    three.

    Ordered by id, so the list a client sees is stable between calls rather than left to the
    database's row order.
    """
    if not delivery_order_ids:
        return {}
    rows = (
        await db.execute(
            select(DeliveryOrderDetail.delivery_order, SalesOrderDetail.sales_order)
            .join(
                SalesOrderDetail,
                SalesOrderDetail.sales_order_detail_id == DeliveryOrderDetail.sales_order_detail,
            )
            .where(DeliveryOrderDetail.delivery_order.in_(set(delivery_order_ids)))
            .distinct()
            .order_by(DeliveryOrderDetail.delivery_order, SalesOrderDetail.sales_order)
        )
    ).all()
    found: dict[int, list[int]] = {}
    for delivery_order, sales_order in rows:
        found.setdefault(delivery_order, []).append(sales_order)
    return found


async def attach_sales_orders(db: AsyncSession, orders: Sequence[DeliveryOrder]) -> None:
    """Attach the originating sales to each order, for `DeliveryOrderSummary.sales_orders` (#147).

    Written under a `__dict__` key, following `fk_expansion`: `delivery_order` has no such column,
    and an instance shared through the identity map must keep its raw values.
    """
    origins = await sales_orders_of(db, [order.delivery_order_id for order in orders])
    for order in orders:
        order.__dict__['sales_orders'] = origins.get(order.delivery_order_id, [])


async def list_orders(
    db: AsyncSession,
    *,
    current: CurrentUser,
    order_status: S | None = None,
    customer: int | None = None,
    facility: int | None = None,
    fulfillment_type: FulfillmentType | None = None,
    sales_order: int | None = None,
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
    if sales_order is not None:
        # "Which deliveries belong to sale N?" — one call, through the per-line link, rather than
        # listing the customer's every delivery order and reconciling their lines client-side
        # (#147). Cancelled orders are included: the filter answers what exists, and the status
        # filter is how a caller narrows that.
        both(
            DeliveryOrder.delivery_order_id.in_(
                select(DeliveryOrderDetail.delivery_order).join(
                    SalesOrderDetail,
                    SalesOrderDetail.sales_order_detail_id
                    == DeliveryOrderDetail.sales_order_detail,
                ).where(SalesOrderDetail.sales_order == sales_order)
            )
        )
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
    employee = current.employee_id

    for field in ('date', 'priority', 'ship_to', 'contact', 'comment'):
        value = getattr(data, field, None)
        if value is not None:
            setattr(order, field, value)

    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return order


async def add_line(
    db: AsyncSession, order: DeliveryOrder, item: DeliveryOrderLineRequest
) -> DeliveryOrderDetail:
    """Put one more of the sale's lines onto an existing draft (#163).

    Until this existed, a detail row could only be born inside `create_from_sales_order`: a line
    dropped with `DELETE` could never be restored, and a line left out at creation could never be
    added to that destination afterwards. That forced every quantity to be decided in the same call
    that creates the destination, which is the reverse of how the point-of-sale delivery step
    works — the destination is created from its address and date, then each sale line's quantity is
    assigned inside it.

    The quantity bound is `_covered_quantities`, the same figure `create_from_sales_order` and
    `update_line` use, so the three cannot between them over-claim a sales order line. It is
    computed per sale, so it stays correct when a delivery order carries lines from several: each
    line is bounded by its own sale's coverage.

    A delivery order and a sales order are many-to-many. One sale splits across destinations, and
    one shipment consolidates several sales for the same customer — both are present in this
    database. That is why the link lives on the line rather than the header (#147): the line is the
    join row, and no column on `delivery_order` could hold the relation.
    """
    assert_editable(order)

    sales_line = await db.get(SalesOrderDetail, item.sales_order_detail)
    sale = await db.get(SalesOrder, sales_line.sales_order) if sales_line is not None else None
    # The customer, and only the customer. This first shipped comparing the line's sale against the
    # one already on the order, which forbade consolidation: 261 of the 27,921 sale-linked delivery
    # orders in this database carry lines from two or three sales, so the check refused an
    # operation the business does. Facility is not checked either — 6 delivery orders span
    # facilities, 2 of them consolidated, so enforcing it would refuse real rows too. What is left
    # holds without exception across every consolidated order.
    #
    # One message for "no such line" and "another customer's line": the client cannot act
    # differently on the two, and separating them would leak which ids exist — the same reasoning
    # as `narrow_to_requested`.
    if sale is None or sale.customer != order.customer:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Line {item.sales_order_detail} is not a deliverable line of this customer',
        )

    # `first`, not `scalar_one_or_none`: nothing in the schema stops a legacy row set carrying the
    # same sales-order line twice, and that should refuse rather than raise.
    existing = (
        (
            await db.execute(
                select(DeliveryOrderDetail.delivery_order_detail_id).where(
                    DeliveryOrderDetail.delivery_order == order.delivery_order_id,
                    DeliveryOrderDetail.sales_order_detail == item.sales_order_detail,
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        # Refused rather than folded into the existing row, so that the caller's quantity always
        # means what it says: `PUT .../lines/{existing}` is the one way to change an amount.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Line {item.sales_order_detail} is already on this delivery order as line '
                f'{existing}'
            ),
        )

    covered = await _covered_quantities(db, sales_line.sales_order)
    elsewhere = covered.get(item.sales_order_detail, Decimal(0))
    if elsewhere + item.quantity > sales_line.quantity:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f'The sales order line has {sales_line.quantity - elsewhere} left to '
                f'deliver; {item.quantity} was requested'
            ),
        )

    line = DeliveryOrderDetail(
        delivery_order=order.delivery_order_id,
        sales_order_detail=sales_line.sales_order_detail_id,
        product=sales_line.product,
        quantity=item.quantity,
        product_code=sales_line.product_code,
        product_name=sales_line.product_name,
        warehouse=(
            sales_line.warehouse
            if sales_line.warehouse is not None
            else await _fallback_warehouse(db, order.facility)
        ),
        committed_quantity=Decimal(0),
        delivered_quantity=Decimal(0),
        returned_quantity=Decimal(0),
    )
    db.add(line)
    await db.commit()
    await db.refresh(line)
    return line


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
    employee = current.employee_id

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
    employee = current.employee_id
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
    employee = current.employee_id
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
    employee = current.employee_id
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
    employee = current.employee_id
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
    employee = current.employee_id

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
    employee = current.employee_id

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
    'attach_sales_orders',
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
    'sales_orders_of',
    'update_line',
    'update_order',
]
