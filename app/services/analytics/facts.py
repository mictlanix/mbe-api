"""The four fact queries (spec 037, data-model.md "Facts", research R3–R6).

Each fact is one grouped `SELECT` of integer cents. The line facts first price every line of the
period's documents with the line rule of `app.services.totals`, rounded to cents, and add each
document's rounding residual to its largest line. Summing those cents over any grouping therefore
gives exactly the sum of the document totals the sales screens show (FR-003, FR-012).

The residual needs every line of a document, so the line subqueries filter only on the document
(status, period, facility scope). Dimension filters apply in the outer query, after the residual
is placed. `detail.py` reads the same subqueries, so a drill-down's amounts are these amounts.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import (
    Select,
    and_,
    case,
    false,
    func,
    literal,
    literal_column,
    null,
    or_,
    select,
    true,
    union_all,
)
from sqlalchemy.sql.expression import ColumnElement, Subquery

from app.core.config import settings
from app.enums import FiscalDocumentType, PaymentTerms, PaymentType
from app.models.analytics import ReceivableSnapshot
from app.models.core import CashSession, Warehouse
from app.models.customer import Customer
from app.models.fiscal import FiscalDocument, FiscalDocumentDetail
from app.models.inventory import InventoryReceipt, InventoryReceiptDetail, LotSerialTracking
from app.models.product import Product, product_label
from app.models.purchases import PurchaseOrder, PurchaseOrderDetail
from app.models.sales import (
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
)
from app.schemas.analytics import DimensionKey, Grain, StockOptions
from app.services.analytics.catalog import DIMENSIONS, Availability, Fact
from app.services.analytics.sql import (
    cents,
    days_between,
    hour_of,
    period_bounds,
    period_start,
    weekday,
)
from app.services.sales_order_service import application_date

#: Customer type and payment terms values, as keys and filter values.
CREDIT, CASH, IMMEDIATE = 'credit', 'cash', 'immediate'
#: Payment classes (R6): the cash cut's sales tender, its money refunds and its store credit.
COLLECTED, PAID_OUT, STORE_CREDIT = 'collected', 'paid_out', 'store_credit'
#: Fiscal document types and statuses (spec 038, R12), as keys and filter values.
INVOICE, CREDIT_NOTE, PAYMENT_RECEIPT = 'invoice', 'credit_note', 'payment_receipt'
STAMPED, CANCELLED, PENDING = 'stamped', 'cancelled', 'pending'


@dataclass(frozen=True)
class Slice:
    """What one fact query covers: grouping, filters, period and facility scope.

    `filters` hold parsed values: ints for entity ids, weekday and hour; dates for `date` (period
    starts at `grain`); lowercase trimmed text for brand and model; the class strings otherwise.

    `moments` is set only for a balance fact (spec 038, research R1): the days at whose end it
    gives its values. Its slice then has neither a date dimension nor a date filter; the engine
    places each moment's values in their date bucket.

    `stock_options` is set only for the stock fact, when a measure reads velocity, cover, dead or
    slow stock (research R5); without it the fact skips them.

    `stamped_only` is set only for the invoice fact, when the request neither groups nor filters by
    document status: its measures then count stamped documents only (spec 038, R12).
    """

    dimensions: tuple[DimensionKey, ...] = ()
    grain: Grain = Grain.DAY
    filters: Mapping[DimensionKey, Sequence[Any]] = field(default_factory=dict)
    start: date = date.min
    end: date = date.max
    facility: int | None = None
    moments: tuple[date, ...] = ()
    stock_options: StockOptions | None = None
    stamped_only: bool = False


# ── Line pricing (mirrors totals.line_amounts) ────────────────────────────────


def _priced(quantity, price, discount_rate, tax_rate, tax_included):
    """`(subtotal, tax, discount subtotal, discount tax)` of a line, unrounded, as SQL."""
    gross = quantity * price * (1 - discount_rate)
    subtotal = case((tax_included, gross / (1 + tax_rate)), else_=gross)
    tax = case((tax_included, gross - subtotal), else_=gross * tax_rate)
    discount = quantity * price * discount_rate
    discount_subtotal = case((tax_included, discount / (1 + tax_rate)), else_=discount)
    discount_tax = case((tax_included, discount - discount_subtotal), else_=discount * tax_rate)
    return subtotal, tax, discount_subtotal, discount_tax


def _with_residual(amount, document, line_id, order_by):
    """The line's cents plus, on the document's largest line, the document's rounding residual.

    The document rounds once, over the sum of its unrounded lines (`totals.document_totals`); the
    lines round one by one. Their difference lands on the line with the largest amount, the lowest
    id on ties, so the lines of a document always sum to its total.
    """
    window = {'partition_by': document}
    rank = func.row_number().over(partition_by=document, order_by=(order_by.desc(), line_id))
    residual = cents(func.sum(amount).over(**window)) - func.sum(cents(amount)).over(**window)
    return cents(amount) + case((rank == 1, residual), else_=0)


def _cost(quantity, cost):
    """Cost without VAT: the cost snapshot is recorded without VAT (spec 031, research R4)."""
    return quantity * cost


def _net_unit_price(price, discount_rate, tax_rate, tax_included):
    """The unit price after discount and without VAT, on the same basis as the cost."""
    net = price * (1 - discount_rate)
    return case((tax_included, net / (1 + tax_rate)), else_=net)


def sale_lines(start: date, end: date, facility: int | None) -> Subquery:
    """Every line of the sales confirmed in the period: confirmed and not cancelled (FR-002)."""
    line, order = SalesOrderDetail, SalesOrder
    subtotal, tax, discount_subtotal, discount_tax = _priced(
        line.quantity, line.price, line.discount_rate, line.tax_rate, line.tax_included
    )
    costed = line.cost > 0
    since, until = period_bounds(start, end)
    query = (
        select(
            line.sales_order_detail_id.label('id'),
            line.sales_order.label('doc'),
            line.product.label('product'),
            line.quantity.label('qty'),
            order.facility.label('facility'),
            order.point_sale.label('point_sale'),
            order.salesperson.label('salesperson'),
            order.customer.label('customer'),
            order.payment_terms.label('payment_terms'),
            order.confirmation_time.label('at'),
            _with_residual(subtotal, line.sales_order, line.sales_order_detail_id, subtotal).label(
                'sub_c'
            ),
            _with_residual(tax, line.sales_order, line.sales_order_detail_id, subtotal).label(
                'tax_c'
            ),
            cents(discount_subtotal).label('disc_sub_c'),
            cents(discount_tax).label('disc_tax_c'),
            case(
                (costed, cents(_cost(line.quantity, line.cost))),
                else_=0,
            ).label('cost_c'),
            case((costed, 1), else_=0).label('costed'),
            case(
                (
                    and_(
                        costed,
                        _net_unit_price(
                            line.price, line.discount_rate, line.tax_rate, line.tax_included
                        )
                        < line.cost,
                    ),
                    1,
                ),
                else_=0,
            ).label('below'),
        )
        .join(order, order.sales_order_id == line.sales_order)
        .where(
            order.completed.is_(True),
            order.cancelled.is_(False),
            order.confirmation_time >= since,
            order.confirmation_time < until,
        )
    )
    if facility is not None:
        query = query.where(order.facility == facility)
    return query.subquery('sale_lines')


def refund_lines(start: date, end: date, facility: int | None) -> Subquery:
    """Every line of the refunds confirmed in the period, dated and placed by the refund (R5).

    Cost comes from the sale line returned, scaled by the refunded quantity. The point of sale and
    payment terms are the refunded order's; the salesperson is the refund's.
    """
    line, refund, sold, order = CustomerRefundDetail, CustomerRefund, SalesOrderDetail, SalesOrder
    subtotal, tax, _, _ = _priced(
        line.quantity, line.price, line.discount, line.tax_rate, line.tax_included
    )
    costed = sold.cost > 0
    since, until = period_bounds(start, end)
    query = (
        select(
            line.customer_refund_detail_id.label('id'),
            line.customer_refund.label('doc'),
            line.product.label('product'),
            line.quantity.label('qty'),
            refund.facility.label('facility'),
            order.point_sale.label('point_sale'),
            refund.sales_person.label('salesperson'),
            func.coalesce(refund.customer, order.customer).label('customer'),
            order.payment_terms.label('payment_terms'),
            refund.date.label('at'),
            refund.sales_order.label('sales_order'),
            _with_residual(
                subtotal, line.customer_refund, line.customer_refund_detail_id, subtotal
            ).label('sub_c'),
            _with_residual(
                tax, line.customer_refund, line.customer_refund_detail_id, subtotal
            ).label('tax_c'),
            case(
                (costed, cents(_cost(line.quantity, sold.cost))),
                else_=0,
            ).label('cost_c'),
            case((costed, 1), else_=0).label('costed'),
        )
        .join(refund, refund.customer_refund_id == line.customer_refund)
        .join(sold, sold.sales_order_detail_id == line.sales_order_detail)
        .join(order, order.sales_order_id == refund.sales_order)
        .where(
            refund.completed.is_(True),
            refund.cancelled.is_(False),
            refund.date >= since,
            refund.date < until,
        )
    )
    if facility is not None:
        query = query.where(refund.facility == facility)
    return query.subquery('refund_lines')


def credit_note_lines(start: date, end: date, facility: int | None) -> Subquery:
    """Every line of the stamped, live CFDI credit notes issued in the period (R5)."""
    line, document = FiscalDocumentDetail, FiscalDocument
    subtotal, tax, _, _ = _priced(
        line.quantity, line.price, line.discount, line.tax_rate, line.tax_included
    )
    since, until = period_bounds(start, end)
    query = (
        select(
            line.fiscal_document_detail_id.label('id'),
            line.document.label('doc'),
            document.facility.label('facility'),
            document.customer.label('customer'),
            document.issued.label('at'),
            _with_residual(subtotal, line.document, line.fiscal_document_detail_id, subtotal).label(
                'sub_c'
            ),
            _with_residual(tax, line.document, line.fiscal_document_detail_id, subtotal).label(
                'tax_c'
            ),
        )
        .join(document, document.fiscal_document_id == line.document)
        .where(
            document.type == int(FiscalDocumentType.CREDIT_NOTE),
            document.stamped.is_not(None),
            document.cancelled.is_(False),
            document.issued >= since,
            document.issued < until,
        )
    )
    if facility is not None:
        query = query.where(document.facility == facility)
    return query.subquery('credit_note_lines')


def payment_rows(start: date, end: date, facility: int | None) -> Subquery:
    """Every customer payment of the period, classified as the cash cut does (R6, `cut_figures`).

    A credit-note payment or any negative one is a refund: negative ones are money paid out, and
    positive credit-note payments are store credit issued. Any other positive payment, of any type
    including 0, is collected tender, net of the change handed back on its live applications.
    """
    payment = CustomerPayment
    since, until = period_bounds(start, end)
    # Only the period's payments: without the date the subquery groups every application ever made.
    applied = CustomerPayment.__table__.alias('applied')
    change = (
        select(
            SalesOrderPayment.customer_payment.label('payment'),
            func.sum(SalesOrderPayment.amount_change).label('change'),
        )
        .join(applied, applied.c.customer_payment_id == SalesOrderPayment.customer_payment)
        .where(
            SalesOrderPayment.cancelled.is_(False),
            applied.c.date >= since,
            applied.c.date < until,
        )
        .group_by(SalesOrderPayment.customer_payment)
        .subquery('payment_change')
    )
    store_credit = and_(payment.payment_type == int(PaymentType.CREDIT_NOTE), payment.amount > 0)
    collected = and_(payment.payment_type != int(PaymentType.CREDIT_NOTE), payment.amount > 0)
    paid_out = payment.amount < 0
    query = (
        select(
            payment.customer_payment_id.label('id'),
            payment.customer_payment_id.label('doc'),
            payment.facility.label('facility'),
            payment.customer.label('customer'),
            payment.cash_session.label('cash_session'),
            payment.method.label('payment_method'),
            CashSession.cashier.label('salesperson'),
            payment.date.label('at'),
            case(
                (collected, cents(payment.amount - func.coalesce(change.c.change, 0))), else_=0
            ).label('collected_c'),
            case((paid_out, cents(-payment.amount)), else_=0).label('paid_out_c'),
            case((store_credit, cents(payment.amount)), else_=0).label('store_c'),
            case(
                (collected, literal(COLLECTED)),
                (paid_out, literal(PAID_OUT)),
                (store_credit, literal(STORE_CREDIT)),
                else_=null(),
            ).label('class'),
        )
        .outerjoin(change, change.c.payment == payment.customer_payment_id)
        .outerjoin(CashSession, CashSession.cash_session_id == payment.cash_session)
        .where(payment.date >= since, payment.date < until)
    )
    if facility is not None:
        query = query.where(payment.facility == facility)
    return query.subquery('payments')


#: A movement's document as one number, so `COUNT(DISTINCT …)` works on both dialects: the
#: ledger type in the high digits, the reference below (references stay under 10^10).
DOCUMENT_BASE = 10_000_000_000


def movement_rows(start: date, end: date, facility: int | None) -> Subquery:
    """Every ledger entry of the period (spec 038, research R2), placed by its warehouse.

    The facility is the warehouse's, so stock in a transit warehouse counts for the facility it is
    travelling to (spec 030, `split_transit`). The supplier is the product's.
    """
    entry = LotSerialTracking
    since, until = period_bounds(start, end)
    query = (
        select(
            entry.lot_serial_tracking_id.label('id'),
            entry.date.label('at'),
            entry.product.label('product'),
            entry.warehouse.label('warehouse'),
            Warehouse.facility.label('facility'),
            entry.source.label('movement_type'),
            entry.reference.label('reference'),
            Product.supplier.label('supplier'),
            entry.quantity.label('qty'),
            case((entry.quantity > 0, entry.quantity), else_=0).label('units_in'),
            case((entry.quantity < 0, -entry.quantity), else_=0).label('units_out'),
            (entry.source * DOCUMENT_BASE + entry.reference).label('doc'),
        )
        .join(Warehouse, Warehouse.warehouse_id == entry.warehouse)
        .join(Product, Product.product_id == entry.product)
        .where(entry.date >= since, entry.date < until)
    )
    if facility is not None:
        query = query.where(Warehouse.facility == facility)
    return query.subquery('movements')


def month_end_on_or_before(day: date) -> date:
    """`day` when it ends a month, else the end of the month before: the stored receivable
    month-end a moment reads (spec 038, research R8)."""
    return day if (day + timedelta(days=1)).day == 1 else day.replace(day=1) - timedelta(days=1)


def credit_flow_rows(start: date, end: date, facility: int | None) -> Subquery:
    """The collection flows of credit orders in the period (spec 038, research R9), by the order's
    facility, one row source of three parts:

    - credit sales: spec 037's sale lines of credit-terms orders, gross with VAT;
    - collected on credit: live applications to credit-terms orders by application date (R7), in
      base currency; those to an order the start balance holds as due in the period are also due
      collected;
    - due in the period: the stored balance at the period's start (the month-end on or before the
      day before it) of the orders due in the period, in base currency, by due date.
    """
    since, until = period_bounds(start, end)
    credit = int(PaymentTerms.NET_D)
    zero = literal(0)
    opening = month_end_on_or_before(start - timedelta(days=1))
    snapshot = ReceivableSnapshot
    due_here = and_(snapshot.due_date >= since, snapshot.due_date < until)

    lines = sale_lines(start, end, facility)
    sales = select(
        lines.c.doc.label('doc'), lines.c.at.label('at'), lines.c.facility.label('facility'),
        lines.c.customer.label('customer'), lines.c.salesperson.label('salesperson'),
        lines.c.payment_terms.label('payment_terms'),
        (lines.c.sub_c + lines.c.tax_c).label('credit_sales_c'), zero.label('collected_c'),
        zero.label('due_c'), zero.label('due_collected_c'),
    ).where(lines.c.payment_terms == credit)  # fmt: skip

    order, applied_at = SalesOrder, application_date()
    collected = cents(SalesOrderPayment.amount * order.exchange_rate)
    applications = (
        select(
            order.sales_order_id.label('doc'), applied_at.label('at'),
            order.facility.label('facility'), order.customer.label('customer'),
            order.salesperson.label('salesperson'), order.payment_terms.label('payment_terms'),
            zero.label('credit_sales_c'), collected.label('collected_c'), zero.label('due_c'),
            case((snapshot.sales_order.is_not(None), collected), else_=0).label('due_collected_c'),
        )
        .join(order, order.sales_order_id == SalesOrderPayment.sales_order)
        .join(
            CustomerPayment,
            CustomerPayment.customer_payment_id == SalesOrderPayment.customer_payment,
        )
        .outerjoin(
            snapshot,
            and_(
                snapshot.sales_order == order.sales_order_id,
                snapshot.month_end == opening,
                due_here,
            ),
        )
        .where(
            SalesOrderPayment.cancelled.is_(False),
            order.payment_terms == credit,
            applied_at >= since,
            applied_at < until,
        )
    )  # fmt: skip

    due = select(
        snapshot.sales_order.label('doc'), snapshot.due_date.label('at'),
        snapshot.facility.label('facility'), snapshot.customer.label('customer'),
        snapshot.salesperson.label('salesperson'), literal(credit).label('payment_terms'),
        zero.label('credit_sales_c'), zero.label('collected_c'),
        cents(snapshot.balance * snapshot.exchange_rate).label('due_c'),
        zero.label('due_collected_c'),
    ).where(snapshot.month_end == opening, due_here)  # fmt: skip

    if facility is not None:
        applications = applications.where(order.facility == facility)
        due = due.where(snapshot.facility == facility)
    return union_all(sales, applications, due).subquery('credit_flow')


def _document_status(document: type[FiscalDocument]) -> ColumnElement:
    """R12: stamped and live; cancelled after stamping; pending (issued, neither stamped nor
    cancelled). The rows exclude drafts cancelled before stamping."""
    return case(
        (and_(document.stamped.is_not(None), document.cancelled.is_(False)), literal(STAMPED)),
        (document.stamped.is_not(None), literal(CANCELLED)),
        else_=literal(PENDING),
    )


def invoice_lines(start: date, end: date, facility: int | None) -> Subquery:
    """The fiscal documents issued in the period (spec 038, R12), by the issuing facility: every
    line of the invoices and credit notes, priced with the line rule as `credit_note_lines` does,
    and one row per payment receipt carrying its payment amount (a receipt's lines are worth
    nothing). Drafts cancelled before they were ever stamped are left out."""
    line, document = FiscalDocumentDetail, FiscalDocument
    subtotal, tax, _, _ = _priced(
        line.quantity, line.price, line.discount, line.tax_rate, line.tax_included
    )
    since, until = period_bounds(start, end)
    issued = [
        document.issued >= since,
        document.issued < until,
        or_(document.stamped.is_not(None), document.cancelled.is_(False)),
    ]
    if facility is not None:
        issued.append(document.facility == facility)
    status = _document_status(document).label('document_status')
    zero = literal(0)
    lines = (
        select(
            line.fiscal_document_detail_id.label('id'), line.document.label('doc'),
            case(
                (document.type == int(FiscalDocumentType.INVOICE), literal(INVOICE)),
                else_=literal(CREDIT_NOTE),
            ).label('document_type'),
            status, document.issuer.label('issuer'), document.facility.label('facility'),
            document.customer.label('customer'), line.product.label('product'),
            document.issued.label('at'),
            _with_residual(subtotal, line.document, line.fiscal_document_detail_id, subtotal).label(
                'sub_c'
            ),
            _with_residual(tax, line.document, line.fiscal_document_detail_id, subtotal).label(
                'tax_c'
            ),
            case((line.order_detail.is_(None), 1), else_=0).label('standalone'),
            zero.label('receipt_c'),
        )
        .join(document, document.fiscal_document_id == line.document)
        .where(
            document.type.in_(
                [int(FiscalDocumentType.INVOICE), int(FiscalDocumentType.CREDIT_NOTE)]
            ),
            *issued,
        )
    )  # fmt: skip
    receipts = select(
        document.fiscal_document_id.label('id'), document.fiscal_document_id.label('doc'),
        literal(PAYMENT_RECEIPT).label('document_type'), status,
        document.issuer.label('issuer'), document.facility.label('facility'),
        document.customer.label('customer'), null().label('product'),
        document.issued.label('at'), zero.label('sub_c'), zero.label('tax_c'),
        zero.label('standalone'),
        cents(func.coalesce(document.payment_amount, 0)).label('receipt_c'),
    ).where(document.type == int(FiscalDocumentType.PAYMENT_RECEIPT), *issued)  # fmt: skip
    return union_all(lines, receipts).subquery('invoice_lines')


def coverage_lines(start: date, end: date, facility: int | None) -> Subquery:
    """Spec 037's sale lines of the period, each with what live stamped invoices invoiced on it,
    whenever they were issued (spec 038, R12).

    The invoiced quantity is capped at the line's, and valued at the sale line's own cents ×
    invoiced ÷ sold, rounded, so a line's invoiced and not invoiced cents add up to its sale cents.
    `lag_w` is the days from the sale to the line's first invoice × its invoiced cents without VAT:
    summed and divided by those cents, it is the lag weighted by invoiced amount.
    """
    sold = sale_lines(start, end, facility)
    line, document = FiscalDocumentDetail, FiscalDocument
    since, until = period_bounds(start, end)
    # Only the invoice lines of the period's sales, so the grouping never reads the whole history.
    invoiced = (
        select(
            line.order_detail.label('line'),
            func.sum(line.quantity).label('qty'),
            func.min(document.issued).label('first'),
        )
        .join(document, document.fiscal_document_id == line.document)
        .join(SalesOrderDetail, SalesOrderDetail.sales_order_detail_id == line.order_detail)
        .join(SalesOrder, SalesOrder.sales_order_id == SalesOrderDetail.sales_order)
        .where(
            document.type == int(FiscalDocumentType.INVOICE),
            document.stamped.is_not(None),
            document.cancelled.is_(False),
            SalesOrder.confirmation_time >= since,
            SalesOrder.confirmation_time < until,
        )
        .group_by(line.order_detail)
        .subquery('invoiced')
    )
    s = sold.c
    asked = func.coalesce(invoiced.c.qty, 0)
    capped = case((s.qty <= 0, 0), (asked > s.qty, s.qty), (asked < 0, 0), else_=asked)

    def share(amount: ColumnElement) -> ColumnElement:
        # Cents × invoiced × 0.01 ÷ sold: pesos, rounded once to cents. The decimal literal keeps
        # SQLite off integer division and MariaDB on exact DECIMALs.
        return case((s.qty > 0, cents(amount * capped * literal_column('0.01') / s.qty)), else_=0)

    invoiced_sub = share(s.sub_c)
    lag = case(
        (invoiced.c.first.is_(None), 0), else_=days_between(s.at, invoiced.c.first) * invoiced_sub
    )
    return (
        select(
            s.id, s.doc, s.product, s.qty, s.facility, s.salesperson, s.customer, s.at,
            s.sub_c, s.tax_c, capped.label('inv_qty'), invoiced_sub.label('inv_sub_c'),
            share(s.tax_c).label('inv_tax_c'), invoiced.c.first.label('first_invoiced'),
            lag.label('lag_w'),
        )
        .select_from(sold)
        .outerjoin(invoiced, invoiced.c.line == s.id)
        .subquery('invoice_coverage')
    )  # fmt: skip


def purchase_lines(start: date, end: date, facility: int | None) -> Subquery:
    """Every line of the purchase orders confirmed in the period: completed, not cancelled, and
    dated by the order's `modification_time`, the moment spec 033 dates them by (spec 038, R13).

    Legacy orders count. The amount is quantity × price × (1 − discount), before tax, whatever the
    tax-included flag says (spec 031, FR-034), with each order's rounding residual on its largest
    line, so an order's lines sum to its subtotal. Each line is placed by its own warehouse's
    facility (R17); a line without a warehouse has none. An order can span facilities, so the
    scope applies after the residual is placed. `received` is the quantity of confirmed receipt
    lines linked to it. `unit` is the price after discount, which price history reads.
    """
    line, order = PurchaseOrderDetail, PurchaseOrder
    since, until = period_bounds(start, end)
    dated = [
        order.completed.is_(True),
        order.cancelled.is_(False),
        order.modification_time >= since,
        order.modification_time < until,
    ]
    # Only the receipts of the period's orders, so the grouping never reads the whole history.
    receipt, got = InventoryReceipt, InventoryReceiptDetail
    received = (
        select(got.purchase_order_detail.label('line'), func.sum(got.quantity).label('qty'))
        .join(receipt, receipt.inventory_receipt_id == got.receipt)
        .join(line, line.purchase_order_detail_id == got.purchase_order_detail)
        .join(order, order.purchase_order_id == line.purchase_order)
        .where(receipt.completed.is_(True), receipt.cancelled.is_(False), *dated)
        .group_by(got.purchase_order_detail)
        .subquery('received')
    )
    unit = line.price * (1 - line.discount)
    amount = line.quantity * unit
    query = (
        select(
            line.purchase_order_detail_id.label('id'),
            line.purchase_order.label('doc'),
            line.product.label('product'),
            line.warehouse.label('warehouse'),
            Warehouse.facility.label('facility'),
            order.supplier.label('supplier'),
            order.modification_time.label('at'),
            line.quantity.label('qty'),
            unit.label('unit'),
            _with_residual(
                amount, line.purchase_order, line.purchase_order_detail_id, amount
            ).label('sub_c'),
            func.coalesce(received.c.qty, 0).label('received'),
        )
        .join(order, order.purchase_order_id == line.purchase_order)
        .outerjoin(Warehouse, Warehouse.warehouse_id == line.warehouse)
        .outerjoin(received, received.c.line == line.purchase_order_detail_id)
        .where(*dated)
    )
    if facility is None:
        return query.subquery('purchase_lines')
    placed = query.subquery('placed_lines')
    return select(placed).where(placed.c.facility == facility).subquery('purchase_lines')


#: The row source of each fact, by period and facility scope.
SOURCES = {
    Fact.SALE_LINE: sale_lines,
    Fact.REFUND_LINE: refund_lines,
    Fact.CREDIT_NOTE: credit_note_lines,
    Fact.PAYMENT: payment_rows,
    Fact.MOVEMENT: movement_rows,
    Fact.CREDIT_FLOW: credit_flow_rows,
    Fact.INVOICE_LINE: invoice_lines,
    Fact.INVOICE_COVERAGE: coverage_lines,
    Fact.PURCHASE_LINE: purchase_lines,
}

#: What each fact sums, by output name.
_SUMS = {
    Fact.SALE_LINE: ('sub_c', 'tax_c', 'disc_sub_c', 'disc_tax_c', 'cost_c', 'qty'),
    Fact.REFUND_LINE: ('sub_c', 'tax_c', 'cost_c', 'qty'),
    Fact.CREDIT_NOTE: ('sub_c', 'tax_c'),
    Fact.PAYMENT: ('collected_c', 'paid_out_c', 'store_c'),
    Fact.MOVEMENT: ('units_in', 'units_out'),
    Fact.CREDIT_FLOW: ('credit_sales_c', 'collected_c', 'due_c', 'due_collected_c'),
    Fact.INVOICE_LINE: ('receipt_c',),
    Fact.INVOICE_COVERAGE: ('sub_c', 'tax_c', 'inv_sub_c', 'inv_tax_c', 'lag_w'),
    Fact.PURCHASE_LINE: ('sub_c', 'qty', 'received'),
}


# ── Dimensions ────────────────────────────────────────────────────────────────


class _Joins:
    """The tables a dimension or filter needs beyond the fact's own rows, joined once each."""

    def __init__(self, rows: Subquery) -> None:
        self.rows = rows
        self.customer = False
        self.product = False
        self.label = False

    def apply(self, query: Select) -> Select:
        if self.customer:
            query = query.outerjoin(Customer, Customer.customer_id == self.rows.c.customer)
        if self.product:
            query = query.outerjoin(Product, Product.product_id == self.rows.c.product)
        if self.label:
            query = query.outerjoin(product_label, product_label.c.product == self.rows.c.product)
        return query


def _text_key(column: ColumnElement) -> ColumnElement:
    """Free text grouped by its trimmed, case-insensitive value; empty is "none"."""
    return func.lower(func.nullif(func.trim(column), ''))


def dimension_expression(
    fact: Fact, dimension: DimensionKey, joins: _Joins, grain: Grain
) -> ColumnElement:
    """The SQL value of `dimension` on `fact`'s rows; NULL ("none") when the fact lacks it."""
    if DIMENSIONS[dimension].facts[fact] is not Availability.COLUMN:
        return null()
    rows = joins.rows.c
    match dimension:
        case DimensionKey.DATE:
            return period_start(grain, rows.at)
        case DimensionKey.WEEKDAY:
            return weekday(rows.at)
        case DimensionKey.HOUR:
            return hour_of(rows.at)
        case DimensionKey.CUSTOMER_TYPE:
            joins.customer = True
            is_credit = and_(
                Customer.credit_limit > 0, Customer.customer_id != settings.default_customer_id
            )
            return case((is_credit, literal(CREDIT)), else_=literal(CASH))
        case DimensionKey.PAYMENT_TERMS:
            return case(
                (rows.payment_terms == int(PaymentTerms.NET_D), literal(CREDIT)),
                else_=literal(IMMEDIATE),
            )
        case DimensionKey.BRAND:
            joins.product = True
            return _text_key(Product.brand)
        case DimensionKey.MODEL:
            joins.product = True
            return _text_key(Product.model)
        case DimensionKey.LABEL:
            joins.label = True
            return product_label.c.label
        case DimensionKey.POINT_OF_SALE:
            return rows.point_sale
        case DimensionKey.SALES_AGENT:
            # The customer's agent, as spec 033's screen groups (research R9).
            joins.customer = True
            return Customer.salesperson
        case _:
            return rows[dimension.value]


def _display(fact: Fact, dimension: DimensionKey) -> ColumnElement:
    """The text shown for brand and model: the group's own spelling. Other labels are looked up."""
    if DIMENSIONS[dimension].facts[fact] is not Availability.COLUMN:
        return null()
    if dimension is DimensionKey.BRAND:
        return func.max(func.trim(Product.brand))
    if dimension is DimensionKey.MODEL:
        return func.max(func.trim(Product.model))
    return null()


def _matches(expression: ColumnElement, values: Sequence[Any]) -> ColumnElement:
    """`expression IN values`, where a `None` value also selects "none" (NULL)."""
    known = [value for value in values if value is not None]
    condition = expression.in_(known) if known else false()
    return or_(condition, expression.is_(None)) if None in values else condition


def _sum(column: ColumnElement) -> ColumnElement:
    return func.coalesce(func.sum(column), 0)


def query(fact: Fact, slice_: Slice) -> Select:
    """One grouped `SELECT` of `fact` for `slice_`: `k0…`/`l0…` keys and labels, then the sums."""
    rows = SOURCES[fact](slice_.start, slice_.end, slice_.facility)
    joins = _Joins(rows)
    columns: list[ColumnElement] = []
    grouped: list[ColumnElement] = []
    for index, dimension in enumerate(slice_.dimensions):
        columns.append(
            dimension_expression(fact, dimension, joins, slice_.grain).label(f'k{index}')
        )
        columns.append(_display(fact, dimension).label(f'l{index}'))
        if DIMENSIONS[dimension].facts[fact] is Availability.COLUMN:
            grouped.append(literal_column(f'k{index}'))

    conditions = [
        _matches(dimension_expression(fact, dimension, joins, slice_.grain), values)
        if DIMENSIONS[dimension].facts[fact] is Availability.COLUMN
        else (true() if None in values else false())
        for dimension, values in slice_.filters.items()
    ]

    c = rows.c
    if fact is Fact.INVOICE_LINE and slice_.stamped_only:
        conditions.append(c.document_status == STAMPED)
    columns += [_sum(c[name]).label(name) for name in _SUMS[fact]]
    if fact in (Fact.SALE_LINE, Fact.REFUND_LINE):
        columns.append(_sum(case((c.costed == 1, c.sub_c), else_=0)).label('costed_sub_c'))
    if fact is Fact.SALE_LINE:
        columns += [
            func.count(func.distinct(c.doc)).label('orders'),
            _sum(1 - c.costed).label('uncosted'),
            _sum(c.below).label('below'),
            # The alerts' amounts: the sales, without VAT, of the lines they count.
            _sum(case((c.costed == 0, c.sub_c), else_=0)).label('uncosted_sub_c'),
            _sum(case((c.below == 1, c.sub_c), else_=0)).label('below_sub_c'),
        ]
    if fact is Fact.REFUND_LINE:
        columns.append(func.count(func.distinct(c.doc)).label('documents'))
    if fact is Fact.PURCHASE_LINE:
        columns.append(func.count(func.distinct(c.doc)).label('orders'))
    if fact is Fact.PAYMENT:
        columns.append(_sum(case((c['class'] == COLLECTED, 1), else_=0)).label('payments'))
    if fact is Fact.INVOICE_LINE:
        invoice = c.document_type == INVOICE
        standalone = and_(invoice, c.standalone == 1)
        stamped = c.document_status == STAMPED
        columns += [
            _sum(case((invoice, c.sub_c), else_=0)).label('sub_c'),
            _sum(case((invoice, c.tax_c), else_=0)).label('tax_c'),
            _sum(case((standalone, c.sub_c), else_=0)).label('standalone_sub_c'),
            _sum(case((standalone, c.tax_c), else_=0)).label('standalone_tax_c'),
            # Output VAT (FR-143): stamped live invoices less stamped live credit notes.
            _sum(
                case(
                    (and_(stamped, invoice), c.tax_c),
                    (and_(stamped, c.document_type == CREDIT_NOTE), -c.tax_c),
                    else_=0,
                )
            ).label('vat_c'),
            func.count(func.distinct(case((invoice, c.doc), else_=null()))).label('invoices'),
        ]
    if fact is Fact.MOVEMENT:
        columns += [
            func.count().label('entries'),
            func.count(func.distinct(c.doc)).label('documents'),
        ]

    statement = joins.apply(select(*columns).select_from(rows)).where(true(), *conditions)
    if grouped:
        statement = statement.group_by(*grouped)
    if slice_.dimensions:
        # A fact that has none of the dimensions still aggregates to one row; with no documents
        # behind it, that row is not a "none" group but nothing at all.
        statement = statement.having(func.count() > 0)
    return statement
