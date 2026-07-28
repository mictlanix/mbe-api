from datetime import date as date_type
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ItineraryStatus, ShortfallReason, StopOutcome

# ── Pending deliveries ────────────────────────────────────────────────────────


class PendingDeliveryLine(BaseModel):
    delivery_order: int
    delivery_order_detail: int
    serial: int | None
    customer: int
    ship_to: int | None
    date: datetime | None
    priority: int
    product: int
    product_code: str
    product_name: str
    warehouse: int
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
    arrival_time: datetime | None
    outcome: StopOutcome
    proof_of_delivery: int | None
    comment: str | None
    lines: list[ItineraryLineResponse] = []


class ItinerarySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    deliveries_itinerary_id: int
    date: date_type
    vehicle: int | None
    vehicle_operator: int | None
    warehouse: int | None
    status: ItineraryStatus
    departure_time: datetime | None
    return_time: datetime | None


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
