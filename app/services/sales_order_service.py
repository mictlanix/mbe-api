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
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.constants import COST_PRICE_LIST_ID
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, OrderOrigin, PaymentTerms
from app.models.core import ExchangeRate, Warehouse
from app.models.customer import Customer
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
from app.services import documents, image_service, stock_ledger, totals

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
        ids = ', '.join(
            str(getattr(a, 'sales_order_payment_id', '?')) for a in live_applications
        )
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

    applied = await applied_amount(db, order.sales_order_id)
    refunded = await refunded_amount(db, order.sales_order_id)

    order.__dict__['lines'] = lines
    order.__dict__['subtotal'] = computed.subtotal
    order.__dict__['tax_total'] = computed.tax_total
    order.__dict__['total'] = computed.total
    # The total is what the document says; the balance is what is still owed on it, and returned
    # goods are not owed for (#223). Floored at zero by `remaining`, which is what keeps a refund
    # against an already-paid order — this API's only kind (FR-060) — at zero rather than negative.
    order.__dict__['balance'] = totals.remaining(computed.total, [applied, refunded])
    order.__dict__['status'] = _status(order)
    return order


async def refunded_by_order(
    db: AsyncSession, order_ids: Sequence[int]
) -> dict[int, Decimal]:
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
        refunded[order_id] = refunded.get(order_id, Decimal(0)) + totals.document_totals(
            lines
        ).total

    return refunded


async def refunded_amount(db: AsyncSession, sales_order_id: int) -> Decimal:
    """`refunded_by_order` for one order."""
    return (await refunded_by_order(db, [sales_order_id])).get(sales_order_id, Decimal(0))


async def attach_summary_totals(db: AsyncSession, orders: Sequence[SalesOrder]) -> None:
    """Compute totals and balances for a whole page in two queries, not two per row.

    `attach_derived` is the right shape for a single order but issues per-order queries; used in a
    loop over a page it is an N+1. List endpoints call this instead, which batches the line fetch
    and the applied-amount aggregate across every order on the page.
    """
    ids = [order.sales_order_id for order in orders]
    if not ids:
        return

    lines_by_order: dict[int, list[totals.Line]] = {oid: [] for oid in ids}
    rows = (
        (
            await db.execute(
                select(SalesOrderDetail).where(SalesOrderDetail.sales_order.in_(ids))
            )
        )
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
    applied_by_order = {oid: amount or Decimal(0) for oid, amount in applied_rows}
    refunded_by = await refunded_by_order(db, ids)

    for order in orders:
        computed = totals.document_totals(lines_by_order.get(order.sales_order_id, []))
        applied = applied_by_order.get(order.sales_order_id, Decimal(0))
        refunded = refunded_by.get(order.sales_order_id, Decimal(0))
        order.__dict__['subtotal'] = computed.subtotal
        order.__dict__['tax_total'] = computed.tax_total
        order.__dict__['total'] = computed.total
        order.__dict__['balance'] = totals.remaining(computed.total, [applied, refunded])
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


async def applied_amount(db: AsyncSession, sales_order_id: int) -> Decimal:
    """Sum of non-cancelled applications against an order."""
    total = (
        await db.execute(
            select(func.sum(SalesOrderPayment.amount)).where(
                SalesOrderPayment.sales_order == sales_order_id,
                SalesOrderPayment.cancelled.is_(False),
            )
        )
    ).scalar_one_or_none()
    return total if total is not None else Decimal(0)


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
    """How many credit orders this customer has let run past their due date, unpaid.

    One definition, read by both gates: the one that refuses credit terms and the one that refuses
    a confirmation (#219). `payment_terms == NET_D` is load-bearing — `derive_due_date` makes an
    immediate-terms order due on its own date, so without it every unpaid counter sale counts as
    arrears (#207).
    """
    return (
        await db.execute(
            select(func.count())
            .select_from(SalesOrder)
            .where(
                SalesOrder.customer == customer_id,
                SalesOrder.payment_terms == PaymentTerms.NET_D,
                SalesOrder.completed.is_(True),
                SalesOrder.cancelled.is_(False),
                SalesOrder.paid.is_(False),
                SalesOrder.due_date < datetime.now(),
            )
        )
    ).scalar_one()


async def _customer_credit_debt(db: AsyncSession, customer_id: int) -> Decimal:
    """What this customer currently owes on credit, in the deployment's base currency (#220).

    Summed from `attach_summary_totals`, so the figure is the same `balance` every endpoint already
    reports for those orders and the same one SC-004 pins — total less every non-cancelled
    application. A credit refusal a user cannot reconcile against the balances on their own screen
    is a support ticket, so there is one money rule here and not a second private one.

    **It does not net off refunds, and the monolith's `Debt()` does.** A refund requires a paid
    order here (FR-060), so on anything this API creates the question cannot arise; the divergence
    is entirely legacy rows, where the monolith allowed refunding an unpaid order. It is not small
    on those: 275 of 860 unpaid credit orders in mbe_dev carry refund lines, and netting them off
    would put the total at 9.0M against the 19.3M of balance the API reports today. The overstated
    figure is what those orders *say*, which is the defect to fix where balances are computed
    rather than to work around inside a credit check.

    Multiplied by each order's exchange rate because a limit is one number and balances are in
    whatever currency their document used. Unexercised today — all 861 unpaid credit orders in
    mbe_dev are MXN at exactly 1.0000 — so it is a rule written down, not a rule proven.
    """
    orders = list(
        (
            await db.execute(
                select(SalesOrder).where(
                    SalesOrder.customer == customer_id,
                    SalesOrder.payment_terms == PaymentTerms.NET_D,
                    SalesOrder.completed.is_(True),
                    SalesOrder.cancelled.is_(False),
                    SalesOrder.paid.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    if not orders:
        return Decimal(0)

    await attach_summary_totals(db, orders)
    return sum(
        (order.__dict__['balance'] * order.exchange_rate for order in orders), Decimal(0)
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

    now = data.date or datetime.now()
    currency = data.currency if data.currency is not None else settings.default_currency

    if data.payment_terms is not None:
        terms = data.payment_terms
    else:
        terms = (
            PaymentTerms.NET_D
            if customer.credit_limit
            and customer.credit_limit > 0
            and customer.customer_id != settings.default_customer_id
            else PaymentTerms.IMMEDIATE
        )
    # Outer indentation on purpose: the hold applies to the terms the order will actually carry,
    # whether the caller named them or the derivation above chose them (#207).
    if terms == PaymentTerms.NET_D:
        await _assert_credit_allowed(db, customer)

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
            both(
                or_(SalesOrder.sales_order_id == int(search), SalesOrder.serial == int(search))
            )
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
            order.modification_time = datetime.now()
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
    if 'payment_terms' in changes and changes['payment_terms'] is not None:
        terms = PaymentTerms(changes['payment_terms'])
        customer = await _customer_or_404(db, order.customer)
        order.payment_terms = int(terms)
        order.due_date = derive_due_date(
            order.date, terms, credit_days=customer.credit_days or 0
        )
    # Asserted on the state the order ends the request in, rather than inside either branch above
    # (#219). Moving a NET_D order onto a customer in arrears revisited the salesperson and the
    # prices and never the credit, so the terms survived the move unchallenged; and a request that
    # sets both fields must be judged once, at the end, or `{customer: walk-in, payment_terms: 0}`
    # is refused for terms the same request is clearing.
    if ('customer' in changes or 'payment_terms' in changes) and (
        order.payment_terms == PaymentTerms.NET_D
    ):
        await _assert_credit_allowed(db, await _customer_or_404(db, order.customer))
    if 'currency' in changes and changes['currency'] is not None:
        await _change_currency(db, order, CurrencyCode(changes['currency']))

    for field in ('promise_date', 'contact', 'ship_to', 'recipient',
                  'customer_name', 'comment'):
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
    order.modification_time = datetime.now()
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
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == order.sales_order_id
                )
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


async def _change_currency(
    db: AsyncSession, order: SalesOrder, currency: CurrencyCode
) -> None:
    """FR-020 — the header rate and every line move together, never a mixed-currency order."""
    rate = await _exchange_rate(db, currency, order.date)
    order.currency = currency
    order.exchange_rate = rate

    lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == order.sales_order_id
                )
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

    quantity = data.quantity if data.quantity is not None else Decimal(product.min_order_qty)
    assert_quantity_allowed(quantity, min_order_qty=product.min_order_qty)

    price = data.price if data.price is not None else (listed.price if listed else Decimal(0))
    cost = cost_row.price if cost_row else Decimal(0)

    line = SalesOrderDetail(
        sales_order=order.sales_order_id,
        product=product.product_id,
        quantity=quantity,
        cost=cost,
        price=price,
        discount_rate=data.discount_rate,
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
    order.modification_time = datetime.now()
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
    for field in ('discount_rate', 'tax_rate', 'warehouse', 'comment'):
        if field in changes and changes[field] is not None:
            setattr(line, field, changes[field])

    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def remove_line(
    db: AsyncSession, order: SalesOrder, line: SalesOrderDetail, *, current: CurrentUser
) -> SalesOrder:
    documents.assert_editable(order)
    order.updater = current.employee_id
    order.modification_time = datetime.now()
    await db.delete(line)
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def get_line(
    db: AsyncSession, order: SalesOrder, line_id: int
) -> SalesOrderDetail | None:
    line = await db.get(SalesOrderDetail, line_id)
    if line is None or line.sales_order != order.sales_order_id:
        return None
    return line


# ── Transitions ───────────────────────────────────────────────────────────────


async def confirm_order(
    db: AsyncSession, order: SalesOrder, *, current: CurrentUser
) -> SalesOrder:
    """Assign the folio, commit the stock, freeze the document — one transaction (FR-017)."""
    documents.assert_editable(order)
    # Before the folio and before the reservation: this is the act that extends credit, and until
    # #219 nothing checked it here. Creation and an explicit terms change were both guarded, but a
    # customer who fell behind after capture, an order moved onto a customer in arrears, and a
    # quote converted on credit terms all reached a committed sale unchallenged.
    await _assert_not_on_credit_hold(db, order.customer)
    employee = current.employee_id

    lines = list(
        (
            await db.execute(
                select(SalesOrderDetail).where(
                    SalesOrderDetail.sales_order == order.sales_order_id
                )
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
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


async def cancel_order(
    db: AsyncSession, order: SalesOrder, *, current: CurrentUser
) -> SalesOrder:
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
    order.modification_time = datetime.now()
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


async def lookup_products(
    db: AsyncSession,
    *,
    pattern: str,
    customer_id: int,
    warehouse: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find salable products with the customer's price and per-warehouse stock (FR-021)."""
    products = await _find_salable(db, pattern=pattern, limit=limit)
    customer = await _customer_or_404(db, customer_id)

    # In-transit locations are ordinary warehouse rows so `on_hand` reports their balances with
    # no new mechanism (spec 012, research R3). Goods on a truck are not pickable, so offering one
    # here would invite a salesperson to promise stock that has already left. There is one per
    # facility now, so all of them are excluded by flag (spec 013, FR-012).
    warehouses = (
        [warehouse]
        if warehouse is not None
        else (
            await db.execute(
                select(Warehouse.warehouse_id).where(
                    Warehouse.warehouse_id > 0,
                    Warehouse.in_transit.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )

    # Two aggregate queries for the whole result set, not two per product per warehouse.
    stockable = {p.product_id for p in products if p.stockable}
    on_hand_totals = await stock_ledger.on_hand_by_warehouse(db, products=stockable)
    reserved_totals = await stock_ledger.reserved_by_warehouse(db, products=stockable)
    units = await units_by_product(db, {p.product_id for p in products})

    results: list[dict] = []
    for product in products:
        listed = await _price_for(db, product, customer.price_list)
        stock: list[dict] = []
        if product.stockable:
            for wid in warehouses:
                held = on_hand_totals.get((product.product_id, wid), Decimal(0))
                claimed = reserved_totals.get((product.product_id, wid), Decimal(0))
                stock.append(
                    {
                        'warehouse': wid,
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
