"""The metrics catalog (spec 037, FR-001, FR-004–FR-006, data-model.md "Catalog"; spec 038,
data-model.md "Catalog additions").

Every number analytics serves is one of these measures, grouped by one of these dimensions. Each is
defined here once; the facts, the engine, the dashboard, the detail and the exports all read it.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.enums import SystemObject
from app.schemas.analytics import DimensionKey, Grain, MeasureKey, MeasureKind


class Fact(StrEnum):
    """What a measure is computed from (data-model.md "Facts"; spec 038 "New facts")."""

    SALE_LINE = 'sale_line'
    REFUND_LINE = 'refund_line'
    CREDIT_NOTE = 'credit_note'
    PAYMENT = 'payment'
    # Spec 038
    STOCK = 'stock'
    STOCK_TODAY = 'stock_today'
    MOVEMENT = 'movement'
    RECEIVABLE = 'receivable'
    CREDIT_FLOW = 'credit_flow'
    PAYABLE = 'payable'
    COMMISSION = 'commission'
    INVOICE_LINE = 'invoice_line'
    INVOICE_COVERAGE = 'invoice_coverage'
    PURCHASE_LINE = 'purchase_line'


#: Facts that give a state at a moment rather than a sum over a period (spec 038, research R1).
#: The engine asks them for the moments it needs; every other fact is a flow.
BALANCE_FACTS = frozenset({Fact.STOCK, Fact.STOCK_TODAY, Fact.RECEIVABLE, Fact.PAYABLE})

#: The column each fact's facility scope applies to (FR-061; spec 038, research R17).
SCOPE_COLUMNS: dict[Fact, str] = {
    Fact.SALE_LINE: 'sales_order.facility',
    Fact.REFUND_LINE: 'customer_refund.facility',
    Fact.CREDIT_NOTE: 'fiscal_document.facility',
    Fact.PAYMENT: 'customer_payment.facility',
    Fact.STOCK: 'warehouse.facility',
    Fact.STOCK_TODAY: 'warehouse.facility',
    Fact.MOVEMENT: 'warehouse.facility',
    Fact.RECEIVABLE: 'sales_order.facility',
    Fact.CREDIT_FLOW: 'sales_order.facility',
    Fact.PAYABLE: 'purchase_order_detail.warehouse.facility',
    Fact.COMMISSION: 'commission_agent.employee.facility',
    Fact.INVOICE_LINE: 'fiscal_document.facility',
    Fact.INVOICE_COVERAGE: 'sales_order.facility',
    Fact.PURCHASE_LINE: 'purchase_order_detail.warehouse.facility',
}


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
    # Spec 038
    INVENTORY = 'inventory'
    RECEIVABLES = 'receivables'
    PAYABLES = 'payables'
    COMMISSIONS = 'commissions'
    FISCAL = 'fiscal'
    PURCHASES = 'purchases'


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
    # Spec 038, research R17: the screens and reports each family replaces.
    Family.INVENTORY: (
        SystemObject.WAREHOUSE_STOCK_REPORT,
        SystemObject.KARDEX,
        SystemObject.STORE_MOVEMENTS_SUMMARY,
    ),
    Family.RECEIVABLES: (SystemObject.ACCOUNTS_RECEIVABLE, SystemObject.CUSTOMER_DEBT_REPORT),
    Family.PAYABLES: (SystemObject.ACCOUNTS_PAYABLE,),
    Family.COMMISSIONS: (SystemObject.COMMISSIONS_BY_SALES_PERSON,),
    Family.FISCAL: (SystemObject.FISCAL_DOCUMENTS, SystemObject.FISCAL_DOCUMENTS_REPORT),
    Family.PURCHASES: (SystemObject.PURCHASES_ORDERS, SystemObject.PRODUCTS_BY_SUPPLIER_REPORT),
}

#: The families measures belong to; any one of them opens the analytics routes. Cost and bulk
#: only widen what another family shows.
MEASURE_FAMILIES = tuple(f for f in Family if f not in (Family.COST, Family.BULK))


@dataclass(frozen=True)
class Measure:
    key: MeasureKey
    label: str
    unit: str  # money | quantity | count | ratio | days
    facts: tuple[Fact, ...]
    family: Family = Family.SALES
    kind: MeasureKind = MeasureKind.FLOW
    sensitive: bool = False
    vat: bool = False  # vat=included changes it
    additive: bool = True
    #: Reads its balance facts at the period's (or each bucket's) start too: turnover, CEI.
    opening: bool = False
    definition: str = ''


@dataclass(frozen=True)
class Dimension:
    key: DimensionKey
    label: str
    facts: dict[Fact, Availability]
    grains: tuple[Grain, ...] = ()


def _measure(key: MeasureKey, label: str, unit: str, *facts: Fact, **options: Any) -> Measure:
    return Measure(key, label, unit, facts, **options)


_SALE, _REFUND, _NOTE, _PAY = Fact.SALE_LINE, Fact.REFUND_LINE, Fact.CREDIT_NOTE, Fact.PAYMENT
_STOCK, _TODAY, _MOVE = Fact.STOCK, Fact.STOCK_TODAY, Fact.MOVEMENT
_RECV, _CREDIT, _PAYABLE = Fact.RECEIVABLE, Fact.CREDIT_FLOW, Fact.PAYABLE
_COMM, _INV, _COVER, _PURCH = (
    Fact.COMMISSION, Fact.INVOICE_LINE, Fact.INVOICE_COVERAGE, Fact.PURCHASE_LINE,
)  # fmt: skip
_BAL = MeasureKind.BALANCE
_INVENTORY, _RECEIVABLES, _PAYABLES = Family.INVENTORY, Family.RECEIVABLES, Family.PAYABLES
_COMMISSIONS, _FISCAL, _PURCHASES = Family.COMMISSIONS, Family.FISCAL, Family.PURCHASES

#: Caveats the definitions repeat (research R4; spec FR-121).
AT_CURRENT_COST = "At current cost: today's list-0 cost without VAT, not the cost on the day."
RECEIVABLE_CAVEAT = (
    '2025 month-ends understate the receivable by up to $2.79M, because legacy orders flagged '
    'paid without a zero-balance time count as closed; exact from 2026-01.'
)

MEASURES: dict[MeasureKey, Measure] = {
    m.key: m
    for m in (
        _measure(
            MeasureKey.GROSS_SALES, 'Gross sales', 'money', _SALE, vat=True,
            definition='Lines of the orders confirmed in the period and not cancelled, after line '
            'discounts.',
        ),
        _measure(
            MeasureKey.DISCOUNTS, 'Discounts', 'money', _SALE, vat=True,
            definition='Line discounts of the orders confirmed in the period.',
        ),
        _measure(
            MeasureKey.REFUNDS, 'Refunds', 'money', _REFUND, vat=True,
            definition="Customer refunds completed in the period, at the refunded lines' amounts.",
        ),
        _measure(
            MeasureKey.CREDIT_NOTES, 'Credit notes', 'money', _NOTE, vat=True,
            definition='Credit notes issued in the period.',
        ),
        _measure(
            MeasureKey.NET_SALES, 'Net sales', 'money', _SALE, _REFUND, _NOTE, vat=True,
            definition='Gross sales − refunds − credit notes.',
        ),
        _measure(
            MeasureKey.COSTED_NET_SALES, 'Costed net sales', 'money', _SALE, _REFUND,
            sensitive=True,
            definition='Net sales without VAT of the lines that have a cost, less their refunds.',
        ),
        _measure(
            MeasureKey.COST_COVERAGE, 'Cost coverage', 'ratio', _SALE, _REFUND, _NOTE,
            sensitive=True, additive=False,
            definition='Costed net sales ÷ net sales without VAT.',
        ),
        _measure(
            MeasureKey.COST_OF_SALES, 'Cost of sales', 'money', _SALE, _REFUND, sensitive=True,
            definition="Quantity × the sale line's cost without VAT, less the refunded lines' "
            'cost.',
        ),
        _measure(
            MeasureKey.GROSS_MARGIN, 'Gross margin', 'money', _SALE, _REFUND, sensitive=True,
            definition='Costed net sales − cost of sales.',
        ),
        _measure(
            MeasureKey.MARGIN_PCT, 'Margin %', 'ratio', _SALE, _REFUND,
            sensitive=True, additive=False,
            definition='Gross margin ÷ costed net sales.',
        ),
        _measure(
            MeasureKey.UNITS_SOLD, 'Units sold', 'quantity', _SALE,
            definition='Quantity of the sale lines.',
        ),
        _measure(
            MeasureKey.UNITS_REFUNDED, 'Units refunded', 'quantity', _REFUND,
            definition='Quantity of the refunded lines.',
        ),
        _measure(
            MeasureKey.UNITS, 'Units', 'quantity', _SALE, _REFUND,
            definition='Units sold − units refunded.',
        ),
        _measure(
            MeasureKey.ORDERS, 'Orders', 'count', _SALE,
            definition='Orders confirmed in the period and not cancelled.',
        ),
        _measure(
            MeasureKey.AVERAGE_TICKET, 'Average ticket', 'money', _SALE, additive=False,
            definition='Gross sales with VAT ÷ orders.',
        ),
        _measure(
            MeasureKey.COLLECTED, 'Collected', 'money', _PAY, family=Family.PAYMENTS,
            definition="Customer payments received in the period: the cash cut's sales tender.",
        ),
        _measure(
            MeasureKey.REFUNDS_PAID_OUT, 'Refunds paid out', 'money', _PAY,
            family=Family.PAYMENTS, definition='Money paid back to customers in the period.',
        ),
        _measure(
            MeasureKey.STORE_CREDIT_ISSUED, 'Store credit issued', 'money', _PAY,
            family=Family.PAYMENTS, definition='Store credit given for refunds in the period.',
        ),
        _measure(
            MeasureKey.PAYMENTS, 'Payments', 'count', _PAY, family=Family.PAYMENTS,
            definition='Customer payments received in the period.',
        ),
        # ── Inventory (spec 038, R2–R5) ──
        _measure(
            MeasureKey.ON_HAND, 'On hand', 'quantity', _STOCK, family=_INVENTORY, kind=_BAL,
            definition='Σ ledger quantity up to the end of the day.',
        ),
        _measure(
            MeasureKey.RESERVED, 'Reserved', 'quantity', _TODAY, family=_INVENTORY, kind=_BAL,
            definition='Reserved by confirmed orders. Today only: null at a past moment.',
        ),
        _measure(
            MeasureKey.AVAILABLE, 'Available', 'quantity', _TODAY, family=_INVENTORY, kind=_BAL,
            definition='On hand − reserved. Today only: null at a past moment.',
        ),
        _measure(
            MeasureKey.IN_TRANSIT, 'In transit', 'quantity', _STOCK, family=_INVENTORY, kind=_BAL,
            definition='On hand in transit warehouses, counted for the destination facility.',
        ),
        _measure(
            MeasureKey.STOCK_VALUE, 'Stock value', 'money', _STOCK,
            family=_INVENTORY, kind=_BAL, sensitive=True,
            definition='On hand × unit cost, over costed pairs. ' + AT_CURRENT_COST,
        ),
        _measure(
            MeasureKey.STOCK_COST_COVERAGE, 'Stock cost coverage', 'ratio', _STOCK,
            family=_INVENTORY, kind=_BAL, sensitive=True, additive=False,
            definition='Costed product × warehouse pairs with stock ÷ pairs with stock. A cost of '
            '0 or at least 99,999 is uncosted.',
        ),
        _measure(
            MeasureKey.UNCOSTED_QUANTITY, 'Uncosted quantity', 'quantity', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='On hand of pairs with stock whose cost is 0 or at least 99,999.',
        ),
        _measure(
            MeasureKey.UNITS_IN, 'Units in', 'quantity', _MOVE, family=_INVENTORY,
            definition='Σ positive ledger quantities in the period.',
        ),
        _measure(
            MeasureKey.UNITS_OUT, 'Units out', 'quantity', _MOVE, family=_INVENTORY,
            definition='−Σ negative ledger quantities in the period.',
        ),
        _measure(
            MeasureKey.MOVEMENT_DOCUMENTS, 'Movement documents', 'count', _MOVE,
            family=_INVENTORY, definition='Distinct documents (source, reference) in the period.',
        ),
        _measure(
            MeasureKey.SALES_VELOCITY, 'Sales velocity', 'quantity', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='Units out by sale, net of refunds, over the velocity window ÷ its days.',
        ),
        _measure(
            MeasureKey.DAYS_OF_COVER, 'Days of cover', 'days', _STOCK,
            family=_INVENTORY, kind=_BAL, additive=False,
            definition='Available (on hand at a past moment) ÷ sales velocity; null without sales.',
        ),
        _measure(
            MeasureKey.STOCK_OUT_PAIRS, 'Stock-outs', 'count', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='Pairs that sold in the velocity window with available ≤ 0.',
        ),
        _measure(
            MeasureKey.BELOW_REORDER_PAIRS, 'Below reorder point', 'count', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='Pairs that sold with available below velocity × (lead + cover days).',
        ),
        _measure(
            MeasureKey.SUGGESTED_QUANTITY, 'Suggested quantity', 'quantity', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='Reorder point − available on pairs below it, rounded up to whole units '
            'unless the product sells fractions.',
        ),
        _measure(
            MeasureKey.DEAD_STOCK_QUANTITY, 'Dead stock', 'quantity', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='On hand of pairs with no ledger entry in the dead-stock window.',
        ),
        _measure(
            MeasureKey.DEAD_STOCK_VALUE, 'Dead stock value', 'money', _STOCK,
            family=_INVENTORY, kind=_BAL, sensitive=True,
            definition='Value of dead stock. ' + AT_CURRENT_COST,
        ),
        _measure(
            MeasureKey.SLOW_STOCK_QUANTITY, 'Slow stock', 'quantity', _STOCK,
            family=_INVENTORY, kind=_BAL,
            definition='On hand of pairs, not dead, with more days of cover than the slow limit.',
        ),
        _measure(
            MeasureKey.SLOW_STOCK_VALUE, 'Slow stock value', 'money', _STOCK,
            family=_INVENTORY, kind=_BAL, sensitive=True,
            definition='Value of slow stock. ' + AT_CURRENT_COST,
        ),
        _measure(
            MeasureKey.TURNOVER, 'Turnover', 'ratio', _STOCK, _SALE, _REFUND,
            family=_INVENTORY, sensitive=True, additive=False, opening=True,
            definition='Cost of sales ÷ the average of stock value at the start and the end. '
            + AT_CURRENT_COST,
        ),
        _measure(
            MeasureKey.DAYS_OF_INVENTORY, 'Days of inventory', 'days', _STOCK, _SALE, _REFUND,
            family=_INVENTORY, sensitive=True, additive=False, opening=True,
            definition='Days in the period ÷ turnover. ' + AT_CURRENT_COST,
        ),
        # ── Receivables and payables (R7–R10) ──
        _measure(
            MeasureKey.RECEIVABLE, 'Receivable', 'money', _RECV, family=_RECEIVABLES, kind=_BAL,
            definition="Open balance of credit orders in base currency (spec 033's rule). "
            + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.RECEIVABLE_CURRENT, 'Receivable current', 'money', _RECV,
            family=_RECEIVABLES, kind=_BAL,
            definition='Receivable not yet past due. ' + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.RECEIVABLE_OVERDUE, 'Receivable overdue', 'money', _RECV,
            family=_RECEIVABLES, kind=_BAL,
            definition='Receivable past its due date. ' + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.OPEN_ORDERS, 'Open credit orders', 'count', _RECV,
            family=_RECEIVABLES, kind=_BAL,
            definition='Credit orders with an open balance. ' + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.CREDIT_SALES, 'Credit sales', 'money', _CREDIT, family=_RECEIVABLES,
            definition='Gross sales with VAT of credit-terms orders confirmed in the period.',
        ),
        _measure(
            MeasureKey.COLLECTED_ON_CREDIT, 'Collected on credit', 'money', _CREDIT,
            family=_RECEIVABLES,
            definition='Live applications to credit orders dated in the period.',
        ),
        _measure(
            MeasureKey.DUE_IN_PERIOD, 'Due in period', 'money', _CREDIT, family=_RECEIVABLES,
            definition="Balance at the period's start of the orders due in the period.",
        ),
        _measure(
            MeasureKey.DUE_COLLECTED, 'Due collected', 'money', _CREDIT, family=_RECEIVABLES,
            definition='Applications dated in the period to the orders due in it.',
        ),
        _measure(
            MeasureKey.DSO, 'DSO', 'days', _RECV, _CREDIT, family=_RECEIVABLES, additive=False,
            definition='Receivable at the end ÷ credit sales × days in the period. Needs a period '
            'from a 1st to a month-end or today. ' + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.CEI, 'Collection effectiveness', 'ratio', _RECV, _CREDIT,
            family=_RECEIVABLES, additive=False, opening=True,
            definition='(Receivable at the start + credit sales − receivable at the end) ÷ '
            '(receivable at the start + credit sales − current receivable at the end). Needs a '
            'period from a 1st to a month-end or today. ' + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.CREDIT_LIMIT, 'Credit limit', 'money', _RECV,
            family=_RECEIVABLES, kind=_BAL,
            definition="The customers' credit limits, as stored with each month-end.",
        ),
        _measure(
            MeasureKey.CREDIT_UTILIZATION, 'Credit utilization', 'ratio', _RECV,
            family=_RECEIVABLES, kind=_BAL, additive=False,
            definition='Receivable ÷ credit limit, over customers with a limit; null without one. '
            + RECEIVABLE_CAVEAT,
        ),
        _measure(
            MeasureKey.PAYABLE, 'Payable', 'money', _PAYABLE, family=_PAYABLES, kind=_BAL,
            definition='Open balance of purchase orders from the supplier cutoff (spec 033). '
            'Today only; zero until the cutoff is set.',
        ),
        _measure(
            MeasureKey.PAYABLE_CURRENT, 'Payable current', 'money', _PAYABLE,
            family=_PAYABLES, kind=_BAL, definition='Payable not yet past due. Today only.',
        ),
        _measure(
            MeasureKey.PAYABLE_OVERDUE, 'Payable overdue', 'money', _PAYABLE,
            family=_PAYABLES, kind=_BAL, definition='Payable past its due date. Today only.',
        ),
        _measure(
            MeasureKey.OPEN_PURCHASE_ORDERS, 'Open purchase orders', 'count', _PAYABLE,
            family=_PAYABLES, kind=_BAL,
            definition='Purchase orders with an open balance. Today only.',
        ),
        # ── Commissions (R11) ──
        _measure(
            MeasureKey.COMMISSION, 'Commission', 'money', _COMM, family=_COMMISSIONS,
            definition='Σ spec 025 commission line amounts; provisional for open months.',
        ),
        _measure(
            MeasureKey.COMMISSION_BASE, 'Commission base', 'money', _COMM, family=_COMMISSIONS,
            definition='Σ line price after discount, with VAT, × quantity.',
        ),
        _measure(
            MeasureKey.COMMISSION_LINES, 'Commission lines', 'count', _COMM,
            family=_COMMISSIONS, definition='Spec 025 commission lines.',
        ),
        # ── Fiscal (R12) ──
        _measure(
            MeasureKey.INVOICED, 'Invoiced', 'money', _INV, family=_FISCAL, vat=True,
            definition='Invoice lines issued in the period; stamped by default.',
        ),
        _measure(
            MeasureKey.INVOICES, 'Invoices', 'count', _INV, family=_FISCAL,
            definition='Distinct invoices issued in the period.',
        ),
        _measure(
            MeasureKey.STANDALONE_INVOICED, 'Standalone invoiced', 'money', _INV,
            family=_FISCAL, vat=True, definition='Invoice lines with no sale line.',
        ),
        _measure(
            MeasureKey.PAYMENT_RECEIPTS, 'Payment receipts', 'money', _INV, family=_FISCAL,
            definition="Payment receipts' payment amount. Listed, never added to VAT.",
        ),
        _measure(
            MeasureKey.OUTPUT_VAT, 'Output VAT', 'money', _INV, family=_FISCAL,
            definition='Invoice VAT − credit note VAT, stamped and not cancelled.',
        ),
        _measure(
            MeasureKey.INVOICED_SALES, 'Invoiced sales', 'money', _COVER,
            family=_FISCAL, vat=True,
            definition="Sales of the period covered by a live stamped invoice, at the line's "
            'amount × invoiced ÷ sold quantity.',
        ),
        _measure(
            MeasureKey.UNINVOICED_SALES, 'Uninvoiced sales', 'money', _COVER,
            family=_FISCAL, vat=True, definition='Sales of the period − invoiced sales.',
        ),
        _measure(
            MeasureKey.INVOICE_COVERAGE, 'Invoice coverage', 'ratio', _COVER,
            family=_FISCAL, additive=False,
            definition='Invoiced sales ÷ (invoiced + uninvoiced sales).',
        ),
        _measure(
            MeasureKey.INVOICING_LAG, 'Invoicing lag', 'days', _COVER,
            family=_FISCAL, additive=False,
            definition='Days from the sale to its first invoice, weighted by invoiced amount.',
        ),
        # ── Purchases (R13) ──
        _measure(
            MeasureKey.PURCHASES, 'Purchases', 'money', _PURCH, family=_PURCHASES,
            definition='Σ quantity × price × (1 − discount) of completed purchase order lines, '
            'before tax.',
        ),
        _measure(
            MeasureKey.PURCHASE_QUANTITY, 'Purchase quantity', 'quantity', _PURCH,
            family=_PURCHASES, definition='Quantity of completed purchase order lines.',
        ),
        _measure(
            MeasureKey.RECEIVED_QUANTITY, 'Received quantity', 'quantity', _PURCH,
            family=_PURCHASES, definition='Quantity from confirmed receipts of those lines.',
        ),
        _measure(
            MeasureKey.PURCHASE_ORDERS, 'Purchase orders', 'count', _PURCH, family=_PURCHASES,
            definition='Distinct completed purchase orders.',
        ),
        _measure(
            MeasureKey.AVERAGE_PURCHASE_PRICE, 'Average purchase price', 'money', _PURCH,
            family=_PURCHASES, additive=False, definition='Purchases ÷ purchase quantity.',
        ),
        _measure(
            MeasureKey.AVERAGE_SALE_PRICE, 'Average sale price', 'money', _SALE,
            family=_PURCHASES, additive=False,
            definition='Gross sales without VAT ÷ units sold.',
        ),
        _measure(
            MeasureKey.PURCHASE_SALE_SPREAD, 'Purchase-sale spread', 'money', _PURCH, _SALE,
            family=_PURCHASES, sensitive=True, additive=False,
            definition='Average sale price − average purchase price.',
        ),
        _measure(
            MeasureKey.PURCHASE_SALE_SPREAD_PCT, 'Purchase-sale spread %', 'ratio', _PURCH, _SALE,
            family=_PURCHASES, sensitive=True, additive=False,
            definition='Purchase-sale spread ÷ average sale price.',
        ),
    )
}  # fmt: skip


#: The stock measures read pair by pair with `StockOptions` (research R5): the stock fact computes
#: them only when one is asked, or when the request names its options.
STOCK_OPTION_MEASURES = frozenset(
    {
        MeasureKey.SALES_VELOCITY, MeasureKey.DAYS_OF_COVER, MeasureKey.STOCK_OUT_PAIRS,
        MeasureKey.BELOW_REORDER_PAIRS, MeasureKey.SUGGESTED_QUANTITY,
        MeasureKey.DEAD_STOCK_QUANTITY, MeasureKey.DEAD_STOCK_VALUE,
        MeasureKey.SLOW_STOCK_QUANTITY, MeasureKey.SLOW_STOCK_VALUE,
    }
)  # fmt: skip

#: The facts spec 038 adds. A dimension applies to those it names; it refuses the rest.
NEW_FACTS = (
    _STOCK, _TODAY, _MOVE, _RECV, _CREDIT, _PAYABLE, _COMM, _INV, _COVER, _PURCH,
)  # fmt: skip


def _dimension(
    key: DimensionKey,
    label: str,
    sale: Availability,
    refund: Availability,
    note: Availability,
    payment: Availability,
    grains: tuple[Grain, ...] = (),
    also: Iterable[Fact] = (),
) -> Dimension:
    """Spec 037's four facts, given one by one; of spec 038's, the ones in `also` have it."""
    facts = {_SALE: sale, _REFUND: refund, _NOTE: note, _PAY: payment}
    facts |= {fact: COLUMN if fact in also else REFUSED for fact in NEW_FACTS}
    return Dimension(key, label, facts, grains)


_PRODUCT_FACTS = (_STOCK, _TODAY, _MOVE, _INV, _COVER, _PURCH)
_STOCK_FACTS = (_STOCK, _TODAY, _MOVE)

#: Which fact provides which dimension (data-model.md "Dimension"; spec 038 "New dimensions").
DIMENSIONS: dict[DimensionKey, Dimension] = {
    d.key: d
    for d in (
        _dimension(
            DimensionKey.DATE, 'Date', COLUMN, COLUMN, COLUMN, COLUMN, tuple(Grain), NEW_FACTS
        ),
        _dimension(DimensionKey.WEEKDAY, 'Weekday', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(DimensionKey.HOUR, 'Hour', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(
            DimensionKey.FACILITY, 'Facility', COLUMN, COLUMN, COLUMN, COLUMN, also=NEW_FACTS
        ),
        _dimension(DimensionKey.POINT_OF_SALE, 'Point of sale', COLUMN, COLUMN, NONE, REFUSED),
        _dimension(
            DimensionKey.SALESPERSON, 'Salesperson', COLUMN, COLUMN, NONE, COLUMN,
            also=(_RECV, _CREDIT, _COMM, _COVER),
        ),
        _dimension(DimensionKey.CASH_SESSION, 'Cash session', REFUSED, REFUSED, REFUSED, COLUMN),
        _dimension(
            DimensionKey.CUSTOMER, 'Customer', COLUMN, COLUMN, COLUMN, COLUMN,
            also=(_RECV, _CREDIT, _COMM, _INV, _COVER),
        ),
        _dimension(DimensionKey.CUSTOMER_TYPE, 'Customer type', COLUMN, COLUMN, COLUMN, COLUMN),
        _dimension(
            DimensionKey.PRODUCT, 'Product', COLUMN, COLUMN, NONE, REFUSED,
            also=(*_PRODUCT_FACTS, _COMM),
        ),
        _dimension(DimensionKey.BRAND, 'Brand', COLUMN, COLUMN, NONE, REFUSED, also=_PRODUCT_FACTS),
        _dimension(DimensionKey.MODEL, 'Model', COLUMN, COLUMN, NONE, REFUSED, also=_PRODUCT_FACTS),
        _dimension(
            DimensionKey.LABEL, 'Label', COLUMN, COLUMN, NONE, REFUSED,
            also=(*_PRODUCT_FACTS, _COMM),
        ),
        _dimension(
            DimensionKey.PAYMENT_TERMS, 'Payment terms', COLUMN, COLUMN, NONE, REFUSED,
            also=(_RECV, _CREDIT),
        ),
        _dimension(
            DimensionKey.PAYMENT_METHOD, 'Payment method', REFUSED, REFUSED, REFUSED, COLUMN
        ),
        # Spec 038
        _dimension(
            DimensionKey.WAREHOUSE, 'Warehouse', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(*_STOCK_FACTS, _PURCH),
        ),
        _dimension(
            DimensionKey.MOVEMENT_TYPE, 'Movement type', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_MOVE,),
        ),
        _dimension(
            DimensionKey.SUPPLIER, 'Supplier', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(*_STOCK_FACTS, _PURCH, _PAYABLE),
        ),
        _dimension(
            DimensionKey.AGING_BUCKET, 'Aging bucket', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_RECV, _PAYABLE),
        ),
        _dimension(
            DimensionKey.DUE_BUCKET, 'Due bucket', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_RECV, _PAYABLE),
        ),
        _dimension(
            DimensionKey.SALES_AGENT, 'Sales agent', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_RECV, _CREDIT),
        ),
        _dimension(
            DimensionKey.COMMISSION_PARTICIPATION, 'Commission participation',
            REFUSED, REFUSED, REFUSED, REFUSED, also=(_COMM,),
        ),
        _dimension(
            DimensionKey.COMMISSION_STATUS, 'Commission status', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_COMM,),
        ),
        _dimension(
            DimensionKey.ISSUER, 'Issuer', REFUSED, REFUSED, REFUSED, REFUSED, also=(_INV,)
        ),
        _dimension(
            DimensionKey.DOCUMENT_STATUS, 'Document status', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_INV,),
        ),
        _dimension(
            DimensionKey.DOCUMENT_TYPE, 'Document type', REFUSED, REFUSED, REFUSED, REFUSED,
            also=(_INV,),
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


def visible_measures(held: Iterable[Family]) -> list[Measure]:
    """The measures a caller holding these families may see, in catalog order (FR-005)."""
    held = set(held)
    return [
        m
        for m in MEASURES.values()
        if m.family in held and (Family.COST in held or not m.sensitive)
    ]
