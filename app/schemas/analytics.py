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


# ── Detail ────────────────────────────────────────────────────────────────────

#: The kind each flag applies to (contracts/api.md "Flags").
FLAG_KINDS: dict[DetailFlag, DetailKind] = {
    DetailFlag.OVERDUE: DetailKind.ORDERS,
    DetailFlag.BELOW_COST: DetailKind.LINES,
    DetailFlag.WITHOUT_COST: DetailKind.LINES,
    DetailFlag.COLLECTED: DetailKind.PAYMENTS,
    DetailFlag.PAID_OUT: DetailKind.PAYMENTS,
    DetailFlag.STORE_CREDIT: DetailKind.PAYMENTS,
}


class AnalyticsDetailRequest(BaseModel):
    kind: DetailKind
    start: date
    end: date
    facility: int | None = None
    filters: list[AnalyticsFilter] = []
    flag: DetailFlag | None = Field(
        default=None,
        description='overdue (orders) | below_cost, without_cost (lines, cost privilege) | '
        'collected, paid_out, store_credit (payments)',
    )
    vat: VatTreatment = VatTreatment.EXCLUDED
    sort: str | None = Field(
        default=None, description='date | amount | document; "-" = descending (default -date)'
    )
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode='after')
    def _flag_fits_the_kind(self) -> 'AnalyticsDetailRequest':
        if self.flag is not None and FLAG_KINDS[self.flag] is not self.kind:
            raise PydanticCustomError(
                'analytics_flag_kind',
                '{flag} applies to {expected}, not {kind}',
                {
                    'flag': self.flag.value,
                    'expected': FLAG_KINDS[self.flag].value,
                    'kind': self.kind.value,
                },
            )
        return self


class AnalyticsDetailItem(BaseModel):
    kind: DetailKind
    id: int = Field(
        description='sales_order_id | sales_order_detail_id | customer_refund_id | '
        'fiscal_document_id | customer_payment_id'
    )
    document_id: int = Field(
        description='The document to open: the order for a line, the refund, the fiscal '
        'document, the payment.'
    )
    folio: str | None
    date: LocalDateTime
    facility: int
    customer: int | None
    customer_name: str | None
    salesperson: int | None
    product: int | None
    product_code: str | None
    product_name: str | None
    quantity: Decimal | None
    amount: Decimal = Field(
        description="Positive: a line's or order's sales amount, a refund's, a credit note's, or "
        "a payment's amount in its class; the balance with flag=overdue; with the request's VAT "
        'treatment.'
    )
    cost: Decimal | None = Field(
        default=None, description='Cost privilege only, absent otherwise; null on uncosted lines.'
    )
    margin: Decimal | None = Field(
        default=None, description='Cost privilege only, absent otherwise.'
    )
    payment_method: int | None


class AnalyticsDetailResponse(BaseModel):
    as_of: LocalDateTime
    items: list[AnalyticsDetailItem]
    total: int
    sums: dict[str, Decimal] = Field(
        description='amount, quantity, discount, and cost and margin for holders of the cost '
        'privilege, over every page: they equal the aggregate.'
    )


# ── Dashboard ─────────────────────────────────────────────────────────────────


class AnalyticsDashboardRequest(BaseModel):
    start: date
    end: date
    facility: int | None = None
    comparison: Comparison | None = Comparison.PREVIOUS_PERIOD


class AnalyticsPoint(BaseModel):
    date: date
    value: Decimal | None


class AnalyticsKpi(BaseModel):
    key: str = Field(
        description='net_sales | margin_pct | orders | average_ticket | collected_today | '
        'collected | refunds | cost_coverage'
    )
    value: Decimal | None
    comparison: Decimal | None
    change: Decimal | None
    change_pct: Decimal | None
    sparkline: list[AnalyticsPoint]
    drill: AnalyticsQueryRequest


class AnalyticsSeries(BaseModel):
    key: str = Field(
        description='sales_over_time | sales_by_facility | sales_by_label | sales_by_brand | '
        'top_products | top_customers | payment_mix | heatmap'
    )
    chart: str = Field(description='line | bar | donut | heatmap')
    query: AnalyticsQueryResponse


class AnalyticsAlert(BaseModel):
    key: str = Field(
        description='overdue_credit_orders | lines_below_cost | lines_without_cost | refund_rate'
    )
    facility: int | None = Field(
        default=None, description='refund_rate only: one alert per facility where it fires.'
    )
    count: int
    amount: Decimal | None
    detail: AnalyticsDetailRequest


class AnalyticsDashboardResponse(BaseModel):
    as_of: LocalDateTime
    definition: AnalyticsDashboardRequest
    comparison_start: date
    comparison_end: date
    kpis: list[AnalyticsKpi]
    series: list[AnalyticsSeries]
    alerts: list[AnalyticsAlert]
