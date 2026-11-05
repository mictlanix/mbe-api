"""The metrics catalog (spec 037, FR-001, FR-004–FR-006, data-model.md "Catalog").

Every number analytics serves is one of these measures, grouped by one of these dimensions. Each is
defined here once; the facts, the engine, the dashboard, the detail and the exports all read it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from app.enums import SystemObject
from app.schemas.analytics import DimensionKey, Grain, MeasureKey


class Fact(StrEnum):
    """The four document facts a measure is computed from (data-model.md "Facts")."""

    SALE_LINE = 'sale_line'
    REFUND_LINE = 'refund_line'
    CREDIT_NOTE = 'credit_note'
    PAYMENT = 'payment'


class Availability(StrEnum):
    """How a fact provides a dimension."""

    COLUMN = 'column'  # the fact has it
    NONE = 'none'  # the fact has no value for it: its money groups under "none"
    REFUSED = 'refused'  # grouping the fact by it makes no sense: the request is refused


COLUMN, NONE, REFUSED = Availability.COLUMN, Availability.NONE, Availability.REFUSED

SALES_FACTS = (Fact.SALE_LINE, Fact.REFUND_LINE, Fact.CREDIT_NOTE)


class Family(StrEnum):
    SALES = 'sales'
    COST = 'cost'
    PAYMENTS = 'payments'
    BULK = 'bulk'


#: The existing report privileges that open each family (FR-060, research R11).
FAMILY_PRIVILEGES: dict[Family, tuple[SystemObject, ...]] = {
    Family.SALES: (
        SystemObject.SALES_BY_CUSTOMER,
        SystemObject.SALES_BY_SALES_PERSON,
        SystemObject.SALES_BY_PRODUCT,
        SystemObject.BEST_SELLING_PRODUCTS_BY_CUSTOMER,
        SystemObject.BEST_SELLING_PRODUCTS_BY_SALES_PERSON,
        SystemObject.SALES_ORDER_SUMMARY_REPORT,
        SystemObject.PRODUCT_SALES_BY_CUSTOMER_REPORT,
        SystemObject.PRODUCT_SALES_BY_MODEL_REPORT,
        SystemObject.PRODUCT_SALES_BY_BRAND_REPORT,
        SystemObject.PRODUCT_SALES_BY_SALES_PERSON,
        SystemObject.PRODUCT_SALES_BY_SALES_PERSON_AND_LABEL,
        SystemObject.PRODUCT_SALES_BY_SALES_PERSON_AND_BRAND,
        SystemObject.PRODUCT_SALES_BY_SALES_PERSON_AND_MODEL,
    ),
    Family.COST: (
        SystemObject.GROSS_PROFITS_BY_CUSTOMER,
        SystemObject.GROSS_PROFITS_BY_SALES_PERSON,
        SystemObject.GROSS_PROFITS_BY_PRODUCT,
    ),
    Family.PAYMENTS: (SystemObject.RECEIVED_PAYMENTS, SystemObject.RECEIVED_PAYMENTS_SUMMARY),
    Family.BULK: (SystemObject.DOWNLOAD_CSV_FILES,),
}


@dataclass(frozen=True)
class Measure:
    key: MeasureKey
    label: str
    unit: str  # money | quantity | count | ratio
    facts: tuple[Fact, ...]
    sensitive: bool = False
    vat: bool = False  # vat=included changes it
    additive: bool = True

    @property
    def family(self) -> Family:
        return Family.PAYMENTS if self.facts == (Fact.PAYMENT,) else Family.SALES


@dataclass(frozen=True)
class Dimension:
    key: DimensionKey
    label: str
    facts: dict[Fact, Availability]
    grains: tuple[Grain, ...] = ()


def _measure(key: MeasureKey, label: str, unit: str, *facts: Fact, **flags: bool) -> Measure:
    return Measure(key, label, unit, facts, **flags)


_SALE, _REFUND, _NOTE, _PAY = Fact.SALE_LINE, Fact.REFUND_LINE, Fact.CREDIT_NOTE, Fact.PAYMENT

MEASURES: dict[MeasureKey, Measure] = {
    m.key: m
    for m in (
        _measure(MeasureKey.GROSS_SALES, 'Gross sales', 'money', _SALE, vat=True),
        _measure(MeasureKey.DISCOUNTS, 'Discounts', 'money', _SALE, vat=True),
        _measure(MeasureKey.REFUNDS, 'Refunds', 'money', _REFUND, vat=True),
        _measure(MeasureKey.CREDIT_NOTES, 'Credit notes', 'money', _NOTE, vat=True),
        _measure(MeasureKey.NET_SALES, 'Net sales', 'money', _SALE, _REFUND, _NOTE, vat=True),
        _measure(
            MeasureKey.COSTED_NET_SALES, 'Costed net sales', 'money', _SALE, _REFUND,
            sensitive=True,
        ),
        _measure(
            MeasureKey.COST_COVERAGE, 'Cost coverage', 'ratio', _SALE, _REFUND, _NOTE,
            sensitive=True, additive=False,
        ),
        _measure(
            MeasureKey.COST_OF_SALES, 'Cost of sales', 'money', _SALE, _REFUND, sensitive=True
        ),
        _measure(
            MeasureKey.GROSS_MARGIN, 'Gross margin', 'money', _SALE, _REFUND, sensitive=True
        ),
        _measure(
            MeasureKey.MARGIN_PCT, 'Margin %', 'ratio', _SALE, _REFUND,
            sensitive=True, additive=False,
        ),
        _measure(MeasureKey.UNITS_SOLD, 'Units sold', 'quantity', _SALE),
        _measure(MeasureKey.UNITS_REFUNDED, 'Units refunded', 'quantity', _REFUND),
        _measure(MeasureKey.UNITS, 'Units', 'quantity', _SALE, _REFUND),
        _measure(MeasureKey.ORDERS, 'Orders', 'count', _SALE),
        _measure(MeasureKey.AVERAGE_TICKET, 'Average ticket', 'money', _SALE, additive=False),
        _measure(MeasureKey.COLLECTED, 'Collected', 'money', _PAY),
        _measure(MeasureKey.REFUNDS_PAID_OUT, 'Refunds paid out', 'money', _PAY),
        _measure(MeasureKey.STORE_CREDIT_ISSUED, 'Store credit issued', 'money', _PAY),
        _measure(MeasureKey.PAYMENTS, 'Payments', 'count', _PAY),
    )
}  # fmt: skip


def _dimension(
    key: DimensionKey,
    label: str,
    sale: Availability,
    refund: Availability,
    note: Availability,
    payment: Availability,
    grains: tuple[Grain, ...] = (),
) -> Dimension:
    return Dimension(key, label, {_SALE: sale, _REFUND: refund, _NOTE: note, _PAY: payment}, grains)


#: Which fact provides which dimension (data-model.md "Dimension").
DIMENSIONS: dict[DimensionKey, Dimension] = {
    d.key: d
    for d in (
        _dimension(DimensionKey.DATE, 'Date', COLUMN, COLUMN, COLUMN, COLUMN, tuple(Grain)),
        _dimension(DimensionKey.WEEKDAY, 'Weekday', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.HOUR, 'Hour', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.FACILITY, 'Facility', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.POINT_OF_SALE, 'Point of sale', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(DimensionKey.SALESPERSON, 'Salesperson', COLUMN, COLUMN, NONE, COLUMN),
        _dimension(DimensionKey.CASH_SESSION, 'Cash session', REFUSED, REFUSED, REFUSED, COLUMN),
        _dimension(DimensionKey.CUSTOMER, 'Customer', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.CUSTOMER_TYPE, 'Customer type', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.PRODUCT, 'Product', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(DimensionKey.BRAND, 'Brand', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(DimensionKey.MODEL, 'Model', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(DimensionKey.LABEL, 'Label', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(DimensionKey.PAYMENT_TERMS, 'Payment terms', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(
            DimensionKey.PAYMENT_METHOD, 'Payment method', REFUSED, REFUSED, REFUSED, COLUMN
        ),
    )
}  # fmt: skip

#: Dimensions that split an order across rows: an order with two products counts in both.
LINE_DIMENSIONS = frozenset(
    {DimensionKey.PRODUCT, DimensionKey.BRAND, DimensionKey.MODEL, DimensionKey.LABEL}
)


def applicable(measure: MeasureKey, dimension: DimensionKey) -> bool:
    """Whether `measure` can be grouped or filtered by `dimension`: no fact it reads refuses it."""
    facts = DIMENSIONS[dimension].facts
    return all(facts[fact] is not REFUSED for fact in MEASURES[measure].facts)


def facts_for(measures: Iterable[MeasureKey]) -> set[Fact]:
    return {fact for key in measures for fact in MEASURES[key].facts}


def visible_measures(*, cost: bool, sales: bool, payments: bool) -> list[Measure]:
    """The measures a caller with these families may see, in catalog order (FR-005)."""
    return [
        m
        for m in MEASURES.values()
        if (sales if m.family is Family.SALES else payments) and (cost or not m.sensitive)
    ]
