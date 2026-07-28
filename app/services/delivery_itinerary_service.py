"""Planning a trip, dispatching it, and closing it stop by stop.

Two rules shape everything here.

**The guard.** A line's open quantity can never be committed to two itineraries. Every commitment
runs inside a transaction that first takes `SELECT ... FOR UPDATE` on the `delivery_order_detail`
row, re-reads `open_quantity`, and refuses an excess. Locking the *line* — not the order, not the
itinerary — is the narrowest lock that still makes two dispatchers queue, because the line is the
resource being consumed (research R2, SC-004).

**Committed survives departure.** `committed_quantity` is released at stop closure, never at
departure. Releasing it when the truck leaves would return goods that are physically on the road
to the open pool, and a second dispatcher could commit them (FR-029a).
"""

from collections.abc import Sequence
from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import DeliveryOrderStatus as S
from app.enums import (
    FulfillmentType,
    ItineraryStatus,
    ShortfallReason,
    StopOutcome,
    TransactionType,
)
from app.models.core import Facility, Vehicle, VehicleOperator
from app.models.logistics import (
    DeliveriesItinerary,
    DeliveriesItineraryDetail,
    DeliveriesItineraryStop,
    DeliveryOrder,
    DeliveryOrderDetail,
)
from app.models.sales import SalesOrderDetail
from app.services import delivery_events, delivery_order_service, stock_ledger

_BUCKETS = ('earlier', 'yesterday', 'today', 'tomorrow', 'day_after', 'later')


def _employee(current: CurrentUser) -> int:
    if current.employee_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Your user account is not linked to an employee and cannot author documents',
        )
    return current.employee_id


# ── Pending deliveries ────────────────────────────────────────────────────────


def bucket_for(scheduled: date_type | None, today: date_type) -> str:
    """Which tab of the sliding window a line belongs in (FR-031)."""
    if scheduled is None:
        return 'later'
    delta = (scheduled - today).days
    if delta < -1:
        return 'earlier'
    if delta == -1:
        return 'yesterday'
    if delta == 0:
        return 'today'
    if delta == 1:
        return 'tomorrow'
    if delta == 2:
        return 'day_after'
    return 'later'


async def pending_deliveries(
    db: AsyncSession, *, current: CurrentUser, today: date_type | None = None
) -> dict[str, list[dict[str, object]]]:
    """Delivery lines waiting to be loaded, grouped by scheduled date (FR-030 – FR-032).

    Exactly the lines of orders in `IN_PREPARATION` at active facilities. Counter pickups never
    appear: they reach `APPROVED` and are handed over at the counter, never loaded (FR-053).
    """
    today = today or date_type.today()

    rows = (
        await db.execute(
            select(DeliveryOrder, DeliveryOrderDetail)
            .join(
                DeliveryOrderDetail,
                DeliveryOrderDetail.delivery_order == DeliveryOrder.delivery_order_id,
            )
            .join(Facility, Facility.facility_id == DeliveryOrder.facility)
            .where(
                DeliveryOrder.status == S.IN_PREPARATION,
                DeliveryOrder.fulfillment_type == FulfillmentType.DELIVERY,
                Facility.status == 0,
            )
            .order_by(DeliveryOrder.priority.desc(), DeliveryOrder.delivery_order_id.desc())
        )
    ).all()

    grouped: dict[str, list[dict[str, object]]] = {key: [] for key in _BUCKETS}
    for order, line in rows:
        remaining = delivery_order_service.open_quantity(line)
        if remaining <= 0:
            continue
        scheduled = order.date.date() if order.date else None
        grouped[bucket_for(scheduled, today)].append(
            {
                'delivery_order': order.delivery_order_id,
                'delivery_order_detail': line.delivery_order_detail_id,
                'serial': order.serial,
                'customer': order.customer,
                'ship_to': order.ship_to,
                'date': order.date,
                'priority': order.priority,
                'product': line.product,
                'product_code': line.product_code,
                'product_name': line.product_name,
                'warehouse': line.warehouse,
                'open_quantity': remaining,
            }
        )
    return grouped


# ── Itineraries ───────────────────────────────────────────────────────────────


async def get_itinerary(db: AsyncSession, itinerary_id: int) -> DeliveriesItinerary | None:
    return await db.get(DeliveriesItinerary, itinerary_id)


def assert_open(itinerary: DeliveriesItinerary) -> None:
    if ItineraryStatus(itinerary.status) is not ItineraryStatus.OPEN:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'This itinerary is {ItineraryStatus(itinerary.status).name} and accepts no '
                f'further changes'
            ),
        )


async def _operator_warnings(db: AsyncSession, operator_id: int | None) -> list[str]:
    """An expired licence warns; it never refuses (FR-035)."""
    if operator_id is None:
        return []
    operator = await db.get(VehicleOperator, operator_id)
    if operator is None:
        return []
    if operator.expiration_date and operator.expiration_date < date_type.today():
        return [
            f'Operator licence {operator.driver_license_number} expired on '
            f'{operator.expiration_date:%Y-%m-%d}'
        ]
    return []


async def create_itinerary(
    db: AsyncSession,
    *,
    current: CurrentUser,
    date: date_type | None = None,
    vehicle: int | None = None,
    vehicle_operator: int | None = None,
    warehouse: int | None = None,
    comment: str | None = None,
) -> tuple[DeliveriesItinerary, list[str]]:
    """Open a trip (FR-033, FR-034).

    One open itinerary per vehicle, enforced under a lock on the vehicle row. MariaDB has no
    partial unique index, so "at most one row where status = OPEN per vehicle" cannot be a
    constraint; the lock makes concurrent opens queue instead of racing (research R9).
    """
    employee = _employee(current)

    if vehicle is not None:
        locked = (
            await db.execute(
                select(Vehicle.vehicle_id).where(Vehicle.vehicle_id == vehicle).with_for_update()
            )
        ).scalar_one_or_none()
        if locked is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Vehicle not found')

        existing = (
            await db.execute(
                select(DeliveriesItinerary.deliveries_itinerary_id).where(
                    DeliveriesItinerary.vehicle == vehicle,
                    DeliveriesItinerary.status == ItineraryStatus.OPEN,
                )
            )
        ).first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f'Vehicle {vehicle} already has an open itinerary ({existing[0]})',
            )

    now = datetime.now()
    itinerary = DeliveriesItinerary(
        vehicle=vehicle,
        vehicle_operator=vehicle_operator,
        date=date or date_type.today(),
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        comment=comment,
        warehouse=warehouse if warehouse is not None else await _pos_warehouse(db, current),
        status=ItineraryStatus.OPEN,
    )
    db.add(itinerary)
    await db.commit()
    await db.refresh(itinerary)
    return itinerary, await _operator_warnings(db, vehicle_operator)


async def _pos_warehouse(db: AsyncSession, current: CurrentUser) -> int | None:
    if current.point_sale_id is None:
        return None
    from app.models.core import PointSale

    return (
        await db.execute(
            select(PointSale.warehouse).where(PointSale.point_sale_id == current.point_sale_id)
        )
    ).scalar_one_or_none()


async def list_itineraries(
    db: AsyncSession,
    *,
    date_from: date_type | None = None,
    date_to: date_type | None = None,
    vehicle: int | None = None,
    vehicle_operator: int | None = None,
    warehouse: int | None = None,
    itinerary_status: ItineraryStatus | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[DeliveriesItinerary], int]:
    """All six FR-068 filters. `warehouse` is the trip's dispatch origin."""
    base: Select = select(DeliveriesItinerary)
    count_q: Select = select(func.count()).select_from(DeliveriesItinerary)

    def both(clause):  # noqa: ANN001, ANN202 — local helper, mirrors existing services
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    if date_from is not None:
        both(DeliveriesItinerary.date >= date_from)
    if date_to is not None:
        both(DeliveriesItinerary.date <= date_to)
    if vehicle is not None:
        both(DeliveriesItinerary.vehicle == vehicle)
    if vehicle_operator is not None:
        both(DeliveriesItinerary.vehicle_operator == vehicle_operator)
    if warehouse is not None:
        both(DeliveriesItinerary.warehouse == warehouse)
    if itinerary_status is not None:
        both(DeliveriesItinerary.status == itinerary_status)

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(DeliveriesItinerary.deliveries_itinerary_id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return rows, total


async def stops_of(db: AsyncSession, itinerary_id: int) -> Sequence[DeliveriesItineraryStop]:
    return (
        (
            await db.execute(
                select(DeliveriesItineraryStop)
                .where(DeliveriesItineraryStop.deliveries_itinerary == itinerary_id)
                .order_by(DeliveriesItineraryStop.sequence)
            )
        )
        .scalars()
        .all()
    )


async def lines_of_stop(db: AsyncSession, stop_id: int) -> Sequence[DeliveriesItineraryDetail]:
    return (
        (
            await db.execute(
                select(DeliveriesItineraryDetail).where(
                    DeliveriesItineraryDetail.deliveries_itinerary_stop == stop_id
                )
            )
        )
        .scalars()
        .all()
    )


async def update_itinerary(
    db: AsyncSession, itinerary: DeliveriesItinerary, data: object, *, current: CurrentUser
) -> DeliveriesItinerary:
    assert_open(itinerary)
    for field in ('date', 'vehicle', 'vehicle_operator', 'comment'):
        value = getattr(data, field, None)
        if value is not None:
            setattr(itinerary, field, value)
    itinerary.updater = _employee(current)
    itinerary.modification_time = datetime.now()
    await db.commit()
    await db.refresh(itinerary)
    return itinerary


async def cancel_itinerary(
    db: AsyncSession, itinerary: DeliveriesItinerary, *, current: CurrentUser
) -> DeliveriesItinerary:
    """Only before departure. Every commitment goes back to the open pool (FR-041)."""
    assert_open(itinerary)

    for stop in await stops_of(db, itinerary.deliveries_itinerary_id):
        for line in await lines_of_stop(db, stop.deliveries_itinerary_stop_id):
            order_line = await db.get(DeliveryOrderDetail, line.delivery_order_detail)
            if order_line is not None:
                order_line.committed_quantity -= line.committed_quantity
            await db.delete(line)
        await db.delete(stop)

    itinerary.status = ItineraryStatus.CANCELLED
    itinerary.updater = _employee(current)
    itinerary.modification_time = datetime.now()
    await db.commit()
    await db.refresh(itinerary)
    return itinerary


# ── Stops and commitments ─────────────────────────────────────────────────────


async def add_stop(
    db: AsyncSession,
    itinerary: DeliveriesItinerary,
    delivery_order_id: int,
    *,
    comment: str | None = None,
) -> DeliveriesItineraryStop:
    assert_open(itinerary)

    order = await db.get(DeliveryOrder, delivery_order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Delivery order not found'
        )
    if FulfillmentType(order.fulfillment_type) is FulfillmentType.COUNTER_PICKUP:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='A counter-pickup order is handed over in store, never loaded onto a trip',
        )
    if S(order.status) is not S.IN_PREPARATION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Only an order in IN_PREPARATION can be loaded; this one is '
                f'{S(order.status).name}'
            ),
        )

    highest = (
        await db.execute(
            select(func.max(DeliveriesItineraryStop.sequence)).where(
                DeliveriesItineraryStop.deliveries_itinerary == itinerary.deliveries_itinerary_id
            )
        )
    ).scalar_one_or_none()

    stop = DeliveriesItineraryStop(
        deliveries_itinerary=itinerary.deliveries_itinerary_id,
        sequence=(highest or 0) + 1,
        outcome=StopOutcome.PENDING,
        comment=comment,
    )
    db.add(stop)
    await db.commit()
    await db.refresh(stop)
    return stop


async def _lock_line(db: AsyncSession, line_id: int) -> DeliveryOrderDetail:
    """Serialise commitments against this line. The lock is the whole guard (SC-004)."""
    line = (
        await db.execute(
            select(DeliveryOrderDetail)
            .where(DeliveryOrderDetail.delivery_order_detail_id == line_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if line is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Delivery order line not found'
        )
    return line


async def commit_line(
    db: AsyncSession,
    itinerary: DeliveriesItinerary,
    stop: DeliveriesItineraryStop,
    *,
    delivery_order_detail: int,
    quantity: Decimal | None = None,
    comment: str | None = None,
) -> DeliveriesItineraryDetail:
    """Claim a quantity of a delivery line for this trip (FR-027, FR-028, FR-037)."""
    assert_open(itinerary)

    line = await _lock_line(db, delivery_order_detail)
    remaining = delivery_order_service.open_quantity(line)

    requested = remaining if quantity is None else quantity
    if requested <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='This line has nothing left to load',
        )
    if requested > remaining:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Only {remaining} is available on this line; {requested} was requested',
        )

    entry = DeliveriesItineraryDetail(
        deliveries_itinerary_stop=stop.deliveries_itinerary_stop_id,
        delivery_order_detail=delivery_order_detail,
        committed_quantity=requested,
        sent_quantity=Decimal(0),
        delivered_quantity=Decimal(0),
        returned_quantity=Decimal(0),
        comment=comment,
    )
    db.add(entry)
    line.committed_quantity += requested

    await db.commit()
    await db.refresh(entry)
    return entry


async def commit_whole_order(
    db: AsyncSession,
    itinerary: DeliveriesItinerary,
    stop: DeliveriesItineraryStop,
    delivery_order_id: int,
) -> list[DeliveriesItineraryDetail]:
    """Every open line of one order, through the same guarded path (FR-038)."""
    committed = []
    for line in await delivery_order_service.lines_of(db, delivery_order_id):
        if delivery_order_service.open_quantity(line) > 0:
            committed.append(
                await commit_line(
                    db, itinerary, stop, delivery_order_detail=line.delivery_order_detail_id
                )
            )
    return committed


async def adjust_commitment(
    db: AsyncSession, itinerary: DeliveriesItinerary, line_id: int, quantity: Decimal
) -> DeliveriesItineraryDetail:
    assert_open(itinerary)

    entry = await db.get(DeliveriesItineraryDetail, line_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Itinerary line not found'
        )

    order_line = await _lock_line(db, entry.delivery_order_detail)
    headroom = delivery_order_service.open_quantity(order_line) + entry.committed_quantity
    if quantity > headroom:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Only {headroom} is available on this line; {quantity} was requested',
        )

    order_line.committed_quantity += quantity - entry.committed_quantity
    entry.committed_quantity = quantity
    await db.commit()
    await db.refresh(entry)
    return entry


async def release_commitment(
    db: AsyncSession, itinerary: DeliveriesItinerary, line_id: int
) -> None:
    assert_open(itinerary)

    entry = await db.get(DeliveriesItineraryDetail, line_id)
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Itinerary line not found'
        )

    order_line = await db.get(DeliveryOrderDetail, entry.delivery_order_detail)
    if order_line is not None:
        order_line.committed_quantity -= entry.committed_quantity
    await db.delete(entry)
    await db.commit()


async def remove_stop(db: AsyncSession, itinerary: DeliveriesItinerary, stop_id: int) -> None:
    assert_open(itinerary)

    stop = await db.get(DeliveriesItineraryStop, stop_id)
    if stop is None or stop.deliveries_itinerary != itinerary.deliveries_itinerary_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Stop not found')

    for line in await lines_of_stop(db, stop_id):
        order_line = await db.get(DeliveryOrderDetail, line.delivery_order_detail)
        if order_line is not None:
            order_line.committed_quantity -= line.committed_quantity
        await db.delete(line)
    await db.delete(stop)
    await db.commit()


# ── Departure ─────────────────────────────────────────────────────────────────


async def depart(
    db: AsyncSession, itinerary: DeliveriesItinerary, *, current: CurrentUser
) -> DeliveriesItinerary:
    """Freeze what is on board and move the goods off the shelf (FR-029, FR-039, FR-057).

    `committed_quantity` on the delivery-order line is deliberately left alone: the goods are
    still spoken for, and releasing it here would let a second itinerary commit stock that is
    physically on the truck (FR-029a).
    """
    employee = _employee(current)
    assert_open(itinerary)

    stops = await stops_of(db, itinerary.deliveries_itinerary_id)
    entries: list[tuple[DeliveriesItineraryDetail, DeliveryOrderDetail]] = []
    orders: dict[int, DeliveryOrder] = {}

    for stop in stops:
        for entry in await lines_of_stop(db, stop.deliveries_itinerary_stop_id):
            order_line = await db.get(DeliveryOrderDetail, entry.delivery_order_detail)
            if order_line is None:
                continue
            entries.append((entry, order_line))
            if order_line.delivery_order not in orders:
                order = await db.get(DeliveryOrder, order_line.delivery_order)
                if order is not None:
                    orders[order_line.delivery_order] = order

    if not entries or all(entry.committed_quantity <= 0 for entry, _ in entries):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Nothing is committed to this itinerary',
        )

    overcommitted = [
        order_line.delivery_order_detail_id
        for _, order_line in entries
        if order_line.committed_quantity > order_line.quantity - order_line.delivered_quantity
    ]
    if overcommitted:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={'message': 'Lines are committed beyond what remains', 'lines': overcommitted},
        )

    stocked = await delivery_order_service._stocked_products(
        db, {order_line.product for _, order_line in entries}
    )
    sales_orders: set[int] = set()

    for entry, order_line in entries:
        entry.sent_quantity = entry.committed_quantity
        if order_line.product in stocked:
            stock_ledger.post_movement(
                db,
                source=TransactionType.DELIVERY_ORDER,
                reference=itinerary.deliveries_itinerary_id,
                product=order_line.product,
                warehouse=order_line.warehouse,
                quantity=entry.sent_quantity,
                outbound=True,
            )
            stock_ledger.post_movement(
                db,
                source=TransactionType.DELIVERY_ORDER,
                reference=itinerary.deliveries_itinerary_id,
                product=order_line.product,
                warehouse=settings.in_transit_warehouse_id,
                quantity=entry.sent_quantity,
                outbound=False,
            )
        if order_line.sales_order_detail is not None:
            sales_line = await db.get(SalesOrderDetail, order_line.sales_order_detail)
            if sales_line is not None:
                sales_orders.add(sales_line.sales_order)
                # Release only what left, and only for this product. Releasing the whole order
                # would give back the lines that stayed behind, and those would then be available
                # to sell a second time — the oversell FR-055a exists to prevent.
                if order_line.product in stocked and sales_line.warehouse is not None:
                    await stock_ledger.release_reservation(
                        db,
                        sales_order=sales_line.sales_order,
                        product=order_line.product,
                        warehouse=sales_line.warehouse,
                        quantity=entry.sent_quantity,
                    )

    for order in orders.values():
        delivery_events.transition(db, order, S.IN_TRANSIT, employee=employee)

    itinerary.status = ItineraryStatus.DEPARTED
    itinerary.departure_time = datetime.now()
    itinerary.updater = employee
    itinerary.modification_time = itinerary.departure_time

    await db.commit()
    await db.refresh(itinerary)
    return itinerary


# ── Closing a stop ────────────────────────────────────────────────────────────


async def close_stop(
    db: AsyncSession,
    itinerary: DeliveriesItinerary,
    stop: DeliveriesItineraryStop,
    *,
    outcomes: list[dict[str, object]],
    receiver_name: str,
    receiver_id_shown: str,
    image_file: str,
    current: CurrentUser,
) -> DeliveriesItineraryStop:
    """Record what the customer accepted, and settle every order at this stop.

    Seven things in one transaction: validate, store the proof, settle each order, split any
    remainder into a child order, post the inventory, update sales-order coverage, and close the
    itinerary if this was its last unresolved stop.
    """
    employee = _employee(current)

    if ItineraryStatus(itinerary.status) is not ItineraryStatus.DEPARTED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Only a departed itinerary has stops to close',
        )
    if StopOutcome(stop.outcome) is not StopOutcome.PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='This stop is already resolved'
        )

    entries = {
        entry.deliveries_itinerary_detail_id: entry
        for entry in await lines_of_stop(db, stop.deliveries_itinerary_stop_id)
    }
    stated = {int(o['line']): o for o in outcomes}
    if set(stated) != set(entries):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Every line on the stop must be accounted for',
        )

    for line_id, outcome in stated.items():
        entry = entries[line_id]
        delivered = Decimal(str(outcome['delivered_quantity']))
        if delivered > entry.sent_quantity:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Line {line_id} was sent {entry.sent_quantity}; {delivered} was accepted',
            )
        if delivered < entry.sent_quantity and outcome.get('reason_code') is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f'Line {line_id} fell short and needs a reason code',
            )

    proof = delivery_order_service.build_proof(
        db,
        receiver_name=receiver_name,
        receiver_id_shown=receiver_id_shown,
        image_file=image_file,
        employee=employee,
    )
    await db.flush()

    per_order: dict[int, list[tuple[DeliveriesItineraryDetail, DeliveryOrderDetail, Decimal]]] = {}
    stocked_cache: set[int] = set()

    for line_id, outcome in stated.items():
        entry = entries[line_id]
        order_line = await db.get(DeliveryOrderDetail, entry.delivery_order_detail)
        if order_line is None:
            continue
        delivered = Decimal(str(outcome['delivered_quantity']))
        returned = entry.sent_quantity - delivered

        entry.delivered_quantity = delivered
        entry.returned_quantity = returned
        entry.reason_code = (
            ShortfallReason(outcome['reason_code'])
            if outcome.get('reason_code') is not None
            else None
        )

        order_line.delivered_quantity += delivered
        order_line.returned_quantity += returned
        order_line.committed_quantity -= entry.committed_quantity

        per_order.setdefault(order_line.delivery_order, []).append((entry, order_line, delivered))

    all_products = {ol.product for lines in per_order.values() for _, ol, _ in lines}
    stocked_cache = await delivery_order_service._stocked_products(db, all_products)

    for lines in per_order.values():
        for entry, order_line, delivered in lines:
            returned = entry.returned_quantity
            if order_line.product not in stocked_cache:
                continue
            if entry.sent_quantity > 0:
                stock_ledger.post_movement(
                    db,
                    source=TransactionType.DELIVERY_ORDER,
                    reference=itinerary.deliveries_itinerary_id,
                    product=order_line.product,
                    warehouse=settings.in_transit_warehouse_id,
                    quantity=entry.sent_quantity,
                    outbound=True,
                )
            if returned > 0:
                stock_ledger.post_movement(
                    db,
                    source=TransactionType.DELIVERY_ORDER,
                    reference=itinerary.deliveries_itinerary_id,
                    product=order_line.product,
                    warehouse=order_line.warehouse,
                    quantity=returned,
                    outbound=False,
                )
                # The goods are back on the shelf but the customer is still owed them, so the
                # claim has to come back with them. Departure released it; without this the
                # sales order holds nothing and its stock can be sold out from under it before
                # the retry or the child order ever ships.
                await _reclaim_reservation(db, order_line, returned)

    sales_orders: set[int] = set()
    outcomes_seen: list[StopOutcome] = []

    for order_id, lines in per_order.items():
        order = await db.get(DeliveryOrder, order_id)
        if order is None:
            continue

        sent = sum(entry.sent_quantity for entry, _, _ in lines)
        accepted = sum(entry.delivered_quantity for entry, _, _ in lines)

        if accepted == sent:
            target = S.DELIVERED
            outcomes_seen.append(StopOutcome.DELIVERED)
        elif accepted == 0:
            target = S.FAILED
            outcomes_seen.append(StopOutcome.FAILED)
        else:
            target = S.PARTIALLY_DELIVERED
            outcomes_seen.append(StopOutcome.PARTIALLY_DELIVERED)

        order.proof_of_delivery = proof.proof_of_delivery_id
        reason = 'Nothing accepted at the stop' if target is S.FAILED else None
        delivery_events.transition(db, order, target, employee=employee, reason=reason)
        order.updater = employee
        order.modification_time = datetime.now()

        if target is S.PARTIALLY_DELIVERED:
            await _split_child_order(db, order, lines, employee=employee)

        for _, order_line, _ in lines:
            if order_line.sales_order_detail is not None:
                sales_line = await db.get(SalesOrderDetail, order_line.sales_order_detail)
                if sales_line is not None:
                    sales_orders.add(sales_line.sales_order)

    stop.outcome = (
        StopOutcome.DELIVERED
        if all(o is StopOutcome.DELIVERED for o in outcomes_seen)
        else StopOutcome.FAILED
        if all(o is StopOutcome.FAILED for o in outcomes_seen)
        else StopOutcome.PARTIALLY_DELIVERED
    )
    stop.arrival_time = datetime.now()
    stop.proof_of_delivery = proof.proof_of_delivery_id

    remaining = [
        s
        for s in await stops_of(db, itinerary.deliveries_itinerary_id)
        if s.deliveries_itinerary_stop_id != stop.deliveries_itinerary_stop_id
        and StopOutcome(s.outcome) is StopOutcome.PENDING
    ]
    if not remaining:
        itinerary.status = ItineraryStatus.CLOSED
        itinerary.return_time = datetime.now()

    await db.commit()
    for sales_order_id in sales_orders:
        await delivery_order_service.refresh_sales_order_delivered(db, sales_order_id)
    await db.refresh(stop)
    return stop


async def _reclaim_reservation(
    db: AsyncSession, order_line: DeliveryOrderDetail, quantity: Decimal
) -> None:
    """Re-reserve stock that came back, so the sale keeps its claim on it.

    Departure releases a reservation because the ledger has taken over recording where the goods
    are. When they return — refused at the door, or a failed stop — the ledger move is reversed,
    and the reservation has to be reinstated to match. Otherwise the retry and the partial
    delivery's child order both depend on stock nothing is holding for them.
    """
    if order_line.sales_order_detail is None:
        return
    sales_line = await db.get(SalesOrderDetail, order_line.sales_order_detail)
    if sales_line is None or sales_line.warehouse is None:
        return

    stock_ledger.reserve(
        db,
        sales_order=sales_line.sales_order,
        product=order_line.product,
        warehouse=sales_line.warehouse,
        quantity=quantity,
    )


async def _split_child_order(
    db: AsyncSession,
    parent: DeliveryOrder,
    lines: list[tuple[DeliveriesItineraryDetail, DeliveryOrderDetail, Decimal]],
    *,
    employee: int,
) -> DeliveryOrder | None:
    """Carry the unaccepted remainder into a child order (FR-048, v2 D1).

    A delivery-type child lands in `IN_PREPARATION`, not `APPROVED`: `APPROVED` is where a counter
    pickup rests, and a child that landed there would have no exit.
    """
    remainder = [
        (order_line, entry.returned_quantity)
        for entry, order_line, _ in lines
        if entry.returned_quantity > 0
    ]
    if not remainder:
        return None

    now = datetime.now()
    child = DeliveryOrder(
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        facility=parent.facility,
        serial=None,
        customer=parent.customer,
        ship_to=parent.ship_to,
        contact=parent.contact,
        date=parent.date,
        priority=parent.priority,
        comment=f'Remainder of delivery order {parent.delivery_order_id}',
        status=S.DRAFT,
        fulfillment_type=parent.fulfillment_type,
        parent_delivery_order=parent.delivery_order_id,
    )
    db.add(child)
    await db.flush()

    for order_line, quantity in remainder:
        db.add(
            DeliveryOrderDetail(
                delivery_order=child.delivery_order_id,
                sales_order_detail=order_line.sales_order_detail,
                product=order_line.product,
                quantity=quantity,
                product_code=order_line.product_code,
                product_name=order_line.product_name,
                warehouse=order_line.warehouse,
                committed_quantity=Decimal(0),
                delivered_quantity=Decimal(0),
                returned_quantity=Decimal(0),
            )
        )

    delivery_events.record_creation(db, child, employee=employee)
    child.serial = await _child_folio(db, child.facility)
    delivery_events.transition(db, child, S.IN_PREPARATION, employee=employee)
    return child


async def _child_folio(db: AsyncSession, facility: int) -> int:
    from app.services import documents

    return await documents.assign_folio(db, DeliveryOrder, facility=facility)


__all__ = [
    'add_stop',
    'adjust_commitment',
    'assert_open',
    'bucket_for',
    'cancel_itinerary',
    'close_stop',
    'commit_line',
    'commit_whole_order',
    'create_itinerary',
    'depart',
    'get_itinerary',
    'lines_of_stop',
    'list_itineraries',
    'pending_deliveries',
    'release_commitment',
    'remove_stop',
    'stops_of',
    'update_itinerary',
]
