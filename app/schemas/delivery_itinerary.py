from datetime import date as date_type
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ItineraryStatus, ShortfallReason, StopOutcome
from app.schemas import CUSTOMER_DISPLAY_NAME_DESCRIPTION, LocalDateTime
from app.schemas.sat_catalog import SatUnitOfMeasurementResponse

# ── Pending deliveries ────────────────────────────────────────────────────────


class PendingDeliveryLine(BaseModel):
    delivery_order: int
    delivery_order_detail: int
    serial: int | None
    customer: int
    #: The customer's own name, joined from `customer` (#326), as on `SalesOrderSummary`.
    customer_display_name: str | None = Field(
        default=None, description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
    )
    ship_to: int | None
    #: `ship_to` as one line, the way mbe-ui formats an address (#326). `null` if the row is gone.
    ship_to_label: str | None = None
    date: LocalDateTime | None
    priority: int
    product: int
    product_code: str
    product_name: str
    warehouse: int
    #: The warehouse's name, joined from `warehouse` (#326). `null` only if the row is gone.
    warehouse_name: str | None = None
    open_quantity: Decimal


class PendingDeliveryBucket(BaseModel):
    """One tab of the sliding window. Always present, possibly empty (FR-031)."""

    key: str
    date: date_type | None
    items: list[PendingDeliveryLine]
    total: int


class PendingDeliveriesResponse(BaseModel):
    buckets: list[PendingDeliveryBucket]


# ── Itinerary ─────────────────────────────────────────────────────────────────


class ItineraryCreate(BaseModel):
    date: date_type | None = None
    vehicle: int | None = None
    vehicle_operator: int | None = None
    warehouse: int | None = None
    comment: str | None = None


class ItineraryUpdate(BaseModel):
    date: date_type | None = None
    vehicle: int | None = None
    vehicle_operator: int | None = None
    comment: str | None = None


class ItineraryLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deliveries_itinerary_detail_id: int
    delivery_order_detail: int
    #: The order `delivery_order_detail` belongs to, so a line renders without a lookup (#343).
    delivery_order: int
    #: Snapshots on `delivery_order_detail` (#343).
    product_code: str
    product_name: str
    #: The product's unit, in the shape `SalesOrderLineResponse.unit_of_measurement` returns, read
    #: through the product (#343). `null` only if the SAT catalog has no row for the product's unit.
    unit_of_measurement: SatUnitOfMeasurementResponse | None = None
    committed_quantity: Decimal
    sent_quantity: Decimal
    delivered_quantity: Decimal
    returned_quantity: Decimal
    reason_code: ShortfallReason | None
    comment: str | None


class ItineraryStopResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deliveries_itinerary_stop_id: int
    sequence: int
    arrival_time: LocalDateTime | None
    outcome: StopOutcome
    proof_of_delivery: int | None
    comment: str | None
    #: The order the stop was added for (#343). `null` on the stops migration 008 made from legacy
    #: itineraries, which mix orders and customers.
    delivery_order: int | None
    #: `delivery_order` plus every order the stop's lines belong to, sorted and distinct (#343). A
    #: stop can carry lines of more than one order (06a §4).
    delivery_orders: list[int] = []
    #: The stored order's customer and ship-to, as on `PendingDeliveryLine` (#343). `null` when the
    #: stop has no stored order, or the order has no ship-to.
    customer_display_name: str | None = Field(
        default=None,
        description=CUSTOMER_DISPLAY_NAME_DESCRIPTION
        + ' On a stop, also null when the stop has no stored delivery order.',
    )
    ship_to_label: str | None = None
    lines: list[ItineraryLineResponse] = []


class ItinerarySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deliveries_itinerary_id: int
    date: date_type
    vehicle: int | None
    #: `nickname · license_plate`, as the printed itinerary shows the vehicle (#343).
    vehicle_name: str | None = None
    vehicle_operator: int | None
    #: The operator's employee name, as every `creator_name` reports it (#343).
    vehicle_operator_name: str | None = None
    warehouse: int | None
    #: The dispatch warehouse's name (#343).
    warehouse_name: str | None = None
    status: ItineraryStatus
    departure_time: LocalDateTime | None
    return_time: LocalDateTime | None


class ItineraryResponse(ItinerarySummary):
    comment: str | None
    stops: list[ItineraryStopResponse] = []
    # Advisory only — an expired operator licence warns, it never refuses (FR-035)
    warnings: list[str] = []


# ── Stops and commitments ─────────────────────────────────────────────────────


class StopCreate(BaseModel):
    delivery_order: int
    comment: str | None = None


class CommitLineRequest(BaseModel):
    delivery_order_detail: int
    # Omitted means "fill to the line's open quantity" (FR-037)
    quantity: Decimal | None = Field(default=None, gt=0)
    comment: str | None = None


class CommitOrderRequest(BaseModel):
    """Commit every open line of one delivery order in a single call (FR-038)."""

    delivery_order: int


class CommitLineUpdate(BaseModel):
    quantity: Decimal = Field(gt=0)


# ── Closing a stop ────────────────────────────────────────────────────────────


class StopClosureLine(BaseModel):
    line: int
    delivered_quantity: Decimal = Field(ge=0)
    # Required whenever delivered falls short of sent (FR-045)
    reason_code: ShortfallReason | None = None


class StopClosureRequest(BaseModel):
    """Proof plus per-line outcome. A status flip alone is not evidence (FR-043)."""

    receiver_name: str = Field(min_length=1)
    receiver_id_shown: str = Field(min_length=1)
    lines: list[StopClosureLine]
