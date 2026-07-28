from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.enums import (
    DeliveryOrderStatus,
    FulfillmentType,
    ItineraryStatus,
    ShortfallReason,
    StopOutcome,
)


class DeliveryOrder(Base):
    """A picking and shipping document raised from a sales order.

    The v2 lifecycle is one `status` column, not the five booleans the legacy application used
    (`completed`, `cancelled`, `confirmed`, `delivered`, `picked_up`). Those admitted 32
    combinations for 11 legal states, and production carried 14 distinct ones — including a row
    that was simultaneously cancelled, completed and picked up. Migration 008 drops them.
    """

    __tablename__ = 'delivery_order'
    __table_args__ = (UniqueConstraint('facility', 'serial', name='uq_delivery_order_folio'),)

    delivery_order_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    facility: Mapped[int] = mapped_column(Integer, ForeignKey('facility.facility_id'))
    # NULL until confirmation assigns the folio, which is why the unique index tolerates repeats
    serial: Mapped[int | None] = mapped_column(Integer)
    customer: Mapped[int] = mapped_column(Integer, ForeignKey('customer.customer_id'))
    ship_to: Mapped[int | None] = mapped_column(Integer, ForeignKey('address.address_id'))
    contact: Mapped[int | None] = mapped_column(Integer, ForeignKey('contact.contact_id'))
    date: Mapped[datetime | None] = mapped_column(DateTime)
    priority: Mapped[int] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[DeliveryOrderStatus] = mapped_column(
        SmallInteger, default=DeliveryOrderStatus.DRAFT, server_default='0'
    )
    # A type, not a status: set from the originating sales order and never editable afterwards
    fulfillment_type: Mapped[FulfillmentType] = mapped_column(
        SmallInteger, default=FulfillmentType.DELIVERY, server_default='0'
    )
    parent_delivery_order: Mapped[int | None] = mapped_column(
        Integer, ForeignKey('delivery_order.delivery_order_id')
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    proof_of_delivery: Mapped[int | None] = mapped_column(
        Integer, ForeignKey('proof_of_delivery.proof_of_delivery_id')
    )


class DeliveryOrderDetail(Base):
    """One product on a delivery order, carrying the running quantity totals.

    Four quantities, not five: sent quantity lives on the itinerary line, where each trip records
    its own. On this row it would be referenced by no invariant and would merely duplicate
    `committed_quantity` for the whole window both are live.
    """

    __tablename__ = 'delivery_order_detail'

    delivery_order_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_order: Mapped[int] = mapped_column(
        Integer, ForeignKey('delivery_order.delivery_order_id')
    )
    sales_order_detail: Mapped[int | None] = mapped_column(
        Integer, ForeignKey('sales_order_detail.sales_order_detail_id')
    )
    product: Mapped[int] = mapped_column(Integer, ForeignKey('product.product_id'))
    # Ordered quantity. open = quantity - delivered - returned - committed  (FR-026)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    product_code: Mapped[str] = mapped_column(String(425))
    product_name: Mapped[str] = mapped_column(String(250))
    # Reserved on an active itinerary. Retained through IN_TRANSIT and cleared only at stop
    # closure: releasing it at departure would return goods still on the truck to the open pool
    # and let a second itinerary commit them (FR-029a).
    committed_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal(0), server_default='0'
    )
    delivered_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal(0), server_default='0'
    )
    returned_quantity: Mapped[Decimal] = mapped_column(
        Numeric(18, 4), default=Decimal(0), server_default='0'
    )
    # Snapshotted at creation. The inherited path (sales_order_detail -> warehouse) is nullable at
    # both hops, and every stocked line needs a warehouse at departure (FR-025a).
    warehouse: Mapped[int] = mapped_column(Integer, ForeignKey('warehouse.warehouse_id'))


class DeliveryOrderEvent(Base):
    """One recorded status transition. Append-only.

    Written by `delivery_events.transition()` rather than by an ORM event listener: a listener
    cannot see the acting employee or the reason, both of which live in request scope.
    """

    __tablename__ = 'delivery_order_event'
    __table_args__ = (
        Index('ix_delivery_order_event_order', 'delivery_order', 'delivery_order_event_id'),
    )

    delivery_order_event_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_order: Mapped[int] = mapped_column(
        Integer, ForeignKey('delivery_order.delivery_order_id')
    )
    # NULL only on the creation entry
    from_status: Mapped[DeliveryOrderStatus | None] = mapped_column(SmallInteger)
    to_status: Mapped[DeliveryOrderStatus] = mapped_column(SmallInteger)
    employee: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    event_time: Mapped[datetime] = mapped_column(DateTime)
    reason: Mapped[str | None] = mapped_column(String(500))


class ProofOfDelivery(Base):
    """Evidence of a handover, serving both fulfilment types.

    A delivery's proof hangs off the stop and is also pointed at by each order settled there, so
    one signature can cover several orders dropped at the same place. A counter pickup's proof is
    pointed at only by its order.
    """

    __tablename__ = 'proof_of_delivery'

    proof_of_delivery_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    receiver_name: Mapped[str] = mapped_column(String(250))
    receiver_id_shown: Mapped[str] = mapped_column(String(100))
    captured_time: Mapped[datetime] = mapped_column(DateTime)
    captured_by: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    # UUID filename under settings.pod_dir. Never content-addressed: identical captures must not
    # alias, or deleting one order's proof would remove another's evidence (FR-044b).
    image_file: Mapped[str] = mapped_column(String(255))


class DeliveriesItinerary(Base):
    """One trip: a truck, a driver, and the stops that make up the route."""

    __tablename__ = 'deliveries_itinerary'
    __table_args__ = (Index('ix_deliveries_itinerary_status_date', 'status', 'date'),)

    deliveries_itinerary_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle: Mapped[int | None] = mapped_column(Integer, ForeignKey('vehicle.vehicle_id'))
    vehicle_operator: Mapped[int | None] = mapped_column(
        Integer, ForeignKey('vehicle_operator.vehicle_operator_id')
    )
    date: Mapped[date] = mapped_column(Date)
    creator: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    updater: Mapped[int] = mapped_column(Integer, ForeignKey('employee.employee_id'))
    creation_time: Mapped[datetime] = mapped_column(DateTime)
    modification_time: Mapped[datetime] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(String(500))
    warehouse: Mapped[int | None] = mapped_column(Integer, ForeignKey('warehouse.warehouse_id'))
    status: Mapped[ItineraryStatus] = mapped_column(
        SmallInteger, default=ItineraryStatus.OPEN, server_default='0'
    )
    departure_time: Mapped[datetime | None] = mapped_column(DateTime)
    return_time: Mapped[datetime | None] = mapped_column(DateTime)


class DeliveriesItineraryStop(Base):
    """One place the truck stops. The unit that closes, carrying the outcome and the proof."""

    __tablename__ = 'deliveries_itinerary_stop'
    __table_args__ = (
        UniqueConstraint('deliveries_itinerary', 'sequence', name='uq_itinerary_stop_sequence'),
    )

    deliveries_itinerary_stop_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deliveries_itinerary: Mapped[int] = mapped_column(
        Integer, ForeignKey('deliveries_itinerary.deliveries_itinerary_id')
    )
    sequence: Mapped[int] = mapped_column(SmallInteger)
    arrival_time: Mapped[datetime | None] = mapped_column(DateTime)
    outcome: Mapped[StopOutcome] = mapped_column(
        SmallInteger, default=StopOutcome.PENDING, server_default='0'
    )
    proof_of_delivery: Mapped[int | None] = mapped_column(
        Integer, ForeignKey('proof_of_delivery.proof_of_delivery_id')
    )
    comment: Mapped[str | None] = mapped_column(String(500))


class DeliveriesItineraryDetail(Base):
    """The commitment of a quantity of one delivery-order line to one stop.

    Reached through its stop alone. A direct itinerary FK alongside the stop FK would save one
    indexed join at the cost of a state where a line claims itinerary A while its stop belongs to
    itinerary B, with nothing declaring which wins.
    """

    __tablename__ = 'deliveries_itinerary_detail'

    deliveries_itinerary_detail_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    deliveries_itinerary_stop: Mapped[int] = mapped_column(
        Integer, ForeignKey('deliveries_itinerary_stop.deliveries_itinerary_stop_id')
    )
    delivery_order_detail: Mapped[int] = mapped_column(
        Integer, ForeignKey('delivery_order_detail.delivery_order_detail_id')
    )
    # What this trip claims of the line; fixed into `sent_quantity` at departure
    committed_quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6))
    sent_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), default=Decimal(0), server_default='0'
    )
    delivered_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), default=Decimal(0), server_default='0'
    )
    returned_quantity: Mapped[Decimal] = mapped_column(
        Numeric(20, 6), default=Decimal(0), server_default='0'
    )
    # Required whenever delivered < sent (FR-045)
    reason_code: Mapped[ShortfallReason | None] = mapped_column(SmallInteger)
    comment: Mapped[str | None] = mapped_column(String(500))
