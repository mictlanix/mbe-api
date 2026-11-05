"""Analytics requests and responses (spec 037, contracts/api.md).

The keys are StrEnums, so an unknown measure, dimension, grain or format fails request validation
with FastAPI's 422 and the allowed values are published in OpenAPI (FR-011).
"""

from enum import StrEnum


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
