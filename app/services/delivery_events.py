"""The one door every delivery-order status change goes through.

`transition()` moves the status and writes the `delivery_order_event` row together, so "no status
change goes unrecorded" (SC-008) holds by construction rather than by everyone remembering. v2 §6
prescribes a SQLAlchemy event listener instead; that was overridden deliberately (research R7).
A mapper listener cannot see *who* acted or *why* — both live in request scope — it fires on flush
rather than on the transition, and it never sees the creation of the order at all.

Legality is keyed on `(from, to, fulfillment_type)`, not on the two statuses alone. Since the
branch happens *at* approval (FR-024), a plain `{from: {to}}` mapping would let a delivery order
reach `READY_FOR_PICKUP`.
"""

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import DeliveryOrderStatus as S
from app.enums import FulfillmentType
from app.models.logistics import DeliveryOrder, DeliveryOrderEvent

TERMINAL: frozenset[S] = frozenset({S.PICKED_UP, S.DELIVERED, S.PARTIALLY_DELIVERED, S.CANCELLED})

#: Every transition the state machine permits, before the fulfilment-type filter below.
LEGAL: dict[S, frozenset[S]] = {
    S.DRAFT: frozenset({S.PENDING_APPROVAL, S.APPROVED, S.IN_PREPARATION, S.CANCELLED}),
    S.PENDING_APPROVAL: frozenset({S.APPROVED, S.IN_PREPARATION, S.DRAFT, S.CANCELLED}),
    S.APPROVED: frozenset({S.READY_FOR_PICKUP, S.CANCELLED}),
    S.READY_FOR_PICKUP: frozenset({S.PICKED_UP, S.CANCELLED}),
    S.IN_PREPARATION: frozenset({S.IN_TRANSIT, S.CANCELLED}),
    # Not cancellable: goods on the road are resolved at the stop, never by cancelling (FR-007)
    S.IN_TRANSIT: frozenset({S.DELIVERED, S.PARTIALLY_DELIVERED, S.FAILED}),
    S.FAILED: frozenset({S.IN_PREPARATION, S.CANCELLED}),
    S.PICKED_UP: frozenset(),
    S.DELIVERED: frozenset(),
    S.PARTIALLY_DELIVERED: frozenset(),
    S.CANCELLED: frozenset(),
}

#: Transitions only one fulfilment type may take. Everything else is legal for both.
TYPE_RESTRICTED: dict[tuple[S, S], FulfillmentType] = {
    (S.DRAFT, S.IN_PREPARATION): FulfillmentType.DELIVERY,
    (S.DRAFT, S.APPROVED): FulfillmentType.COUNTER_PICKUP,
    (S.PENDING_APPROVAL, S.IN_PREPARATION): FulfillmentType.DELIVERY,
    (S.PENDING_APPROVAL, S.APPROVED): FulfillmentType.COUNTER_PICKUP,
    (S.APPROVED, S.READY_FOR_PICKUP): FulfillmentType.COUNTER_PICKUP,
}

#: Landing on one of these without saying why is refused. A status flip alone explains nothing.
REASON_REQUIRED: frozenset[S] = frozenset({S.DRAFT, S.FAILED, S.CANCELLED})


def assert_legal(order: DeliveryOrder, to_status: S) -> None:
    """Refuse anything the state machine does not permit, naming the attempted move (FR-002)."""
    from_status = S(order.status)

    if from_status in TERMINAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'{from_status.name} is terminal; cannot move to {to_status.name}'
            ),
        )

    if to_status not in LEGAL[from_status]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f'Cannot move a delivery order from {from_status.name} to {to_status.name}',
        )

    required = TYPE_RESTRICTED.get((from_status, to_status))
    if required is not None and FulfillmentType(order.fulfillment_type) is not required:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'{from_status.name} to {to_status.name} is only valid for a '
                f'{required.name} order'
            ),
        )


def transition(
    db: AsyncSession,
    order: DeliveryOrder,
    to_status: S,
    *,
    employee: int,
    reason: str | None = None,
) -> DeliveryOrderEvent:
    """Move the order and record the move. The caller commits both with its own transaction."""
    assert_legal(order, to_status)

    if to_status in REASON_REQUIRED and not (reason or '').strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'A reason is required to move an order to {to_status.name}',
        )

    event = DeliveryOrderEvent(
        delivery_order=order.delivery_order_id,
        from_status=S(order.status),
        to_status=to_status,
        employee=employee,
        event_time=datetime.now(),
        reason=reason.strip() if reason else None,
    )
    db.add(event)
    order.status = to_status
    return event


def record_creation(db: AsyncSession, order: DeliveryOrder, *, employee: int) -> DeliveryOrderEvent:
    """The first entry of an order's history, with no status left behind (FR-065).

    Separate from `transition()` because there is no `from_status` to validate — an update
    listener would never see this event at all, which is part of why R7 rejected one.
    """
    event = DeliveryOrderEvent(
        delivery_order=order.delivery_order_id,
        from_status=None,
        to_status=S.DRAFT,
        employee=employee,
        event_time=datetime.now(),
        reason=None,
    )
    db.add(event)
    order.status = S.DRAFT
    return event
