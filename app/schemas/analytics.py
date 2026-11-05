"""Analytics requests and responses (spec 037, contracts/api.md).

The keys are StrEnums, so an unknown measure, dimension, grain or format fails request validation
with FastAPI's 422 and the allowed values are published in OpenAPI (FR-011).
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from app.schemas import LocalDateTime


class MeasureKey(StrEnum):
    GROSS_SALES = 'gross_sales'
    DISCOUNTS = 'discounts'
    REFUNDS = 'refunds'
    CREDIT_NOTES = 'credit_notes'
    NET_SALES = 'net_sales'
    COSTED_NET_SALES = 'costed_net_sales'
    COST_COVERAGE = 'cost_coverage'
    COST_OF_SALES = 'cost_of_sales'
    GROSS_MARGIN = 'gross_margin'
    MARGIN_PCT = 'margin_pct'
    UNITS_SOLD = 'units_sold'
    UNITS_REFUNDED = 'units_refunded'
    UNITS = 'units'
    ORDERS = 'orders'
    AVERAGE_TICKET = 'average_ticket'
    COLLECTED = 'collected'
    REFUNDS_PAID_OUT = 'refunds_paid_out'
    STORE_CREDIT_ISSUED = 'store_credit_issued'
    PAYMENTS = 'payments'


class DimensionKey(StrEnum):
    DATE = 'date'
    WEEKDAY = 'weekday'
    HOUR = 'hour'
    FACILITY = 'facility'
    POINT_OF_SALE = 'point_of_sale'
    SALESPERSON = 'salesperson'
    CASH_SESSION = 'cash_session'
    CUSTOMER = 'customer'
    CUSTOMER_TYPE = 'customer_type'
    PRODUCT = 'product'
    BRAND = 'brand'
    MODEL = 'model'
    LABEL = 'label'
    PAYMENT_TERMS = 'payment_terms'
    PAYMENT_METHOD = 'payment_method'


class Grain(StrEnum):
    DAY = 'day'
    WEEK = 'week'
    MONTH = 'month'
    QUARTER = 'quarter'
    YEAR = 'year'


class Comparison(StrEnum):
    PREVIOUS_PERIOD = 'previous_period'
    SAME_PERIOD_LAST_YEAR = 'same_period_last_year'


class VatTreatment(StrEnum):
    EXCLUDED = 'excluded'
    INCLUDED = 'included'


class DetailKind(StrEnum):
    ORDERS = 'orders'
    LINES = 'lines'
    REFUNDS = 'refunds'
    CREDIT_NOTES = 'credit_notes'
    PAYMENTS = 'payments'


class DetailFlag(StrEnum):
    OVERDUE = 'overdue'
    BELOW_COST = 'below_cost'
    WITHOUT_COST = 'without_cost'
    COLLECTED = 'collected'
    PAID_OUT = 'paid_out'
    STORE_CREDIT = 'store_credit'


class ExportSource(StrEnum):
    QUERY = 'query'
    DASHBOARD = 'dashboard'
    DETAIL = 'detail'


class ExportFormat(StrEnum):
    XLSX = 'xlsx'
    PDF = 'pdf'
    CSV = 'csv'


class ExportStatus(StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    READY = 'ready'
    FAILED = 'failed'


# ── Catalog ───────────────────────────────────────────────────────────────────


class AnalyticsMeasure(BaseModel):
    key: MeasureKey
    label: str
    unit: str = Field(description='money | quantity | count | ratio')
    sensitive: bool = Field(description='Needs the cost privilege (37, 38 or 39).')
    vat: bool = Field(description='Whether `vat=included` changes it.')
    additive: bool = Field(description='False for ratios, recomputed from their parts.')
    dimensions: list[DimensionKey] = Field(description='The dimensions it can be grouped by.')


class AnalyticsDimension(BaseModel):
    key: DimensionKey
    label: str
    grains: list[Grain] = Field(description='Date only: the grains it can be grouped at.')


class AnalyticsCatalogResponse(BaseModel):
    as_of: LocalDateTime
    measures: list[AnalyticsMeasure]
    dimensions: list[AnalyticsDimension]


# ── Query ─────────────────────────────────────────────────────────────────────


class AnalyticsFilter(BaseModel):
    dimension: DimensionKey
    values: list[str] = Field(
        min_length=1,
        description=(
            'Ids for entity dimensions; the value for brand, model, customer_type (credit, cash), '
            'payment_terms (immediate, credit), weekday (1 = Monday) and hour; the ISO date of '
            'the period start for date. An empty string selects "none".'
        ),
    )

    @model_validator(mode='after')
    def _values_fit_the_dimension(self) -> 'AnalyticsFilter':
        for value in self.values:
            if value == '':
                continue
            match self.dimension:
                case DimensionKey.DATE:
                    try:
                        date.fromisoformat(value)
                    except ValueError:
                        raise PydanticCustomError(
                            'analytics_filter_value', 'date values are ISO dates'
                        ) from None
                case DimensionKey.BRAND | DimensionKey.MODEL:
                    pass
                case DimensionKey.CUSTOMER_TYPE:
                    if value not in ('credit', 'cash'):
                        raise PydanticCustomError(
                            'analytics_filter_value', 'customer_type is credit or cash'
                        )
                case DimensionKey.PAYMENT_TERMS:
                    if value not in ('immediate', 'credit'):
                        raise PydanticCustomError(
                            'analytics_filter_value', 'payment_terms is immediate or credit'
                        )
                case _:
                    if not value.lstrip('-').isdigit():
                        raise PydanticCustomError(
                            'analytics_filter_value',
                            '{dimension} values are integer ids',
                            {'dimension': self.dimension.value},
                        )
        return self


class AnalyticsQueryRequest(BaseModel):
    measures: list[MeasureKey] = Field(min_length=1, max_length=12)
    dimensions: list[DimensionKey] = Field(default=[], description='Up to three.')
    grain: Grain = Grain.DAY
    start: date
    end: date = Field(description='Inclusive; at most five years after start.')
    facility: int | None = Field(
        default=None,
        description="One facility. Omitted: the caller's own, or every facility for callers "
        'who may see them all (administrators and holders of 101).',
    )
    filters: list[AnalyticsFilter] = []
    comparison: Comparison | None = None
    vat: VatTreatment = VatTreatment.EXCLUDED
    sort: str | None = Field(
        default=None, description='A requested measure or dimension key; "-" prefix = descending.'
    )
    top: int | None = Field(default=None, ge=1, le=100)


class AnalyticsKey(BaseModel):
    dimension: DimensionKey
    value: str | None = Field(description='The id or value; null is "none".')
    label: str


class AnalyticsRow(BaseModel):
    keys: list[AnalyticsKey]
    values: dict[str, Decimal | None]
    share: dict[str, Decimal | None]
    comparison: dict[str, Decimal | None] | None = None
    change: dict[str, Decimal | None] | None = None
    change_pct: dict[str, Decimal | None] | None = None
    drill: list[AnalyticsFilter]


class AnalyticsQueryResponse(BaseModel):
    as_of: LocalDateTime
    definition: AnalyticsQueryRequest
    rows: list[AnalyticsRow]
    others: AnalyticsRow | None = None
    totals: AnalyticsRow
    overlapping: bool = Field(
        description='True when one document can count in several rows (grouped by label, or '
        'orders by a line dimension), so rows can add up to more than the totals.'
    )
