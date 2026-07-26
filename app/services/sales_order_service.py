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
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, PaymentTerms, TransactionType
from app.models.core import ExchangeRate, Warehouse
from app.models.customer import Customer
from app.models.product import Product, ProductPrice
from app.models.sales import SalesOrder, SalesOrderDetail, SalesOrderPayment
from app.schemas.sales_order import (
    SalesOrderCreate,
    SalesOrderLineCreate,
    SalesOrderLineUpdate,
    SalesOrderUpdate,
)
from app.services import documents, stock_ledger, totals

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


def assert_price_in_margin(
    price: Decimal, *, low: Decimal, high: Decimal, enabled: bool, exempt: bool
) -> None:
    """Refuse a price outside the product's profit band (FR-014).

    Bypassed for a caller holding ExcludePriceRangeValidation (102), and skipped entirely when the
    deployment turns margin validation off.
    """
    if not enabled or exempt:
        return
    if price < low or price > high:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Price {price} is outside the allowed margin [{low}, {high}]',
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
    on_hand: dict[tuple[int, int], Decimal],
    stocked: set[int],
) -> list[str]:
    """Report what the order cannot fulfil (FR-018).

    Quantities are aggregated per product+warehouse first: a product on three lines is one demand
    against the warehouse, not three independent ones that each pass while the total does not.
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
        available = on_hand.get((product, warehouse), Decimal(0))
        if needed > available:
            problems.append(
                f'Product {product} needs {needed} in warehouse {warehouse} but only {available} '
                f'is on hand'
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


def _employee(current: CurrentUser) -> int:
    """FR-002 — a user with no employee cannot author a document."""
    if current.employee_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Your user account is not linked to an employee and cannot author documents',
        )
    return current.employee_id


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
    for line in lines:
        _line_totals(line)

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

    order.__dict__['lines'] = lines
    order.__dict__['subtotal'] = computed.subtotal
    order.__dict__['tax_total'] = computed.tax_total
    order.__dict__['total'] = computed.total
    order.__dict__['balance'] = totals.remaining(computed.total, [applied])
    order.__dict__['status'] = _status(order)
    return order


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


async def _assert_credit_allowed(db: AsyncSession, customer: Customer) -> None:
    """Credit terms need a real credit line and a customer in good standing (FR-016)."""
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

    now = datetime.now()
    expired = (
        await db.execute(
            select(func.count())
            .select_from(SalesOrder)
            .where(
                SalesOrder.customer == customer.customer_id,
                SalesOrder.completed.is_(True),
                SalesOrder.cancelled.is_(False),
                SalesOrder.paid.is_(False),
                SalesOrder.due_date < now,
            )
        )
    ).scalar_one()
    if expired:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Customer has {expired} overdue credit order(s)',
        )


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
    employee = _employee(current)
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
    if terms == PaymentTerms.NET_D:
        await _assert_credit_allowed(db, customer)

    order = SalesOrder(
        facility=facility,
        serial=None,
        point_sale=point_sale,
        salesperson=data.salesperson or customer.salesperson or employee,
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
            both(SalesOrder.customer_name.ilike(f'%{search}%'))

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(SalesOrder.sales_order_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    for order in items:
        await attach_derived(db, order)
    return items, total


async def update_order(
    db: AsyncSession, order: SalesOrder, data: SalesOrderUpdate, *, current: CurrentUser
) -> SalesOrder:
    employee = _employee(current)
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
        order.customer = customer.customer_id
    if 'payment_terms' in changes and changes['payment_terms'] is not None:
        terms = PaymentTerms(changes['payment_terms'])
        customer = await _customer_or_404(db, order.customer)
        if terms == PaymentTerms.NET_D:
            await _assert_credit_allowed(db, customer)
        order.payment_terms = int(terms)
        order.due_date = derive_due_date(
            order.date, terms, credit_days=customer.credit_days or 0
        )
    if 'currency' in changes and changes['currency'] is not None:
        await _change_currency(db, order, CurrencyCode(changes['currency']))

    for field in ('salesperson', 'promise_date', 'contact', 'ship_to', 'recipient',
                  'customer_name', 'comment'):
        if field in changes:
            setattr(order, field, changes[field])
    if 'priority' in changes and changes['priority'] is not None:
        order.priority = int(changes['priority'])

    order.updater = employee
    order.modification_time = datetime.now()
    await db.commit()
    await db.refresh(order)
    return await attach_derived(db, order)


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
    employee = _employee(current)

    product = await db.get(Product, data.product)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')

    customer = await _customer_or_404(db, order.customer)
    listed = await _price_for(db, product, customer.price_list)
    cost_row = await _price_for(db, product, settings.cost_price_list_id)

    quantity = data.quantity if data.quantity is not None else Decimal(product.min_order_qty)
    assert_quantity_allowed(quantity, min_order_qty=product.min_order_qty)

    price = data.price if data.price is not None else (listed.price if listed else Decimal(0))
    if listed is not None:
        assert_price_in_margin(
            price,
            low=listed.low_profit,
            high=listed.high_profit,
            enabled=settings.price_validation_in_range_required,
            exempt=await _exempt_from_margin(db, current),
        )

    line = SalesOrderDetail(
        sales_order=order.sales_order_id,
        product=product.product_id,
        quantity=quantity,
        cost=cost_row.price if cost_row else Decimal(0),
        price=price,
        discount_rate=data.discount_rate,
        tax_rate=product.tax_rate,
        product_code=product.code,
        product_name=product.name,
        delivery=data.delivery,
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
    employee = _employee(current)
    changes = data.model_dump(exclude_unset=True)

    product = await db.get(Product, line.product)
    if 'quantity' in changes and changes['quantity'] is not None:
        assert_quantity_allowed(
            changes['quantity'], min_order_qty=product.min_order_qty if product else 1
        )
        line.quantity = changes['quantity']
    if 'price' in changes and changes['price'] is not None:
        customer = await _customer_or_404(db, order.customer)
        listed = await _price_for(db, product, customer.price_list) if product else None
        if listed is not None:
            assert_price_in_margin(
                changes['price'],
                low=listed.low_profit,
                high=listed.high_profit,
                enabled=settings.price_validation_in_range_required,
                exempt=await _exempt_from_margin(db, current),
            )
        line.price = changes['price']
    for field in ('discount_rate', 'warehouse', 'delivery', 'comment'):
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
    order.updater = _employee(current)
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


async def _exempt_from_margin(db: AsyncSession, current: CurrentUser) -> bool:
    from app.enums import AccessRight, SystemObject
    from app.models.user import AccessPrivilege

    if current.administrator:
        return True
    priv = (
        await db.execute(
            select(AccessPrivilege).where(
                AccessPrivilege.user_id == current.user_id,
                AccessPrivilege.system_object == int(SystemObject.EXCLUDE_PRICE_RANGE_VALIDATION),
            )
        )
    ).scalar_one_or_none()
    return priv is not None and bool(priv.privileges & int(AccessRight.UPDATE))


# ── Transitions ───────────────────────────────────────────────────────────────


async def confirm_order(
    db: AsyncSession, order: SalesOrder, *, current: CurrentUser
) -> SalesOrder:
    """Assign the folio, commit the stock, freeze the document — one transaction (FR-017)."""
    documents.assert_editable(order)
    employee = _employee(current)

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
    on_hand = {}
    for line in lines:
        if line.product in stocked and line.warehouse is not None:
            key = (line.product, line.warehouse)
            if key not in on_hand:
                on_hand[key] = await stock_ledger.on_hand(
                    db, product=line.product, warehouse=line.warehouse
                )

    problems = stock_shortfalls(lines, on_hand=on_hand, stocked=stocked)
    if problems:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'message': 'Insufficient stock', 'lines': problems},
        )

    order.serial = await documents.assign_folio(db, SalesOrder, facility=order.facility)

    for line in lines:
        if line.product in stocked and line.warehouse is not None:
            stock_ledger.post_movement(
                db,
                source=TransactionType.SALES_ORDER,
                reference=order.sales_order_id,
                product=line.product,
                warehouse=line.warehouse,
                quantity=line.quantity,
                outbound=True,
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
    employee = _employee(current)
    assert_can_cancel(order, live_applications=await live_applications(db, order.sales_order_id))

    if order.completed:
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
        stocked = await _stocked_products(db, {line.product for line in lines})
        for line in lines:
            if line.product in stocked and line.warehouse is not None:
                # A compensating entry, not an edit — the sale and its reversal stay visible
                stock_ledger.post_movement(
                    db,
                    source=TransactionType.SALES_ORDER,
                    reference=order.sales_order_id,
                    product=line.product,
                    warehouse=line.warehouse,
                    quantity=line.quantity,
                    outbound=False,
                )

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


async def lookup_products(
    db: AsyncSession,
    *,
    pattern: str,
    customer_id: int,
    warehouse: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Find salable products with the customer's price and per-warehouse stock (FR-021).

    A 13-digit numeric pattern is a barcode scan, not a search term.
    """
    query = select(Product).where(Product.salable.is_(True))

    if pattern.isdigit() and len(pattern) == BARCODE_LENGTH:
        query = query.where(Product.bar_code == pattern)
    else:
        like = f'%{pattern}%'
        query = query.where(
            or_(
                Product.name.ilike(like),
                Product.code.ilike(like),
                Product.sku.ilike(like),
                Product.brand.ilike(like),
                Product.model.ilike(like),
            )
        )

    products = (await db.execute(query.limit(limit))).scalars().all()
    customer = await _customer_or_404(db, customer_id)

    results: list[dict] = []
    for product in products:
        listed = await _price_for(db, product, customer.price_list)
        stock: list[dict] = []
        warehouses = (
            [warehouse]
            if warehouse is not None
            else (
                await db.execute(select(Warehouse.warehouse_id).where(Warehouse.warehouse_id > 0))
            )
            .scalars()
            .all()
        )
        if product.stockable:
            for wid in warehouses:
                stock.append(
                    {
                        'warehouse': wid,
                        'on_hand': await stock_ledger.on_hand(
                            db, product=product.product_id, warehouse=wid
                        ),
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
