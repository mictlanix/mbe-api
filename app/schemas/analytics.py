"""Analytics requests and responses (specs 037 and 038, contracts/api.md).

The keys are StrEnums, so an unknown measure, dimension, grain or format fails request validation
with FastAPI's 422 and the allowed values are published in OpenAPI (FR-011).
"""

from datetime import date, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
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
    # Inventory (spec 038)
    ON_HAND = 'on_hand'
    RESERVED = 'reserved'
    AVAILABLE = 'available'
    IN_TRANSIT = 'in_transit'
    STOCK_VALUE = 'stock_value'
    STOCK_COST_COVERAGE = 'stock_cost_coverage'
    UNCOSTED_QUANTITY = 'uncosted_quantity'
    UNITS_IN = 'units_in'
    UNITS_OUT = 'units_out'
    MOVEMENT_DOCUMENTS = 'movement_documents'
    SALES_VELOCITY = 'sales_velocity'
    DAYS_OF_COVER = 'days_of_cover'
    STOCK_OUT_PAIRS = 'stock_out_pairs'
    BELOW_REORDER_PAIRS = 'below_reorder_pairs'
    SUGGESTED_QUANTITY = 'suggested_quantity'
    DEAD_STOCK_QUANTITY = 'dead_stock_quantity'
    DEAD_STOCK_VALUE = 'dead_stock_value'
    SLOW_STOCK_QUANTITY = 'slow_stock_quantity'
    SLOW_STOCK_VALUE = 'slow_stock_value'
    TURNOVER = 'turnover'
    DAYS_OF_INVENTORY = 'days_of_inventory'
    # Receivables and payables
    RECEIVABLE = 'receivable'
    RECEIVABLE_CURRENT = 'receivable_current'
    RECEIVABLE_OVERDUE = 'receivable_overdue'
    OPEN_ORDERS = 'open_orders'
    CREDIT_SALES = 'credit_sales'
    COLLECTED_ON_CREDIT = 'collected_on_credit'
    DUE_IN_PERIOD = 'due_in_period'
    DUE_COLLECTED = 'due_collected'
    DSO = 'dso'
    CEI = 'cei'
    CREDIT_LIMIT = 'credit_limit'
    CREDIT_UTILIZATION = 'credit_utilization'
    PAYABLE = 'payable'
    PAYABLE_CURRENT = 'payable_current'
    PAYABLE_OVERDUE = 'payable_overdue'
    OPEN_PURCHASE_ORDERS = 'open_purchase_orders'
    # Commissions
    COMMISSION = 'commission'
    COMMISSION_BASE = 'commission_base'
    COMMISSION_LINES = 'commission_lines'
    # Fiscal
    INVOICED = 'invoiced'
    INVOICES = 'invoices'
    STANDALONE_INVOICED = 'standalone_invoiced'
    PAYMENT_RECEIPTS = 'payment_receipts'
    OUTPUT_VAT = 'output_vat'
    INVOICED_SALES = 'invoiced_sales'
    UNINVOICED_SALES = 'uninvoiced_sales'
    INVOICE_COVERAGE = 'invoice_coverage'
    INVOICING_LAG = 'invoicing_lag'
    # Purchases
    PURCHASES = 'purchases'
    PURCHASE_QUANTITY = 'purchase_quantity'
    RECEIVED_QUANTITY = 'received_quantity'
    PURCHASE_ORDERS = 'purchase_orders'
    AVERAGE_PURCHASE_PRICE = 'average_purchase_price'
    AVERAGE_SALE_PRICE = 'average_sale_price'
    PURCHASE_SALE_SPREAD = 'purchase_sale_spread'
    PURCHASE_SALE_SPREAD_PCT = 'purchase_sale_spread_pct'


class MeasureKind(StrEnum):
    """A flow sums over a period; a balance is a state at a moment (spec 038, research R1)."""

    FLOW = 'flow'
    BALANCE = 'balance'


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
    # Spec 038
    WAREHOUSE = 'warehouse'
    MOVEMENT_TYPE = 'movement_type'
    SUPPLIER = 'supplier'
    AGING_BUCKET = 'aging_bucket'
    DUE_BUCKET = 'due_bucket'
    SALES_AGENT = 'sales_agent'
    COMMISSION_PARTICIPATION = 'commission_participation'
    COMMISSION_STATUS = 'commission_status'
    ISSUER = 'issuer'
    DOCUMENT_STATUS = 'document_status'
    DOCUMENT_TYPE = 'document_type'


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
    # Spec 038
    STOCK = 'stock'
    MOVEMENTS = 'movements'
    RECEIVABLES = 'receivables'
    INVOICES = 'invoices'
    UNINVOICED_LINES = 'uninvoiced_lines'
    PURCHASES = 'purchases'
    COMMISSION_LINES = 'commission_lines'


class DetailFlag(StrEnum):
    OVERDUE = 'overdue'
    BELOW_COST = 'below_cost'
    WITHOUT_COST = 'without_cost'
    COLLECTED = 'collected'
    PAID_OUT = 'paid_out'
    STORE_CREDIT = 'store_credit'
    # Spec 038
    STOCK_OUT = 'stock_out'
    BELOW_REORDER = 'below_reorder'
    DEAD = 'dead'
    SLOW = 'slow'
    UNCOSTED = 'uncosted'
    PRICE_CHANGE = 'price_change'


class ExportSource(StrEnum):
    QUERY = 'query'
    DASHBOARD = 'dashboard'
    DETAIL = 'detail'
    KARDEX = 'kardex'
    CUSTOMER = 'customer'


class ExportFormat(StrEnum):
    XLSX = 'xlsx'
    PDF = 'pdf'
    CSV = 'csv'


class ExportStatus(StrEnum):
    PENDING = 'pending'
    RUNNING = 'running'
    READY = 'ready'
    FAILED = 'failed'


class RelativePeriod(StrEnum):
    """A saved view's period, resolved against the day it runs (spec 038, research R14)."""

    TODAY = 'today'
    YESTERDAY = 'yesterday'
    THIS_WEEK = 'this_week'
    THIS_MONTH = 'this_month'
    THIS_QUARTER = 'this_quarter'
    THIS_YEAR = 'this_year'
    LAST_WEEK = 'last_week'
    LAST_MONTH = 'last_month'
    LAST_QUARTER = 'last_quarter'
    LAST_YEAR = 'last_year'
    LAST_N_DAYS = 'last_n_days'
    FIXED = 'fixed'


class ViewKind(StrEnum):
    QUERY = 'query'
    DASHBOARD = 'dashboard'
    DETAIL = 'detail'
    CUSTOMER = 'customer'


class Cadence(StrEnum):
    DAILY = 'daily'
    WEEKLY = 'weekly'
    MONTHLY = 'monthly'


class Family(StrEnum):
    """The analytics families (FR-060); the catalog maps each to the privileges that open it."""

    SALES = 'sales'
    COST = 'cost'
    PAYMENTS = 'payments'
    BULK = 'bulk'
    # Spec 038
    INVENTORY = 'inventory'
    RECEIVABLES = 'receivables'
    PAYABLES = 'payables'
    COMMISSIONS = 'commissions'
    FISCAL = 'fiscal'
    PURCHASES = 'purchases'


class SendOutcome(StrEnum):
    RUNNING = 'running'
    SENT = 'sent'
    FAILED = 'failed'
    SKIPPED = 'skipped'


# ── Catalog ───────────────────────────────────────────────────────────────────


class AnalyticsMeasure(BaseModel):
    key: MeasureKey
    label: str
    unit: str = Field(description='money | quantity | count | ratio | days')
    family: str = Field(
        description='sales | payments | inventory | receivables | payables | commissions | '
        'fiscal | purchases'
    )
    kind: str = Field(
        description="flow: summed over the period | balance: the value at the period's end, at "
        "each date bucket's end, never added across dates"
    )
    sensitive: bool = Field(description='Needs the cost privilege (37, 38 or 39).')
    vat: bool = Field(description='Whether `vat=included` changes it.')
    additive: bool = Field(description='False for ratios, recomputed from their parts.')
    dimensions: list[DimensionKey] = Field(description='The dimensions it can be grouped by.')
    definition: str = Field(description='The written definition, with its caveats.')


class AnalyticsDimension(BaseModel):
    key: DimensionKey
    label: str
    grains: list[Grain] = Field(description='Date only: the grains it can be grouped at.')


class AnalyticsCatalogResponse(BaseModel):
    as_of: LocalDateTime
    measures: list[AnalyticsMeasure]
    dimensions: list[AnalyticsDimension]


# ── Query ─────────────────────────────────────────────────────────────────────

#: The values of the dimensions keyed by a fixed word rather than an id (spec 038).
TEXT_VALUES: dict[DimensionKey, tuple[str, ...]] = {
    DimensionKey.AGING_BUCKET: ('current', '1-30', '31-60', '61-90', '90+'),
    DimensionKey.DUE_BUCKET: ('overdue', '1-7', '8-30', '31-60', '60+'),
    DimensionKey.COMMISSION_STATUS: ('provisional', 'closed'),
    DimensionKey.DOCUMENT_STATUS: ('stamped', 'cancelled', 'pending'),
    DimensionKey.DOCUMENT_TYPE: ('invoice', 'credit_note', 'payment_receipt'),
}

#: Each stock option's allowed values: a range, or for `dead_days` a set (research R5).
STOCK_OPTION_RANGES: dict[str, tuple[int, int]] = {
    'velocity_days': (30, 365),
    'dead_days': (90, 365),
    'slow_cover_days': (30, 730),
    'lead_days': (0, 120),
    'cover_days': (1, 365),
}
DEAD_DAYS = (90, 180, 365)


class StockOptions(BaseModel):
    """The windows and targets of velocity, cover, dead and slow stock and restock (R5)."""

    model_config = ConfigDict(frozen=True)

    velocity_days: int = Field(default=90, description='30-365')
    dead_days: int = Field(default=180, description='90, 180 or 365')
    slow_cover_days: int = Field(default=180, description='30-730')
    lead_days: int = Field(default_factory=lambda: settings.restock_lead_days, description='0-120')
    cover_days: int = Field(
        default_factory=lambda: settings.restock_cover_days, description='1-365'
    )

    @model_validator(mode='after')
    def _in_range(self) -> 'StockOptions':
        for option, (low, high) in STOCK_OPTION_RANGES.items():
            value = getattr(self, option)
            allowed = value in DEAD_DAYS if option == 'dead_days' else low <= value <= high
            if not allowed:
                raise DomainError(
                    ErrorCode.ANALYTICS_STOCK_OPTIONS_INVALID,
                    f'{option} must be between {low} and {high}',
                    {'option': option, 'value': value, 'min': low, 'max': high},
                )
        return self


class AnalyticsFilter(BaseModel):
    dimension: DimensionKey
    values: list[str] = Field(
        min_length=1,
        description=(
            'Ids for entity dimensions; the value for brand, model, customer_type (credit, cash), '
            'payment_terms (immediate, credit), weekday (1 = Monday), hour, issuer (RFC), '
            'aging_bucket (current, 1-30, 31-60, 61-90, 90+), due_bucket (overdue, 1-7, 8-30, '
            '31-60, 60+), commission_status (provisional, closed), document_status (stamped, '
            'cancelled, pending) and document_type (invoice, credit_note, payment_receipt); the '
            'ISO date of the period start for date. An empty string selects "none".'
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
                case DimensionKey.BRAND | DimensionKey.MODEL | DimensionKey.ISSUER:
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
                case dimension if dimension in TEXT_VALUES:
                    if value not in TEXT_VALUES[dimension]:
                        raise PydanticCustomError(
                            'analytics_filter_value',
                            '{dimension} is one of {values}',
                            {
                                'dimension': dimension.value,
                                'values': ', '.join(TEXT_VALUES[dimension]),
                            },
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
    stock_options: StockOptions | None = Field(
        default=None, description='Inventory measures only; omitted gives the defaults.'
    )


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
FLAG_KINDS: dict[DetailFlag, tuple[DetailKind, ...]] = {
    DetailFlag.OVERDUE: (DetailKind.ORDERS, DetailKind.RECEIVABLES),
    DetailFlag.BELOW_COST: (DetailKind.LINES,),
    DetailFlag.WITHOUT_COST: (DetailKind.LINES,),
    DetailFlag.COLLECTED: (DetailKind.PAYMENTS,),
    DetailFlag.PAID_OUT: (DetailKind.PAYMENTS,),
    DetailFlag.STORE_CREDIT: (DetailKind.PAYMENTS,),
    DetailFlag.STOCK_OUT: (DetailKind.STOCK,),
    DetailFlag.BELOW_REORDER: (DetailKind.STOCK,),
    DetailFlag.DEAD: (DetailKind.STOCK,),
    DetailFlag.SLOW: (DetailKind.STOCK,),
    DetailFlag.UNCOSTED: (DetailKind.STOCK,),
    DetailFlag.PRICE_CHANGE: (DetailKind.PURCHASES,),
}


class AnalyticsDetailRequest(BaseModel):
    kind: DetailKind
    start: date
    end: date
    facility: int | None = None
    filters: list[AnalyticsFilter] = []
    flag: DetailFlag | None = Field(
        default=None,
        description='overdue (orders, receivables) | below_cost, without_cost (lines, cost '
        'privilege) | collected, paid_out, store_credit (payments) | stock_out, below_reorder, '
        'dead, slow, uncosted (stock) | price_change (purchases)',
    )
    vat: VatTreatment = VatTreatment.EXCLUDED
    sort: str | None = Field(
        default=None, description='date | amount | document; "-" = descending (default -date)'
    )
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)
    price_change_pct: int | None = Field(
        default=None,
        ge=0,
        le=1000,
        description='Whole percent: flag price_change selects purchases whose price changed by '
        'more than it (default 10).',
    )

    @model_validator(mode='after')
    def _flag_fits_the_kind(self) -> 'AnalyticsDetailRequest':
        if self.flag is not None and self.kind not in FLAG_KINDS[self.flag]:
            raise PydanticCustomError(
                'analytics_flag_kind',
                '{flag} applies to {expected}, not {kind}',
                {
                    'flag': self.flag.value,
                    'expected': ', '.join(kind.value for kind in FLAG_KINDS[self.flag]),
                    'kind': self.kind.value,
                },
            )
        return self


class AnalyticsDetailFigures(BaseModel):
    """A kind's own columns (spec 038), as decimal strings or ISO dates; null where not known."""

    values: dict[str, str | None] = Field(
        description='stock: on_hand, reserved, available, in_transit, velocity, cover, '
        'reorder_point, suggested, last_movement, value (cost privilege only) | movements: '
        'movement_type, source, reference | receivables: balance, exchange_rate, due_date, '
        'days_past_due, bucket, due_bucket | invoices: type, status, issuer, uuid, issued, '
        'stamped, cancelled, subtotal, vat, total | uninvoiced_lines: sold, invoiced, '
        'invoiced_amount, first_invoiced, lag_days | commission_lines: month, status, movement, '
        'scheme, sales_order, sales_order_detail, customer_refund_detail, order_date, paid_date, '
        'product, label, label_name, label_rate, participation, participation_name, '
        'participation_rate, rate, price, base, share, full_commission, amount | purchases: '
        'supplier, supplier_name, unit_price, previous_price, change, change_pct, received'
    )


class AnalyticsDetailItem(BaseModel):
    kind: DetailKind
    id: int = Field(
        description='sales_order_id | sales_order_detail_id | customer_refund_id | '
        'fiscal_document_id | customer_payment_id | product (stock) | lot_serial_tracking_id '
        '(movements) | sales_order_detail_id (commission_lines, uninvoiced_lines) | '
        'purchase_order_detail_id (purchases)'
    )
    document_id: int | None = Field(
        default=None,
        description='The document to open: the order for a line, the refund, the fiscal '
        "document, the payment, a movement's document; null for stock pairs.",
    )
    folio: str | None
    date: LocalDateTime | None = Field(default=None, description='Null for stock pairs.')
    facility: int | None = None
    customer: int | None
    customer_name: str | None
    salesperson: int | None
    product: int | None
    product_code: str | None
    product_name: str | None
    quantity: Decimal | None
    amount: Decimal | None = Field(
        default=None,
        description="Positive: a line's or order's sales amount, a refund's, a credit note's, or "
        "a payment's amount in its class; the balance with flag=overdue; with the request's VAT "
        'treatment. Null where a kind has no amount (stock, movements).',
    )
    cost: Decimal | None = Field(
        default=None,
        description="Cost privilege only, absent otherwise; null on uncosted lines. A stock pair's "
        'unit cost.',
    )
    margin: Decimal | None = Field(
        default=None, description='Cost privilege only, absent otherwise.'
    )
    payment_method: int | None
    warehouse: int | None = Field(default=None, description='Stock, movements and purchases.')
    figures: AnalyticsDetailFigures | None = Field(
        default=None, description="Spec 038 kinds: the kind's own columns."
    )


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
    families: list[Family] | None = Field(
        default=None,
        description='Only the tiles, series and alerts of these families, of those the caller '
        'holds; null for every one. Cost only widens the sales and inventory tiles, so alone it '
        'gives none. The UI requests tile groups in parallel (spec 038, R18).',
    )


class AnalyticsPoint(BaseModel):
    date: date
    value: Decimal | None


class AnalyticsKpi(BaseModel):
    key: str = Field(
        description='net_sales | margin_pct | orders | average_ticket | collected_today | '
        'collected | refunds | cost_coverage | stock_value'
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
        'top_products | top_customers | payment_mix | heatmap | stock_value_by_facility'
    )
    chart: str = Field(description='line | bar | donut | heatmap')
    query: AnalyticsQueryResponse


class AnalyticsAlert(BaseModel):
    key: str = Field(
        description='overdue_credit_orders | lines_below_cost | lines_without_cost | refund_rate '
        '| stock_outs | below_reorder | dead_stock | uncosted_stock | payables_overdue'
    )
    facility: int | None = Field(
        default=None, description='refund_rate only: one alert per facility where it fires.'
    )
    count: int
    amount: Decimal | None
    detail: AnalyticsDetailRequest | None = Field(
        description='The detail listing what the alert counts; null for payables_overdue, which '
        'has no detail kind (spec 038).'
    )


class AnalyticsDashboardResponse(BaseModel):
    as_of: LocalDateTime
    definition: AnalyticsDashboardRequest
    comparison_start: date
    comparison_end: date
    kpis: list[AnalyticsKpi]
    series: list[AnalyticsSeries]
    alerts: list[AnalyticsAlert]


# ── Kardex (spec 038, research R6) ────────────────────────────────────────────

#: The most entries one kardex page holds.
KARDEX_MAX_LIMIT = 500


class AnalyticsKardexRequest(BaseModel):
    """One product's ledger in one warehouse, any range from the stock history start, paged."""

    product: int
    warehouse: int
    start: date
    end: date
    skip: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=KARDEX_MAX_LIMIT)


class AnalyticsKardexRow(BaseModel):
    date: LocalDateTime
    movement_type: int
    source: str
    reference: int
    document_id: int | None = Field(description='Null when the entry does not resolve.')
    quantity: Decimal = Field(description='Signed: positive in, negative out.')
    balance: Decimal
    value: Decimal | None = Field(
        default=None,
        description='Balance × unit_cost. Cost privilege only, absent otherwise; null when the '
        'product is uncosted.',
    )


class AnalyticsKardexResponse(BaseModel):
    as_of: LocalDateTime
    product: int
    warehouse: int
    start: date
    end: date
    opening: Decimal = Field(description='The balance before start.')
    unit_cost: Decimal | None = Field(
        default=None,
        description="Today's list-0 cost, without VAT. Cost privilege only, absent otherwise; "
        'null when uncosted (0, or at least the placeholder).',
    )
    rows: list[AnalyticsKardexRow]
    total: int
    closing: Decimal = Field(description='The balance at the end of end.')


# ── Customer 360 (spec 038, FR-125) ──────────────────────────────────────────


class AnalyticsCustomerRequest(BaseModel):
    """One customer and period; the export and saved-view form of the route's parameters."""

    customer: int
    start: date
    end: date
    facility: int | None = Field(
        default=None,
        description="Omitted: the caller's own, or every facility for callers who "
        'may see them all.',
    )


class AnalyticsCustomerResponse(BaseModel):
    as_of: LocalDateTime
    customer: int
    customer_name: str
    start: date
    end: date
    sales: AnalyticsQueryResponse | None = Field(
        description='Net sales, refunds, collected (payments family) and margin (cost) for the '
        'period, with the previous period. Null without the sales family.'
    )
    receivable: AnalyticsQueryResponse | None = Field(
        description='Receivable, current and overdue now, by aging bucket. Null without the '
        'receivables family.'
    )
    top_products: AnalyticsQueryResponse | None = Field(
        description='The ten products with the most net sales in the period. Null without the '
        'sales family.'
    )
    last_purchase: LocalDateTime | None = Field(
        description='The last confirmed order in the 12 months up to end. Sales family.'
    )
    orders_per_month: Decimal | None = Field(
        description='Orders in the 12 months up to end ÷ 12, two decimals. Sales family.'
    )
    credit_limit: Decimal | None = Field(description="Spec 033's. Receivables family.")
    credit_utilization: Decimal | None = Field(
        description="Spec 033's: owed across every facility ÷ the limit; null without a limit. "
        'Receivables family.'
    )


# ── Exports ───────────────────────────────────────────────────────────────────


class AnalyticsExportRequest(BaseModel):
    """Exactly one source; a dashboard exports only to PDF (`analytics.export_source_invalid`)."""

    format: ExportFormat
    query: AnalyticsQueryRequest | None = None
    dashboard: AnalyticsDashboardRequest | None = None
    detail: AnalyticsDetailRequest | None = None
    kardex: AnalyticsKardexRequest | None = Field(
        default=None, description='Spec 038: every entry of the range; skip and limit are ignored.'
    )
    customer: AnalyticsCustomerRequest | None = Field(default=None, description='Spec 038.')


class AnalyticsExportResponse(BaseModel):
    analytics_export_id: int
    source: ExportSource
    format: ExportFormat
    status: ExportStatus
    row_count: int | None
    creation_time: LocalDateTime
    completion_time: LocalDateTime | None
    expiry_time: LocalDateTime | None
    download_url: str | None


# ── Saved views (spec 038, R14) ───────────────────────────────────────────────

#: The longest "last N days" a view may name.
MAX_PERIOD_DAYS = 3650


class AnalyticsPeriod(BaseModel):
    """A view's period: a relative key resolved the day it runs, N days, or fixed dates."""

    key: RelativePeriod
    days: int | None = Field(
        default=None, ge=1, le=MAX_PERIOD_DAYS, description='last_n_days only: 1-3,650.'
    )
    start: date | None = Field(default=None, description='fixed only.')
    end: date | None = Field(default=None, description='fixed only; not before start.')

    @model_validator(mode='after')
    def _fits_the_key(self) -> 'AnalyticsPeriod':
        days = self.key is RelativePeriod.LAST_N_DAYS
        fixed = self.key is RelativePeriod.FIXED
        if days != (self.days is not None):
            raise PydanticCustomError(
                'analytics_period', 'days goes with last_n_days, and only with it'
            )
        if fixed != (self.start is not None and self.end is not None) or (
            not fixed and (self.start is not None or self.end is not None)
        ):
            raise PydanticCustomError(
                'analytics_period', 'start and end go with fixed, and only with it'
            )
        if fixed and self.start > self.end:
            raise PydanticCustomError('analytics_period', 'The start must not follow the end')
        return self


class AnalyticsViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100, description='Unique per owner.')
    kind: ViewKind
    definition: dict[str, Any] = Field(
        description="The kind's request (query, dashboard, detail or customer 360) without "
        '`start` and `end`; the period gives them.'
    )
    period: AnalyticsPeriod
    shared_with: list[str] = Field(default=[], description='Active user ids.')


class AnalyticsViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    definition: dict[str, Any] | None = None
    period: AnalyticsPeriod | None = None
    shared_with: list[str] | None = Field(default=None, description='Replaces the list.')


class AnalyticsViewResponse(BaseModel):
    analytics_view_id: int
    name: str
    kind: ViewKind
    owner: str
    definition: dict[str, Any]
    period: AnalyticsPeriod
    shared_with: list[str] = Field(description='Shown to the owner only; empty for shared users.')
    creation_time: LocalDateTime
    modification_time: LocalDateTime


# ── Schedules (spec 038, R15–R16) ─────────────────────────────────────────────

MAX_RECIPIENTS = 10
Weekday = Annotated[int, Field(ge=1, le=7)]


def timing_problem(cadence: Cadence, weekdays: list[int], month_day: int | None) -> str | None:
    """Why a cadence and its days don't fit together, or None when they do."""
    if (cadence is Cadence.WEEKLY) != bool(weekdays):
        return 'weekdays go with weekly, which needs at least one'
    if (cadence is Cadence.MONTHLY) != (month_day is not None):
        return 'month_day goes with monthly, which needs it'
    return None


class AnalyticsScheduleCreate(BaseModel):
    analytics_view: int = Field(description="One of the caller's own views.")
    cadence: Cadence
    weekdays: list[Weekday] = Field(default=[], description='1 (Monday) to 7; weekly only.')
    month_day: int | None = Field(
        default=None, ge=1, le=31, description="Monthly only; past the month's end, its last day."
    )
    time_of_day: time = Field(description='Business time.')
    format: ExportFormat
    subject: str = Field(min_length=1, max_length=150)
    recipients: list[str] = Field(
        default=[],
        max_length=MAX_RECIPIENTS,
        description="Active users who hold the view's families (and cost for a sensitive measure).",
    )
    enabled: bool = True

    @model_validator(mode='after')
    def _days_fit_the_cadence(self) -> 'AnalyticsScheduleCreate':
        problem = timing_problem(self.cadence, self.weekdays, self.month_day)
        if problem:
            raise PydanticCustomError('analytics_schedule', problem)
        return self


class AnalyticsScheduleUpdate(BaseModel):
    """Only the fields given change. A cadence change clears the days that don't apply to it."""

    cadence: Cadence | None = None
    weekdays: list[Weekday] | None = None
    month_day: int | None = Field(default=None, ge=1, le=31)
    time_of_day: time | None = None
    format: ExportFormat | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=150)
    recipients: list[str] | None = Field(default=None, max_length=MAX_RECIPIENTS)
    enabled: bool | None = None


class AnalyticsScheduleResponse(BaseModel):
    analytics_schedule_id: int
    analytics_view: int
    cadence: Cadence
    weekdays: list[int]
    month_day: int | None
    time_of_day: time
    format: ExportFormat
    subject: str
    recipients: list[str]
    enabled: bool
    next_run_time: LocalDateTime


class AnalyticsSendResponse(BaseModel):
    analytics_send_id: int
    occurrence: LocalDateTime
    started_at: LocalDateTime
    finished_at: LocalDateTime | None
    outcome: SendOutcome = Field(description='A row left running over 10 minutes reads as failed.')
    code: str | None = Field(description='The spec 036 code of a failure or skip.')
    sent_to: list[str]
    dropped: list[str] = Field(description='Recipients who no longer qualified at send time.')
    size: int | None = Field(description='Attachment bytes.')
    row_count: int | None
