from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.enums import DeliveryOrderStatus, FulfillmentType

# ── Lines ─────────────────────────────────────────────────────────────────────


class DeliveryOrderLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_order_detail_id: int
    sales_order_detail: int | None
    product: int
    product_code: str
    product_name: str
    warehouse: int
    quantity: Decimal
    committed_quantity: Decimal
    delivered_quantity: Decimal
    returned_quantity: Decimal

    @computed_field
    @property
    def open_quantity(self) -> Decimal:
        """What is still loadable: ordered less delivered, returned and committed (FR-026).

        Returned quantity is subtracted because those goods are accounted for elsewhere — by the
        child order a partial delivery creates, or by the requeue that puts them back in play.
        """
        return (
            self.quantity - self.delivered_quantity - self.returned_quantity
            - self.committed_quantity
        )


class DeliveryOrderLineUpdate(BaseModel):
    quantity: Decimal = Field(gt=0)


class DeliveryOrderLineRequest(BaseModel):
    """One sales-order line, and how much of it this delivery should carry (#138)."""

    sales_order_detail: int
    quantity: Decimal = Field(gt=0)


# ── Header ────────────────────────────────────────────────────────────────────


class DeliveryOrderCreate(BaseModel):
    """Raised from a sales order — the only origin (spec Assumptions)."""

    sales_order: int
    # Omitted means "work it out from the ship-to address". Supplied when one sales order splits
    # across both kinds: part collected at the counter, the rest shipped (FR-005a).
    fulfillment_type: FulfillmentType | None = None
    # Omitted means every quantity the sale still owes — the original and only behaviour. Supplied
    # when one sale splits across several destinations, so this delivery must claim a named subset
    # rather than everything left (#138). An explicit empty list is the third case and the opposite
    # of omitting it: create the destination now, carrying nothing, and assign quantities into it
    # afterwards with `POST /{id}/lines` (#163, #165). The three stay distinguishable because the
    # service tests `lines is not None`, never truthiness — `if lines:` would fold the empty list
    # back into the omitted case and claim everything the sale owes.
    lines: list[DeliveryOrderLineRequest] | None = None
    # The destination's own header, for that same split: each address needs its own contact, date
    # and instructions. Each falls back to the sale's value when omitted, so a caller that sets
    # none of them gets exactly what it got before (#146).
    ship_to: int | None = None
    contact: int | None = None
    date: datetime | None = None
    comment: str | None = None


class DeliveryOrderUpdate(BaseModel):
    date: datetime | None = None
    priority: int | None = Field(default=None, ge=0)
    ship_to: int | None = None
    contact: int | None = None
    comment: str | None = None


class ReasonRequest(BaseModel):
    """Rejection, cancellation and failure all have to say why (FR-007, FR-023)."""

    reason: str = Field(min_length=1)


class DeliveryOrderSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_order_id: int
    facility: int
    serial: int | None
    customer: int
    # The sale this delivery was raised from, derived from the lines and attached by
    # `attach_sales_order` (#147). Defaulted rather than required so a path that has not attached it
    # answers `null` instead of failing serialisation; `null` also covers a line set with no
    # sales-order link at all.
    sales_order: int | None = None
    ship_to: int | None
    date: datetime | None
    priority: int
    status: DeliveryOrderStatus
    fulfillment_type: FulfillmentType
    parent_delivery_order: int | None


class DeliveryOrderResponse(DeliveryOrderSummary):
    contact: int | None
    comment: str | None
    rejection_reason: str | None
    proof_of_delivery: int | None
    creation_time: datetime
    modification_time: datetime
    lines: list[DeliveryOrderLineResponse] = []


# ── History and proof ─────────────────────────────────────────────────────────


class DeliveryOrderEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    delivery_order_event_id: int
    from_status: DeliveryOrderStatus | None
    to_status: DeliveryOrderStatus
    employee: int
    event_time: datetime
    reason: str | None


class ProofOfDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    proof_of_delivery_id: int
    receiver_name: str
    receiver_id_shown: str
    captured_time: datetime
    captured_by: int
    # The filename only. The bytes come from an authenticated route, never a static URL (FR-044a).
    image_file: str
