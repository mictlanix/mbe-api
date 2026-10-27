"""Sales orders — the spine of the sales cycle.

The lifecycle rules enforced here are the ones the spec pins down and they are mutually exclusive
by design (SC-010): an order is payable once completed and uncancelled, cancellable only while no
money stands against it, and refundable only once fully paid. Because paying requires an
uncancelled order, a paid order is necessarily uncancelled — so the refund path needs no separate
cancellation check.

The decision rules are plain functions at the top of the module. They carry the branching the
tests need to exercise directly, and keeping them free of I/O is what makes that possible.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.clock import local_now
from app.core.config import settings
from app.core.constants import COST_PRICE_LIST_ID
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, FiscalDocumentType, FulfillmentType, OrderOrigin, PaymentTerms
from app.models.core import ExchangeRate, Facility, Warehouse
from app.models.customer import Customer
from app.models.fiscal import (
    FiscalDocument,
    FiscalDocumentDetail,
    FiscalDocumentRelation,
    FiscalDocumentXml,
)
from app.models.product import Product, ProductPrice
from app.models.sales import (
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesOrderPayment,
    SalesQuote,
)
from app.models.sat_catalog import SatUnitOfMeasurement
from app.schemas.sales_order import (
    SalesOrderCreate,
    SalesOrderLineCreate,
    SalesOrderLineUpdate,
    SalesOrderUpdate,
)
from app.schemas.sat_catalog import SatUnitOfMeasurementResponse
from app.services import (
    cfdi_invoice,
    customer_discount_service,
    documents,
    fiscal_document_service,
    image_service,
    stock_ledger,
    totals,
    warehouse_service,
)

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def derive_due_date(date: datetime, terms: PaymentTerms, *, credit_days: int) -> datetime:
    """Immediate terms fall due on the order date; credit terms add the customer's days (FR-015)."""
    if terms == PaymentTerms.NET_D:
        return date + timedelta(days=credit_days)
    return date


def assert_quantity_allowed(quantity: Decimal, *, min_order_qty: int) -> None:
    if quantity < min_order_qty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Quantity is below the product minimum of {min_order_qty}',
        )


def zero_priced_lines(lines: Iterable[object]) -> list[str]:
    """Name every line priced at zero, so confirmation can report them all at once (FR-017)."""
    offenders = []
    for line in lines:
        if getattr(line, 'price', Decimal(0)) == 0:
            name = getattr(line, 'product_name', 'line')
            offenders.append(f'{name} (line {getattr(line, "sales_order_detail_id", "?")})')
    return offenders


def stock_shortfalls(
    lines: Iterable[object],
    *,
    available: dict[tuple[int, int], Decimal],
    stocked: set[int],
) -> list[str]:
    """Report what the order cannot fulfil (FR-018, FR-055a).

    Quantities are aggregated per product+warehouse first: a product on three lines is one demand
    against the warehouse, not three independent ones that each pass while the total does not.

    The figure compared against is **availability** — on hand less what other confirmed orders
    have reserved — not raw on-hand. Confirmation stopped decrementing on-hand when consumption
    moved to delivery, so checking on-hand here would pass the same physical unit to every order
    that asked for it.
    """
    demand: dict[tuple[int, int], Decimal] = {}
    problems: list[str] = []

    for line in lines:
        product = getattr(line, 'product')
        if product not in stocked:
            continue
        warehouse = getattr(line, 'warehouse', None)
        if warehouse is None:
            name = getattr(line, 'product_name', str(product))
            problems.append(f'{name} requires stock but no warehouse is set')
            continue
        key = (product, warehouse)
        demand[key] = demand.get(key, Decimal(0)) + getattr(line, 'quantity', Decimal(0))

    for (product, warehouse), needed in demand.items():
        can_supply = available.get((product, warehouse), Decimal(0))
        if needed > can_supply:
            problems.append(
                f'Product {product} needs {needed} in warehouse {warehouse} but only '
                f'{can_supply} is available'
            )

    return problems


def missing_delivery_details(order: object, *, today: date) -> list[str]:
    """What a back-office order still lacks before it can be confirmed (spec 023 FR-008).

    - The fulfilment intent, always.
    - The address, when the intent is delivery or mixed. An unknown intent asks for the intent, not
      for an address the order may never need.
    - A promise date that has not passed. The 7-day default counts until it goes stale.

    The address is the order's own `ship_to`. mbe-ui enters addresses only on delivery orders,
    after confirming, so until it sets this one a back-office delivery cannot be confirmed
    (FR-018).
    """
    missing = []
    if order.fulfillment_intent is None:
        missing.append('fulfillment_intent')
    elif order.fulfillment_intent != FulfillmentType.PICKUP and order.ship_to is None:
        missing.append('ship_to')
    if order.promise_date.date() < today:
        missing.append('promise_date')
    return missing


def assert_can_cancel(order: object, *, live_applications: Sequence[object]) -> None:
    """Refuse cancelling a paid order, or one still holding money (FR-019, FR-019b)."""
    if getattr(order, 'cancelled', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Order is already cancelled'
        )

    if getattr(order, 'paid', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='A paid order cannot be cancelled — refund it instead',
        )

    if live_applications:
        ids = ', '.join(str(getattr(a, 'sales_order_payment_id', '?')) for a in live_applications)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f'Order still has payment applications ({ids}); reverse them before cancelling'
            ),
        )


# ── Context resolution ────────────────────────────────────────────────────────


def _point_sale(current: CurrentUser, requested: int | None) -> int:
    """FR-004a — `sales_order.point_sale` is NOT NULL but a user's setting is optional."""
    point_sale = requested if requested is not None else current.point_sale_id
    if point_sale is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='No point of sale is configured for your user; set one or supply it explicitly',
        )
    return point_sale


def _facility(current: CurrentUser) -> int:
    """FR-004 — the facility comes from the caller's context, never the request body."""
    if current.facility_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='No facility is configured for your user',
        )
    return current.facility_id


# ── Derived values ────────────────────────────────────────────────────────────


def _line_totals(line: SalesOrderDetail) -> None:
    subtotal, tax = totals.line_amounts(
        quantity=line.quantity,
        price=line.price,
        discount_rate=line.discount_rate,
        tax_rate=line.tax_rate,
        tax_included=line.tax_included,
    )
    line.__dict__['subtotal'] = subtotal.quantize(totals.CENTS)
    line.__dict__['tax_total'] = tax.quantize(totals.CENTS)
    line.__dict__['total'] = line.__dict__['subtotal'] + line.__dict__['tax_total']


async def units_by_product(
    db: AsyncSession, product_ids: Iterable[int]
) -> dict[int, SatUnitOfMeasurementResponse]:
    """The SAT unit of measurement of each product, keyed by product id (#145).

    One query for a whole line set, joined rather than fetched per line. The full record is returned
    rather than a flattened string so this reads the same as `unit_of_measurement` on the product
    endpoints — a client that has both in hand compares them field for field.
    """
    ids = {i for i in product_ids if i is not None}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(Product.product_id, SatUnitOfMeasurement)
            .join(
                SatUnitOfMeasurement,
                SatUnitOfMeasurement.sat_unit_of_measurement_id == Product.unit_of_measurement,
            )
            .where(Product.product_id.in_(ids))
        )
    ).all()
    return {
        product_id: SatUnitOfMeasurementResponse(
            id=unit.sat_unit_of_measurement_id,
            name=unit.name,
            description=unit.description,
            symbol=unit.symbol,
        )
        for product_id, unit in rows
    }


async def photos_by_product(db: AsyncSession, product_ids: Iterable[int]) -> dict[int, str | None]:
    """The photo URL of each product, keyed by product id (#157).

    Resolved through `image_service` so a line reads the same URL the product endpoints return,
    and batched for the same reason `units_by_product` is — one query for a whole line set.
    """
    ids = {i for i in product_ids if i is not None}
    if not ids:
        return {}
    rows = (
        await db.execute(
            select(Product.product_id, Product.photo).where(Product.product_id.in_(ids))
        )
    ).all()
    return {product_id: image_service.image_url(photo) for product_id, photo in rows}


async def attach_derived(db: AsyncSession, order: SalesOrder) -> SalesOrder:
    """Attach lines, computed money and the single lifecycle status.

    Written under `__dict__` keys rather than onto mapped columns, following the fix in
    `fk_expansion` — an instance shared through the identity map must keep its raw values.
    """
    lines = list(
        (
            await db.execute(
                select(SalesOrderDetail)
                .where(SalesOrderDetail.sales_order == order.sales_order_id)
                .order_by(SalesOrderDetail.sales_order_detail_id)
            )
        )
        .scalars()
        .all()
    )
    products = {line.product for line in lines}
    units = await units_by_product(db, products)
    photos = await photos_by_product(db, products)
    for line in lines:
        _line_totals(line)
        # A resumed sale re-reads its lines and never re-runs the product lookup, so the unit has to
        # come with them or the column is blank on exactly the rows already captured (#145). The
        # thumbnail slot beside each row is blank for the same reason without the photo (#157).
        line.__dict__['unit_of_measurement'] = units.get(line.product)
        line.__dict__['photo'] = photos.get(line.product)

    computed = (await order_balances(db, [order]))[order.sales_order_id]

    order.__dict__['lines'] = lines
    order.__dict__['subtotal'] = computed.totals.subtotal
    order.__dict__['tax_total'] = computed.totals.tax_total
    order.__dict__['total'] = computed.total
    # The total is what the document says; the balance is what is still owed on it. Returned goods
    # (#223) and credit notes against its invoices (spec 033) are not owed for. Floored at zero,
    # which keeps a refund against an already-paid order — this API's only kind (FR-060) — at zero.
    order.__dict__['balance'] = computed.balance
    order.__dict__['status'] = _status(order)
    return order


async def refunded_by_order(db: AsyncSession, order_ids: Sequence[int]) -> dict[int, Decimal]:
    """What has been handed back on each order, in one query for the whole page (#223).

    Goods a customer returned are goods they do not owe for, and nothing else in the data records
    that: the monolith settled a refund against an unpaid order by flipping `IsPaid` when the
    refund covered the balance, and otherwise left the reduction implied by the refund lines alone
    — no application row is ever written. Its `SalesOrder.Balance` subtracts them for that reason
    (`Model/SalesOrder.cs:246`).

    Totalled through `totals.document_totals`, the same rule the refund's own endpoints report, so
    a refund's total means one thing across the system. Note `discount` here against
    `discount_rate` on a sales order line — the columns differ, the arithmetic does not.
    """
    if not order_ids:
        return {}

    rows = (
        await db.execute(
            select(
                CustomerRefund.sales_order,
                CustomerRefund.customer_refund_id,
                CustomerRefundDetail,
            )
            .join(
                CustomerRefundDetail,
                CustomerRefundDetail.customer_refund == CustomerRefund.customer_refund_id,
            )
            .where(
                CustomerRefund.sales_order.in_(order_ids),
                CustomerRefund.completed.is_(True),
                CustomerRefund.cancelled.is_(False),
            )
        )
    ).all()

    lines_by_refund: dict[tuple[int, int], list[totals.Line]] = {}
    for order_id, refund_id, line in rows:
        lines_by_refund.setdefault((order_id, refund_id), []).append(
            totals.Line(
                quantity=line.quantity,
                price=line.price,
                discount_rate=line.discount,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
        )

    refunded: dict[int, Decimal] = {}
    for (order_id, _refund_id), lines in lines_by_refund.items():
        # Per refund, then summed: each is its own document and rounds once, exactly as the
        # refund endpoints report it. Totalling every line of every refund in one go would drift.
        refunded[order_id] = (
            refunded.get(order_id, Decimal(0)) + totals.document_totals(lines).total
        )

    return refunded


#: A balance at or below this is settled, the current system's rule for setting `paid` (spec 033).
SETTLED = Decimal('0.01')


def credit_note_shares(amount: Decimal, parts: dict[int, Decimal]) -> dict[int, Decimal]:
    """`amount` shared out in proportion to `parts`, in cents (spec 033, research R2).

    The order with the highest id takes the rounding remainder, so the shares always add up to the
    note: a cent lost to rounding would be a cent the invoice's orders still owe.
    """
    whole = sum(parts.values(), Decimal(0))
    if whole <= 0:
        return {}
    ids = sorted(parts)
    shares = {
        oid: (amount * parts[oid] / whole).quantize(totals.CENTS, rounding=ROUND_HALF_UP)
        for oid in ids[:-1]
    }
    shares[ids[-1]] = (amount - sum(shares.values(), Decimal(0))).quantize(totals.CENTS)
    return shares


def _fiscal_line_total(line: FiscalDocumentDetail) -> Decimal:
    """A CFDI line's total as the stamped document computes it."""
    return cfdi_invoice.line_amounts(
        cfdi_invoice.InvoiceLineData(
            product_service=line.product_service or '',
            product_code=line.product_code,
            quantity=line.quantity,
            unit=line.unit_of_measurement or '',
            unit_name=line.unit_of_measurement_name,
            description=line.product_name,
            price=line.price,
            discount_rate=line.discount,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
    ).total


async def _fiscal_lines_by_order(
    db: AsyncSession, document_ids: Iterable[int]
) -> dict[int, dict[int | None, Decimal]]:
    """Per document, its line totals summed by the sales order each line was copied from. Lines
    added by hand carry no order line and sum under `None`."""
    rows = (
        await db.execute(
            select(FiscalDocumentDetail, SalesOrderDetail.sales_order)
            .outerjoin(
                SalesOrderDetail,
                SalesOrderDetail.sales_order_detail_id == FiscalDocumentDetail.order_detail,
            )
            .where(FiscalDocumentDetail.document.in_(list(document_ids)))
        )
    ).all()
    by_document: dict[int, dict[int | None, Decimal]] = {}
    for line, order_id in rows:
        sums = by_document.setdefault(line.document, {})
        sums[order_id] = sums.get(order_id, Decimal(0)) + _fiscal_line_total(line)
    return by_document


async def _credited_by_order(db: AsyncSession, order_ids: Sequence[int]) -> dict[int, Decimal]:
    """Each order's share of the live credit notes against its invoices (spec 033, research R2).

    A note line copied from an order's line counts in full for that order. The rest of the note,
    its stamped total less those lines, is shared out by each order's part of the invoice: only 1
    of the 115 live note lines in mbe_dev keeps its order line, so the share is what applies in
    practice. The parts span every order on the invoice, not just `order_ids`, so an order's figure
    doesn't depend on which other orders were asked about.
    """
    if not order_ids:
        return {}

    invoice = aliased(FiscalDocument)
    invoices = set(
        (
            await db.execute(
                select(FiscalDocumentDetail.document)
                .join(
                    SalesOrderDetail,
                    SalesOrderDetail.sales_order_detail_id == FiscalDocumentDetail.order_detail,
                )
                .join(invoice, invoice.fiscal_document_id == FiscalDocumentDetail.document)
                .where(
                    SalesOrderDetail.sales_order.in_(order_ids),
                    invoice.type == FiscalDocumentType.INVOICE,
                    fiscal_document_service.live(invoice),
                )
                .distinct()
            )
        )
        .scalars()
        .all()
    )
    if not invoices:
        return {}

    note = aliased(FiscalDocument)
    note_rows = (
        await db.execute(
            select(note.fiscal_document_id, FiscalDocumentRelation.relation, FiscalDocumentXml.data)
            .join(
                FiscalDocumentRelation, FiscalDocumentRelation.document == note.fiscal_document_id
            )
            .join(
                FiscalDocumentXml,
                FiscalDocumentXml.fiscal_document_xml_id == note.fiscal_document_id,
            )
            .where(
                FiscalDocumentRelation.relation.in_(invoices),
                note.type == FiscalDocumentType.CREDIT_NOTE,
                fiscal_document_service.live(note),
            )
            .order_by(note.fiscal_document_id, FiscalDocumentRelation.fiscal_document_relation_id)
        )
    ).all()
    notes: dict[int, tuple[int, Decimal]] = {}
    for note_id, related, xml in note_rows:
        # A credit note relates one invoice (spec 028); the first relation stands for it.
        notes.setdefault(note_id, (related, fiscal_document_service.xml_total(xml) or Decimal(0)))
    if not notes:
        return {}

    parts = await _fiscal_lines_by_order(db, invoices)
    traced = await _fiscal_lines_by_order(db, notes)
    wanted = set(order_ids)
    credited: dict[int, Decimal] = {}
    for note_id, (related, note_total) in notes.items():
        lines = {oid: amount for oid, amount in traced.get(note_id, {}).items() if oid is not None}
        untraced = max(note_total - sum(lines.values(), Decimal(0)), Decimal(0))
        invoice_parts = {
            oid: amount for oid, amount in parts.get(related, {}).items() if oid is not None
        }
        for oid, share in credit_note_shares(untraced, invoice_parts).items():
            lines[oid] = lines.get(oid, Decimal(0)) + share
        for oid, amount in lines.items():
            if oid in wanted:
                credited[oid] = credited.get(oid, Decimal(0)) + amount
    return credited


@dataclass(frozen=True)
class OrderBalance:
    """What an order says and what is still owed on it (spec 033, FR-002)."""

    totals: totals.DocumentTotals
    refunded: Decimal
    credited: Decimal
    applied: Decimal
    balance: Decimal

    @property
    def total(self) -> Decimal:
        return self.totals.total


async def order_balances(db: AsyncSession, orders: Sequence[SalesOrder]) -> dict[int, OrderBalance]:
    """The one balance rule for sales orders, batched over `orders` (spec 033, FR-001).

    Total, less completed refunds, less the order's share of live credit notes, less live
    applications, floored at zero. Change handed back is not subtracted: it is money the payment
    returned, not money the order received (research R14).
    """
    ids = [order.sales_order_id for order in orders]
    if not ids:
        return {}

    lines_by_order: dict[int, list[totals.Line]] = {oid: [] for oid in ids}
    rows = (
        (await db.execute(select(SalesOrderDetail).where(SalesOrderDetail.sales_order.in_(ids))))
        .scalars()
        .all()
    )
    for row in rows:
        lines_by_order[row.sales_order].append(
            totals.Line(
                quantity=row.quantity,
                price=row.price,
                discount_rate=row.discount_rate,
                tax_rate=row.tax_rate,
                tax_included=row.tax_included,
            )
        )

    applied_rows = (
        await db.execute(
            select(SalesOrderPayment.sales_order, func.sum(SalesOrderPayment.amount))
            .where(
                SalesOrderPayment.sales_order.in_(ids),
                SalesOrderPayment.cancelled.is_(False),
            )
            .group_by(SalesOrderPayment.sales_order)
        )
    ).all()
    applied_by = {oid: amount or Decimal(0) for oid, amount in applied_rows}
    refunded_by = await refunded_by_order(db, ids)
    credited_by = await _credited_by_order(db, ids)

    balances = {}
    for oid in ids:
        computed = totals.document_totals(lines_by_order[oid])
        applied = applied_by.get(oid, Decimal(0))
        refunded = refunded_by.get(oid, Decimal(0))
        credited = credited_by.get(oid, Decimal(0))
        balances[oid] = OrderBalance(
            totals=computed,
            refunded=refunded,
            credited=credited,
            applied=applied,
            balance=totals.remaining(computed.total, [refunded, credited, applied]),
        )
    return balances


async def _credit_candidates(
    db: AsyncSession,
    *,
    customer: int | None = None,
    facility: int | None = None,
    due_before: datetime | None = None,
) -> list[tuple[SalesOrder, OrderBalance]]:
    """Credit orders not flagged paid, with their balances: the one customer-side selector.

    The flag closes an order whatever it computes (spec 033, clarification Q1), which keeps the
    16,405 legacy paid orders out and the set small. `payment_terms == NET_D` is load-bearing:
    `derive_due_date` makes an immediate-terms order due on its own date (#207).
    """
    query = select(SalesOrder).where(
        SalesOrder.payment_terms == PaymentTerms.NET_D,
        SalesOrder.completed.is_(True),
        SalesOrder.cancelled.is_(False),
        SalesOrder.paid.is_(False),
    )
    if customer is not None:
        query = query.where(SalesOrder.customer == customer)
    if facility is not None:
        query = query.where(SalesOrder.facility == facility)
    if due_before is not None:
        query = query.where(SalesOrder.due_date < due_before)
    orders = list((await db.execute(query.order_by(SalesOrder.sales_order_id))).scalars().all())
    balances = await order_balances(db, orders)
    return [(order, balances[order.sales_order_id]) for order in orders]


async def open_credit_orders(
    db: AsyncSession,
    *,
    customer: int | None = None,
    facility: int | None = None,
    due_before: datetime | None = None,
) -> list[tuple[SalesOrder, OrderBalance]]:
    """The customer side's open documents: credit orders not flagged paid that still owe more than
    `SETTLED` (spec 033, FR-007). The credit check, the AR view and statements read them."""
    candidates = await _credit_candidates(
        db, customer=customer, facility=facility, due_before=due_before
    )
    return [(order, balance) for order, balance in candidates if balance.balance > SETTLED]


async def attach_summary_totals(db: AsyncSession, orders: Sequence[SalesOrder]) -> None:
    """Compute totals and balances for a whole page in a few queries, not a few per row.

    `attach_derived` is the right shape for a single order but issues per-order queries; used in a
    loop over a page it is an N+1. List endpoints call this instead: `order_balances` batches the
    lines, applications, refunds and credit notes across every order on the page.
    """
    balances = await order_balances(db, orders)
    for order in orders:
        computed = balances[order.sales_order_id]
        order.__dict__['subtotal'] = computed.totals.subtotal
        order.__dict__['tax_total'] = computed.totals.tax_total
        order.__dict__['total'] = computed.total
        order.__dict__['balance'] = computed.balance
        order.__dict__['status'] = _status(order)


async def attach_customer_names(
    db: AsyncSession, orders: Sequence[SalesOrder] | Sequence[SalesQuote]
) -> None:
    """Project each row's customer name onto it, in one query for the whole page (#172).

    Quotes too (#213): the body only reads `.customer` and writes `__dict__`, so the hint is what
    had to widen, not the code. A quote has no `customer_name` override column to fall back on, so
    this is the only route to a name on that list.

    `SalesOrder.customer_name` is the per-document *override* and is null on every sale that did
    not set one, so a list rendering it shows nothing for ordinary sales. The name a client wants
    lives on `customer`, and a list row is the one shape with no other reason to fetch that record.

    Separate from `attach_summary_totals` for the reason `attach_relations` is separate in
    `cash_session_service` (#141): that helper computes money, this one expands a foreign key, and
    each is worth a query-count test of its own.
    """
    ids = {order.customer for order in orders}
    if not ids:
        return

    names = dict(
        (
            await db.execute(
                select(Customer.customer_id, Customer.name).where(Customer.customer_id.in_(ids))
            )
        ).all()
    )
    for order in orders:
        # `__dict__` rather than the attribute: there is no such column on the model, and the
        # instance may be shared through the identity map (see `attach_derived`).
        order.__dict__['customer_display_name'] = names.get(order.customer)


def _status(order: SalesOrder) -> str:
    from app.schemas.sales_order import derive_status

    return derive_status(
        completed=order.completed, cancelled=order.cancelled, paid=order.paid
    ).value


async def live_applications(db: AsyncSession, sales_order_id: int) -> Sequence[SalesOrderPayment]:
    return (
        (
            await db.execute(
                select(SalesOrderPayment).where(
                    SalesOrderPayment.sales_order == sales_order_id,
                    SalesOrderPayment.cancelled.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )


# ── Lookups ───────────────────────────────────────────────────────────────────


async def _exchange_rate(db: AsyncSession, currency: CurrencyCode, on: datetime) -> Decimal:
    if currency == settings.default_currency:
        return Decimal(1)
    rate = (
        await db.execute(
            select(ExchangeRate.rate)
            .where(ExchangeRate.target == int(currency), ExchangeRate.date <= on.date())
            .order_by(ExchangeRate.date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    return rate if rate is not None else Decimal(1)


async def _customer_or_404(db: AsyncSession, customer_id: int) -> Customer:
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Customer not found')
    return customer


async def _overdue_credit_orders(db: AsyncSession, customer_id: int) -> int:
    """How many credit orders this customer has let run past their due date still owing.

    One definition, read by both gates: the one that refuses credit terms and the one that refuses
    a confirmation (#219). Owing means a balance above 0.01 by the one balance rule, not the `paid`
    flag alone (spec 033, FR-006): 199 customers in mbe_dev were held by the flag and only 130 by
    what they owe, the rest by orders paid off whose flag was never set. An order flagged paid is
    closed whatever it computes, and `payment_terms == NET_D` is load-bearing (#207): both are
    `open_credit_orders`' rules.
    """
    return len(await open_credit_orders(db, customer=customer_id, due_before=local_now()))


async def _customer_credit_debt(db: AsyncSession, customer_id: int) -> Decimal:
    """What this customer currently owes on credit, in the deployment's base currency (#220).

    The sum of their open credit orders' balances by the one balance rule (spec 033, FR-001): total
    less completed refunds, less credit notes against the order's invoices, less live applications.
    The AR view and statements sum the same balances, so a credit refusal can be reconciled against
    what the user sees there.

    Multiplied by each order's exchange rate because a limit is one number and balances are in
    whatever currency their document used. Unexercised today — every open credit order in mbe_dev
    is MXN at exactly 1.0000 — so it is a rule written down, not a rule proven.
    """
    return sum(
        (
            balance.balance * order.exchange_rate
            for order, balance in await open_credit_orders(db, customer=customer_id)
        ),
        Decimal(0),
    )


async def _assert_within_credit_limit(
    db: AsyncSession, customer: Customer, *, adding: Decimal = Decimal(0)
) -> None:
    """FR-016's fourth refusal, which was never implemented: "is over their credit limit" (#220).

    `credit_limit` was read only as a yes/no flag — a customer with a 5,000 limit and 400,000
    outstanding took credit terms without complaint. The limits are real values, not sentinels:
    1,011 of 1,828 customers in mbe_dev sit between 1,000 and 100,000, and only 43 carry one large
    enough never to bind.

    `adding` is the order's own value, and it is what makes this bite before a breach rather than
    after one. Nobody in mbe_dev is over their limit today; customer 11202 is at **98.7%** of
    theirs, so the check that ignores the order in hand would let every future order through until
    one of them silently crossed the line. The monolith's `IsOverCreditLimit` takes the same
    parameter and defaults it on.

    The walk-in customer is exempt for the reason it is exempt from the hold (#219), and here the
    reason is sharper: its `credit_limit` is **0.00**, so any credit order against it exceeds its
    limit by construction, and it carries 139 such orders historically.
    """
    if customer.customer_id == settings.default_customer_id:
        return

    limit = customer.credit_limit or Decimal(0)
    debt = await _customer_credit_debt(db, customer.customer_id)
    if debt + adding > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f'Customer is over their credit limit: {debt + adding:.2f} of {limit:.2f} '
                f'would be outstanding'
            ),
        )


async def _assert_not_on_credit_hold(db: AsyncSession, customer_id: int) -> None:
    """A customer behind on credit commits nothing further — whatever this order's own terms are.

    The gate the monolith puts on `Confirm` (`SalesOrdersController.cs:1251`), and the reason its
    other gaps are harmless: `CreateFromSalesQuote` copies a quote's terms unchecked there, exactly
    as `convert_to_order` did here, and confirmation catches it anyway. Every route to a committed
    sale passes through here, so this is the one place a check cannot be walked around — including
    the case no front-door check can reach, a customer who was in good standing when the order was
    captured and is not by the time it is confirmed (#219).

    Not filtered to credit orders, deliberately: the hold is a fact about the *customer*, not about
    this document, and confirming a cash sale for a customer already in arrears still hands over
    goods. That is the monolith's rule and it is stricter than #207's create-time gate, which only
    fires when the order itself carries credit terms.

    The walk-in customer is exempt, where the monolith does not bother to exempt it. It has no
    credit line, so `_assert_credit_allowed` refuses to put it on credit terms and it should never
    hold an overdue credit order — but mbe_dev carries **139 NET_D orders against it** from before
    this API, and one of those falling overdue would stop **every** counter sale confirming (210 of
    its orders are open drafts right now). A hold on a customer that is a stand-in for "no customer"
    is a deployment outage, not a credit control.
    """
    if customer_id == settings.default_customer_id:
        return

    expired = await _overdue_credit_orders(db, customer_id)
    if expired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f'Customer is on credit hold: {expired} overdue credit order(s) must be '
                'settled before this order can be confirmed'
            ),
        )


def is_credit_customer(customer: object) -> bool:
    """A credit limit above zero, and never the walk-in customer (spec 023 FR-010).

    The one definition the credit default, the move onto a new customer and the cash rule share.
    Credit days play no part, unlike the monolith's `HasCredit`: 140 active customers have a limit
    and no days, and this API has always given them the credit default.
    """
    return bool(customer.credit_limit and customer.credit_limit > 0) and (
        customer.customer_id != settings.default_customer_id
    )


async def _assert_cash_allowed(db: AsyncSession, customer: Customer) -> None:
    """A credit customer pays cash only while they are under their credit limit (spec 023 FR-012).

    At or over it, credit is already refused (#220), and cash is refused too: buying in cash is not
    a way around a debt the customer has stopped paying down. The comparison is the debt alone, not
    the debt plus this order, because a cash order adds nothing to what they owe on credit.
    """
    if not is_credit_customer(customer):
        return
    debt = await _customer_credit_debt(db, customer.customer_id)
    if debt >= customer.credit_limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f'Customer must settle their credit before buying: debt {debt:.2f} of limit '
                f'{customer.credit_limit:.2f}'
            ),
        )


async def _assert_credit_allowed(db: AsyncSession, customer: Customer) -> None:
    """Credit terms need a real credit line and a customer in good standing (FR-016).

    Deliberately asserted against the *derived* terms too, not only against terms a caller asked
    for by name (#207). A customer with a credit line takes NET_D by default, so an order raised
    for one in arrears is refused even though the request said nothing about payment terms — and
    that refusal is the policy, not an oversight: no new order is opened for a customer who is
    behind, and a credit hold that a caller can step around by omitting a field is not a hold.

    The alternative considered was deriving IMMEDIATE when credit is not allowed, so a cash order
    could still be raised for a customer in arrears. That is a different policy, not a bug fix.
    What was wrong was only how it reads: a hold surfacing as a validation error on order creation.
    Hence the wording below.

    **Arrears means overdue *credit*.** The count was every completed, unpaid order past its due
    date, and `derive_due_date` sets `due_date = date` for immediate terms — so an unpaid counter
    sale was overdue the day after it was rung up and held the customer off every future order,
    under a message announcing "overdue credit order(s)". FR-016 says "expired outstanding credit",
    and the monolith's `HasExpiredCredits` filters `Terms == NetD` for the same reason. Measured on
    mbe_dev, that difference is **25 of 175 held customers** — 72 customers carry 172 overdue
    immediate-terms rows, and 25 of them have no overdue credit order at all.
    """
    if customer.customer_id == settings.default_customer_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='The default walk-in customer cannot buy on credit',
        )
    if customer.credit_limit is None or customer.credit_limit <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Customer has no credit limit',
        )

    expired = await _overdue_credit_orders(db, customer.customer_id)
    if expired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f'Customer is on credit hold: {expired} overdue credit order(s) must be '
                'settled before a new order can be raised'
            ),
        )

    # Debt so far only: at creation the order has no lines, so there is no value to weigh against
    # the limit yet. Confirmation weighs the order itself (#220).
    await _assert_within_credit_limit(db, customer)


async def _price_for(db: AsyncSession, product: Product, price_list: int) -> ProductPrice | None:
    return (
        await db.execute(
            select(ProductPrice).where(
                ProductPrice.product == product.product_id,
                ProductPrice.price_list == price_list,
            )
        )
    ).scalar_one_or_none()


# ── Header operations ─────────────────────────────────────────────────────────


async def create_order(
    db: AsyncSession, data: SalesOrderCreate, *, current: CurrentUser
) -> SalesOrder:
    employee = current.employee_id
    facility = _facility(current)
    point_sale = _point_sale(current, data.point_sale)

    customer = await _customer_or_404(
        db, data.customer if data.customer is not None else settings.default_customer_id
    )

    now = data.date or local_now()
    currency = data.currency if data.currency is not None else settings.default_currency

    if data.payment_terms is not None:
        terms = data.payment_terms
    else:
        terms = PaymentTerms.NET_D if is_credit_customer(customer) else PaymentTerms.IMMEDIATE
    # Outer indentation on purpose: the hold applies to the terms the order will actually carry,
    # whether the caller named them or the derivation above chose them (#207).
    if terms == PaymentTerms.NET_D:
        await _assert_credit_allowed(db, customer)
    else:
        await _assert_cash_allowed(db, customer)

    order = SalesOrder(
        facility=facility,
        serial=None,
        point_sale=point_sale,
        salesperson=data.salesperson
        or documents.default_salesperson(customer.salesperson, employee),
        customer=customer.customer_id,
        customer_name=data.customer_name,
        sales_quote=None,
        payment_terms=int(terms),
        date=now,
        promise_date=data.promise_date
        or now + timedelta(days=settings.max_days_to_deliver_stockables),
        due_date=derive_due_date(now, terms, credit_days=customer.credit_days or 0),
        completed=False,
        cancelled=False,
        paid=False,
        delivered=False,
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        contact=data.contact,
        ship_to=data.ship_to,
        recipient=data.recipient,
        recipient_name=None,
        recipient_address=None,
        comment=data.comment,
        currency=currency,
        exchange_rate=await _exchange_rate(db, currency, now),
        priority=int(data.priority),
        partial_deliveries=None,
        # Deliberately not derived from `ship_to`: the address can say delivery or counter pickup
        # and cannot say mixed, so inferring here would record a confident wrong answer for the one
        # case the field exists to carry (#170).
        fulfillment_intent=(
            None if data.fulfillment_intent is None else int(data.fulfillment_intent)
        ),
        # Deliberately not derived from `point_sale`: it is the caller's register whichever
        # workflow this is, so inferring would label every back-office order raised by a user with
        # a register as a counter sale — the defect #209 exists to fix, restated as a default.
        origin=(None if data.origin is None else int(data.origin)),
        balance_zeroed_time=None,
    )
    db.add(order)
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def get_order(db: AsyncSession, sales_order_id: int) -> SalesOrder | None:
    return await db.get(SalesOrder, sales_order_id)


async def list_orders(
    db: AsyncSession,
    *,
    current: CurrentUser,
    mine: bool = False,
    customer: int | None = None,
    salesperson: int | None = None,
    order_status: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    facility: int | None = None,
    point_sale: int | None = None,
    origin: OrderOrigin | None = None,
    exclude_origin: OrderOrigin | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[SalesOrder], int]:
    """No implicit scoping beyond the caller's facility — narrowing is explicit (FR-009)."""
    base = select(SalesOrder)
    count_q = select(func.count()).select_from(SalesOrder)

    def both(clause):  # noqa: ANN001, ANN202 — local helper, mirrors existing services
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    both(SalesOrder.facility == (facility if facility is not None else current.facility_id))

    if mine and current.employee_id is not None:
        both(
            or_(
                SalesOrder.creator == current.employee_id,
                SalesOrder.updater == current.employee_id,
                SalesOrder.salesperson == current.employee_id,
            )
        )
    if customer is not None:
        both(SalesOrder.customer == customer)
    if salesperson is not None:
        both(SalesOrder.salesperson == salesperson)
    if point_sale is not None:
        both(SalesOrder.point_sale == point_sale)
    if origin is not None:
        both(SalesOrder.origin == int(origin))
    if exclude_origin is not None:
        # The `IS NULL` arm is load-bearing, not defensive. `origin != 1` evaluates to NULL for a
        # row that recorded nothing, a WHERE keeps only rows evaluating to true, and every order
        # raised before #209 — 335,816 of them — would drop out of the register's own list. An
        # order that recorded no origin is not a back-office order, so it belongs in the answer.
        both(
            or_(
                SalesOrder.origin.is_(None),
                SalesOrder.origin != int(exclude_origin),
            )
        )
    if date_from is not None:
        both(SalesOrder.date >= date_from)
    if date_to is not None:
        both(SalesOrder.date <= date_to)
    if order_status == 'draft':
        both(SalesOrder.completed.is_(False))
        both(SalesOrder.cancelled.is_(False))
    elif order_status == 'completed':
        both(SalesOrder.completed.is_(True))
        both(SalesOrder.cancelled.is_(False))
    elif order_status == 'cancelled':
        both(SalesOrder.cancelled.is_(True))
    elif order_status == 'paid':
        both(SalesOrder.paid.is_(True))
    if search:
        if search.isdigit():
            both(or_(SalesOrder.sales_order_id == int(search), SalesOrder.serial == int(search)))
        else:
            # Matching the override alone matched a column that is null on the rows a cashier is
            # looking for, so a name search returned nothing and read as "no results" (#172). The
            # customer's own name is what they are typing. Subquery rather than a join, as in
            # `customer_payment_service.outstanding_orders`: a join would multiply rows and the
            # count query has to stay countable.
            like = f'%{search}%'
            both(
                or_(
                    SalesOrder.customer.in_(
                        select(Customer.customer_id).where(Customer.name.ilike(like))
                    ),
                    SalesOrder.customer_name.ilike(like),
                )
            )

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(SalesOrder.sales_order_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    await attach_summary_totals(db, items)
    await attach_customer_names(db, items)
    return items, total


async def update_order(
    db: AsyncSession, order: SalesOrder, data: SalesOrderUpdate, *, current: CurrentUser
) -> SalesOrder:
    employee = current.employee_id
    changes = data.model_dump(exclude_unset=True)

    # Priority is the one field that survives completion (FR-011)
    if order.completed or order.cancelled:
        if set(changes) - {'priority'}:
            documents.assert_editable(order)
        if 'priority' in changes and changes['priority'] is not None:
            order.priority = int(changes['priority'])
            order.updater = employee
            order.modification_time = local_now()
            await db.commit()
            await db.refresh(order)
        return await attach_derived(db, order)

    if 'customer' in changes and changes['customer'] is not None:
        customer = await _customer_or_404(db, changes['customer'])
        moved = customer.customer_id != order.customer
        outgoing = order.customer  # read before the write below loses it
        order.customer = customer.customer_id
        if moved:
            # Two conditions, not one: the rep follows the customer (#195), prices follow the
            # list (#196). Gating both on the id rewrote same-list moves — 98.6% of them.
            if customer.salesperson is not None:  # nullable there, NOT NULL here
                order.salesperson = customer.salesperson
            previous = await _customer_or_404(db, outgoing)
            if customer.price_list != previous.price_list:
                await _reprice_lines(db, order, customer)
            # Credit follows a credit customer, as on create (spec 023 FR-011). Only this
            # direction: a credit order moved off credit is still the end-state check's to refuse.
            if (
                'payment_terms' not in changes
                and order.payment_terms == PaymentTerms.IMMEDIATE
                and is_credit_customer(customer)
            ):
                order.payment_terms = int(PaymentTerms.NET_D)
                order.due_date = derive_due_date(
                    order.date, PaymentTerms.NET_D, credit_days=customer.credit_days or 0
                )
    if 'payment_terms' in changes and changes['payment_terms'] is not None:
        terms = PaymentTerms(changes['payment_terms'])
        customer = await _customer_or_404(db, order.customer)
        order.payment_terms = int(terms)
        order.due_date = derive_due_date(order.date, terms, credit_days=customer.credit_days or 0)
    # Asserted on the state the order ends the request in, rather than inside either branch above
    # (#219). Moving a NET_D order onto a customer in arrears revisited the salesperson and the
    # prices and never the credit, so the terms survived the move unchallenged; and a request that
    # sets both fields must be judged once, at the end, or `{customer: walk-in, payment_terms: 0}`
    # is refused for terms the same request is clearing.
    if ('customer' in changes or 'payment_terms' in changes) and (
        order.payment_terms == PaymentTerms.NET_D
    ):
        await _assert_credit_allowed(db, await _customer_or_404(db, order.customer))
    elif 'customer' in changes or 'payment_terms' in changes:
        await _assert_cash_allowed(db, await _customer_or_404(db, order.customer))
    if 'currency' in changes and changes['currency'] is not None:
        await _change_currency(db, order, CurrencyCode(changes['currency']))

    for field in ('promise_date', 'contact', 'ship_to', 'recipient', 'customer_name', 'comment'):
        if field in changes:
            setattr(order, field, changes[field])
    # Last, so an explicit value beats the customer's; `is not None` because the column is NOT
    # NULL and the loop above would have written a sent `null` into it (#195).
    if changes.get('salesperson') is not None:
        order.salesperson = changes['salesperson']
    if 'fulfillment_intent' in changes:
        # `int()` rather than the enum member, matching `priority` below: the column is a plain
        # SmallInteger and storing the member would leave the attribute an enum on this instance
        # and an int on the next one read back.
        value = changes['fulfillment_intent']
        order.fulfillment_intent = None if value is None else int(value)
    if 'priority' in changes and changes['priority'] is not None:
        order.priority = int(changes['priority'])

    order.updater = employee
    order.modification_time = local_now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def _reprice_lines(db: AsyncSession, order: SalesOrder, customer: Customer) -> None:
    """#131 — the customer's price list changed, so every line moves onto the new one.

    Unconditionally, once called. A line tracks whichever list the order's customer is on,
    including a line whose price a salesperson typed in: `sales_order_detail` stores no marker
    distinguishing a hand-entered price from a listed one, so "preserve the override" could only
    ever have been a guess at what the previous list would have charged.

    That holds only where the list differs, which is why #196 narrowed the trigger from the
    customer id to the price list: within one list there was never anything to guess at.

    Two things are deliberately left alone. `tax_rate` follows the product, not the customer, so a
    customer change has no bearing on it — a per-line override (#135) is the caller's to set and
    keep. `cost` comes from the cost price list, which is customer-independent.

    A product with no row on the new list prices at zero, exactly as `add_line` would for that
    customer; confirmation's zero-price gate is what catches it rather than a failure here.

    Only ever called when the customer moved *and* the two price lists differ; neither a `PUT`
    echoing back the current customer nor a move within one list is a change in the list.
    """
    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order.sales_order_id)
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        return

    # One query for the whole page of lines, not one per line (the N+1 rule).
    rows = (
        (
            await db.execute(
                select(ProductPrice).where(
                    ProductPrice.product.in_({line.product for line in lines}),
                    ProductPrice.price_list == customer.price_list,
                )
            )
        )
        .scalars()
        .all()
    )
    listed = {row.product: row.price for row in rows}
    for line in lines:
        line.price = listed.get(line.product, Decimal(0))


async def _change_currency(db: AsyncSession, order: SalesOrder, currency: CurrencyCode) -> None:
    """FR-020 — the header rate and every line move together, never a mixed-currency order."""
    rate = await _exchange_rate(db, currency, order.date)
    order.currency = currency
    order.exchange_rate = rate

    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order.sales_order_id)
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        line.currency = currency
        line.exchange_rate = rate


# ── Line operations ───────────────────────────────────────────────────────────


async def add_line(
    db: AsyncSession, order: SalesOrder, data: SalesOrderLineCreate, *, current: CurrentUser
) -> SalesOrder:
    documents.assert_editable(order)
    employee = current.employee_id

    product = await db.get(Product, data.product)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')

    customer = await _customer_or_404(db, order.customer)
    listed = await _price_for(db, product, customer.price_list)
    cost_row = await _price_for(db, product, COST_PRICE_LIST_ID)

    if data.warehouse is not None:
        await warehouse_service.assert_sellable(db, data.warehouse)

    quantity = data.quantity if data.quantity is not None else Decimal(product.min_order_qty)
    assert_quantity_allowed(quantity, min_order_qty=product.min_order_qty)

    price = data.price if data.price is not None else (listed.price if listed else Decimal(0))
    cost = cost_row.price if cost_row else Decimal(0)
    # Read only when the request names no rate: an explicit 0 is a decision, not an absence.
    discount = data.discount_rate
    if discount is None:
        discount = await customer_discount_service.rate_for(
            db, customer.customer_id, product.product_id
        ) or Decimal(0)

    line = SalesOrderDetail(
        sales_order=order.sales_order_id,
        product=product.product_id,
        quantity=quantity,
        cost=cost,
        price=price,
        discount_rate=discount,
        tax_rate=data.tax_rate if data.tax_rate is not None else product.tax_rate,
        product_code=product.code,
        product_name=product.name,
        warehouse=data.warehouse,
        exchange_rate=order.exchange_rate,
        currency=order.currency,
        tax_included=product.tax_included,
        comment=data.comment if data.comment is not None else product.comment,
    )
    db.add(line)
    order.updater = employee
    order.modification_time = local_now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def update_line(
    db: AsyncSession,
    order: SalesOrder,
    line: SalesOrderDetail,
    data: SalesOrderLineUpdate,
    *,
    current: CurrentUser,
) -> SalesOrder:
    documents.assert_editable(order)
    employee = current.employee_id
    changes = data.model_dump(exclude_unset=True)

    product = await db.get(Product, line.product)
    if 'quantity' in changes and changes['quantity'] is not None:
        assert_quantity_allowed(
            changes['quantity'], min_order_qty=product.min_order_qty if product else 1
        )
        line.quantity = changes['quantity']
    if 'price' in changes and changes['price'] is not None:
        line.price = changes['price']
    if changes.get('warehouse') is not None:
        await warehouse_service.assert_sellable(db, changes['warehouse'])
    for field in ('discount_rate', 'tax_rate', 'warehouse', 'comment'):
        if field in changes and changes[field] is not None:
            setattr(line, field, changes[field])

    order.updater = employee
    order.modification_time = local_now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def remove_line(
    db: AsyncSession, order: SalesOrder, line: SalesOrderDetail, *, current: CurrentUser
) -> SalesOrder:
    documents.assert_editable(order)
    order.updater = current.employee_id
    order.modification_time = local_now()
    await db.delete(line)
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def get_line(db: AsyncSession, order: SalesOrder, line_id: int) -> SalesOrderDetail | None:
    line = await db.get(SalesOrderDetail, line_id)
    if line is None or line.sales_order != order.sales_order_id:
        return None
    return line


# ── Transitions ───────────────────────────────────────────────────────────────


async def confirm_order(db: AsyncSession, order: SalesOrder, *, current: CurrentUser) -> SalesOrder:
    """Assign the folio, commit the stock, freeze the document — one transaction (FR-017)."""
    documents.assert_editable(order)
    # Before the folio and before the reservation: this is the act that extends credit, and until
    # #219 nothing checked it here. Creation and an explicit terms change were both guarded, but a
    # customer who fell behind after capture, an order moved onto a customer in arrears, and a
    # quote converted on credit terms all reached a committed sale unchallenged.
    await _assert_not_on_credit_hold(db, order.customer)
    if order.origin == OrderOrigin.BACK_OFFICE:
        missing = missing_delivery_details(order, today=local_now().date())
        if missing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={'message': 'Order is missing delivery details', 'missing': missing},
            )
    employee = current.employee_id

    lines = list(
        (
            await db.execute(
                select(SalesOrderDetail).where(SalesOrderDetail.sales_order == order.sales_order_id)
            )
        )
        .scalars()
        .all()
    )
    if not lines:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Cannot confirm an order with no lines'
        )

    offenders = zero_priced_lines(lines)
    if offenders:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'message': 'Order has lines priced at zero', 'lines': offenders},
        )

    stocked = await _stocked_products(db, {line.product for line in lines})
    available: dict[tuple[int, int], Decimal] = {}
    for line in lines:
        if line.product in stocked and line.warehouse is not None:
            key = (line.product, line.warehouse)
            if key not in available:
                available[key] = await stock_ledger.available(
                    db, product=line.product, warehouse=line.warehouse
                )

    problems = stock_shortfalls(lines, available=available, stocked=stocked)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'message': 'Insufficient stock', 'lines': problems},
        )

    # Only a credit order adds to what the customer owes, so a cash sale is weighed against no
    # limit — unlike the hold above, which is a fact about the customer whatever this order is
    # (#219). Here the lines exist, so this is the one gate that can refuse the order that *would*
    # take them over rather than the one after it (#220).
    if order.payment_terms == PaymentTerms.NET_D:
        computed = totals.document_totals(
            [
                totals.Line(
                    quantity=line.quantity,
                    price=line.price,
                    discount_rate=line.discount_rate,
                    tax_rate=line.tax_rate,
                    tax_included=line.tax_included,
                )
                for line in lines
            ]
        )
        await _assert_within_credit_limit(
            db,
            await _customer_or_404(db, order.customer),
            adding=computed.total * order.exchange_rate,
        )
    else:
        await _assert_cash_allowed(db, await _customer_or_404(db, order.customer))

    order.serial = await documents.assign_folio(db, SalesOrder, facility=order.facility)

    # Claim the stock rather than take it. The goods stay on the shelf and in `on_hand` until
    # the truck leaves; the delivery posts the movement (FR-055).
    for line in lines:
        if line.product in stocked and line.warehouse is not None:
            stock_ledger.reserve(
                db,
                sales_order=order.sales_order_id,
                product=line.product,
                warehouse=line.warehouse,
                quantity=line.quantity,
            )

    order.completed = True
    order.updater = employee
    order.modification_time = local_now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def cancel_order(db: AsyncSession, order: SalesOrder, *, current: CurrentUser) -> SalesOrder:
    """Retire the order and give back the stock it took (FR-019, FR-019a, FR-019b)."""
    employee = current.employee_id
    assert_can_cancel(order, live_applications=await live_applications(db, order.sales_order_id))

    if order.completed:
        # Nothing left the warehouse, so there is nothing to compensate for: releasing the claim
        # restores availability outright (FR-056). Goods already dispatched are not reachable
        # here — a delivered order's stock is resolved at the stop, not by cancelling the sale.
        await stock_ledger.release_reservations(db, sales_order=order.sales_order_id)

    order.cancelled = True
    order.updater = employee
    order.modification_time = local_now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def _stocked_products(db: AsyncSession, product_ids: set[int]) -> set[int]:
    """Products that both require stock verification and are stockable (FR-018)."""
    if not product_ids:
        return set()
    rows = (
        (
            await db.execute(
                select(Product.product_id).where(
                    Product.product_id.in_(product_ids),
                    Product.stock_verification.is_(True),
                    Product.stockable.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)


# ── Product lookup ────────────────────────────────────────────────────────────


BARCODE_LENGTH = 13


async def _find_salable(db: AsyncSession, *, pattern: str, limit: int) -> Sequence[Product]:
    """A barcode scan first, then an ordinary search — and a scan that misses falls through (#208).

    Treating a 13-digit numeric pattern as a scan is right for the device, but it is not a claim
    that nothing else can look like one. A catalog migrated with its EAN in `code` produces exactly
    that shape, and matching it against `bar_code` alone returned nothing for a product that is
    present, salable and priced: in mbe_dev **3,612 of 21,585 salable products have a 13-digit
    numeric `code` with no matching `bar_code`, and only 43 products carry a `bar_code` at all**.

    So the scan branch stays a fast path rather than a verdict. A real scan still resolves in one
    query and is unaffected; the fallback only runs where the lookup used to report an empty
    catalog. It is two statements rather than one `or_` because a search that also matched
    `bar_code` would let free text outrank the scanned row inside `limit`.
    """
    salable = select(Product).where(Product.salable.is_(True))

    if pattern.isdigit() and len(pattern) == BARCODE_LENGTH:
        scanned = (
            (await db.execute(salable.where(Product.bar_code == pattern).limit(limit)))
            .scalars()
            .all()
        )
        if scanned:
            return scanned

    like = f'%{pattern}%'
    return (
        (
            await db.execute(
                salable.where(
                    or_(
                        Product.name.ilike(like),
                        Product.code.ilike(like),
                        Product.sku.ilike(like),
                        Product.brand.ilike(like),
                        Product.model.ilike(like),
                    )
                ).limit(limit)
            )
        )
        .scalars()
        .all()
    )


async def _salable_by_id(db: AsyncSession, product_ids: list[int]) -> Sequence[Product]:
    """The salable products among `product_ids`; unknown or unsalable ids are simply absent (#237).

    A client checking stock for lines already on a sale knows the ids, and a text search cannot
    reliably get back to them: `code` matches siblings under `limit`, and a 13-digit `code` with no
    `bar_code` matched nothing at all before #208.
    """
    return (
        (
            await db.execute(
                select(Product).where(
                    Product.salable.is_(True), Product.product_id.in_(product_ids)
                )
            )
        )
        .scalars()
        .all()
    )


async def lookup_products(
    db: AsyncSession,
    *,
    pattern: str | None = None,
    products: list[int] | None = None,
    customer_id: int,
    warehouse: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find salable products with the customer's price and per-warehouse stock (FR-021).

    By `products` when given, otherwise by `pattern`.
    """
    found = (
        await _salable_by_id(db, products)
        if products
        else await _find_salable(db, pattern=pattern or '', limit=limit)
    )
    customer = await _customer_or_404(db, customer_id)

    # In-transit locations are ordinary warehouse rows so `on_hand` reports their balances with
    # no new mechanism (spec 012, research R3). Goods on a truck are not pickable, so offering one
    # here would invite a salesperson to promise stock that has already left. There is one per
    # facility now, so all of them are excluded by flag (spec 013, FR-012).
    offered = (
        Warehouse.warehouse_id == warehouse
        if warehouse is not None
        else and_(Warehouse.warehouse_id > 0, Warehouse.in_transit.is_(False))
    )
    # The owning facility rides along so a cashier can group other branches' stock without
    # `WAREHOUSES READ`, which `GET /warehouses` would demand (#242).
    rows = (
        await db.execute(
            select(Warehouse.warehouse_id, Warehouse.name, Facility.facility_id, Facility.name)
            .join(Facility, Facility.facility_id == Warehouse.facility)
            .where(offered)
        )
    ).all()
    known: dict[int, dict[str, object]] = {
        wid: {'warehouse_name': name, 'facility': fid, 'facility_name': fname}
        for wid, name, fid, fname in rows
    }
    # An explicitly named warehouse is reported even when no row matches, as it always was.
    unknown: dict[str, object] = {'warehouse_name': None, 'facility': None, 'facility_name': None}
    warehouses = known if warehouse is None else {warehouse: known.get(warehouse, unknown)}

    # Two aggregate queries for the whole result set, not two per product per warehouse.
    stockable = {p.product_id for p in found if p.stockable}
    on_hand_totals = await stock_ledger.on_hand_by_warehouse(db, products=stockable)
    reserved_totals = await stock_ledger.reserved_by_warehouse(db, products=stockable)
    units = await units_by_product(db, {p.product_id for p in found})

    results: list[dict] = []
    for product in found:
        listed = await _price_for(db, product, customer.price_list)
        stock: list[dict] = []
        if product.stockable:
            for wid, located in warehouses.items():
                held = on_hand_totals.get((product.product_id, wid), Decimal(0))
                claimed = reserved_totals.get((product.product_id, wid), Decimal(0))
                stock.append(
                    {
                        'warehouse': wid,
                        **located,
                        'on_hand': held,
                        # What confirmation will actually allow: a salesperson shown raw on-hand
                        # sees five units and is refused, because those five are reserved.
                        'available': held - claimed,
                    }
                )

        results.append(
            {
                'product': product.product_id,
                'code': product.code,
                'name': product.name,
                'sku': product.sku,
                'brand': product.brand,
                'model': product.model,
                'bar_code': product.bar_code,
                'unit_of_measurement': units.get(product.product_id),
                # The row is already loaded, so the photo needs no second query — only the same
                # resolution the product endpoints apply (#157).
                'photo': image_service.image_url(product.photo),
                'price': listed.price if listed else Decimal(0),
                'tax_rate': product.tax_rate,
                'tax_included': product.tax_included,
                'min_order_qty': product.min_order_qty,
                'stock_required': product.stock_verification,
                'stockable': product.stockable,
                'stock': stock,
            }
        )
    return results
