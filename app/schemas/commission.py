"""Commission settings and periods (spec 025, contracts/commissions.md)."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.enums import CommissionMovement, CommissionScheme
from app.schemas import LocalDateTime

# ── Periods ───────────────────────────────────────────────────────────────────


class CommissionLineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scheme: int
    movement: Literal['sale', 'refund']
    sales_order: int
    sales_order_detail: int
    customer_refund_detail: int | None
    order_date: LocalDateTime
    paid_date: LocalDateTime
    customer: int
    customer_name: str
    product: int
    product_name: str
    label: int | None
    label_name: str | None
    label_rate: Decimal
    salesperson: int
    order_salesperson: int
    customer_salesperson: int
    participation: int
    participation_name: str
    participation_rate: Decimal
    rate: Decimal
    price: Decimal
    quantity: Decimal
    total: Decimal
    full_commission: Decimal
    share: Decimal | None
    commission: Decimal

    @field_validator('movement', mode='before')
    @classmethod
    def _movement_name(cls, value: object) -> object:
        if isinstance(value, int):
            return 'refund' if value == CommissionMovement.REFUND else 'sale'
        return value


class CommissionTotalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    salesperson: int
    label: int | None
    label_name: str | None
    lines: int
    commission: Decimal


class CommissionPeriodResponse(BaseModel):
    """A month's state and audit trail, without its lines."""

    model_config = ConfigDict(from_attributes=True)

    year: int
    month: int
    status: Literal['open', 'closed', 'paid']
    closed_by: int | None
    closed_at: LocalDateTime | None
    reopened_by: int | None
    reopened_at: LocalDateTime | None
    paid_by: int | None
    paid_at: LocalDateTime | None

    @field_validator('status', mode='before')
    @classmethod
    def _status_name(cls, value: object) -> object:
        return {0: 'open', 1: 'closed', 2: 'paid'}.get(value, value)


class CommissionPeriodDetail(CommissionPeriodResponse):
    """A month as the report reads it (FR-012): final once closed, computed live while open."""

    final: bool
    totals: list[CommissionTotalResponse]
    lines: list[CommissionLineResponse]


class CommissionPeriodSummary(CommissionPeriodResponse):
    total: Decimal = Field(description='Sum of the stored lines; 0 for a reopened month.')


# ── Settings ──────────────────────────────────────────────────────────────────

_Rate = Annotated[Decimal, Field(ge=0, le=1)]


class CommissionLabelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    commission_rate: _Rate
    comment: str | None = Field(None, max_length=50)


class CommissionLabelUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=50)
    commission_rate: _Rate | None = None
    comment: str | None = Field(None, max_length=50)


class CommissionLabelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    commission_id: int
    name: str
    commission_rate: Decimal
    comment: str | None


class CommissionParticipationBody(BaseModel):
    name: str = Field(min_length=1, max_length=50)


class CommissionParticipationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    commission_participation_id: int
    name: str


class CommissionAgentCreate(BaseModel):
    employee: int
    scheme: CommissionScheme = CommissionScheme.FULLY_PAID


class CommissionAgentUpdate(BaseModel):
    scheme: CommissionScheme


class CommissionAgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    employee: int
    name: str
    scheme: int


class CommissionAgentRateCreate(BaseModel):
    salesperson: int
    commission: int
    commission_participation: int
    participation_rate: _Rate


class CommissionAgentRateUpdate(BaseModel):
    participation_rate: _Rate


class CommissionAgentRateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    commission_salesperson_id: int
    salesperson: int
    commission: int
    commission_participation: int
    participation_rate: Decimal


class CommissionProductLabelSet(BaseModel):
    commission: int


class CommissionProductLabelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product: int
    product_code: str
    product_name: str
    commission: int
