"""Sales quotes — a priced, expiring offer that can become an order.

A quote is the same document shape as an order minus the fulfilment concerns, so it reuses the
shared folio, totals and editability helpers rather than repeating them. The one behaviour unique
to quotes is expiry: an offer that has passed its due date cannot be converted, because the prices
it names are no longer the ones being offered.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, PaymentTerms
from app.models.customer import Customer
from app.models.product import Product
from app.models.sales import SalesOrder, SalesOrderDetail, SalesQuote, SalesQuoteDetail
from app.schemas.sales_order import derive_status
from app.schemas.sales_quote import (
    SalesQuoteCreate,
    SalesQuoteLineCreate,
    SalesQuoteLineUpdate,
    SalesQuoteUpdate,
)
from app.services import documents, sales_order_service, totals

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def has_expired(quote: object, *, now: datetime | None = None) -> bool:
    """An offer past its due date. Expiry is about the date only, not the document's state."""
    due = getattr(quote, 'due_date', None)
    if due is None:
        return False
    return due < (now or datetime.now())


def assert_convertible(quote: object, *, now: datetime | None = None) -> None:
    """FR-034 — only a confirmed, uncancelled, unexpired quote becomes an order.

    Each refusal names its own cause: a salesperson who sent an expired quote needs to know to
    re-quote, which is a different action from confirming a draft.
    """
    if getattr(quote, 'cancelled', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='A cancelled quote cannot be converted'
        )
    if not getattr(quote, 'completed', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Only a confirmed quote can be converted; confirm it first',
        )
    if has_expired(quote, now=now):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Quote has expired and cannot be converted; duplicate it to re-quote',
        )


def default_due_date(date: datetime, *, validity_days: int) -> datetime:
    return date + timedelta(days=validity_days)


# ── Derived values ────────────────────────────────────────────────────────────


async def attach_derived(db: AsyncSession, quote: SalesQuote) -> SalesQuote:
    lines = list(
        (
            await db.execute(
                select(SalesQuoteDetail)
                .where(SalesQuoteDetail.sales_quote == quote.sales_quote_id)
                .order_by(SalesQuoteDetail.sales_quote_detail_id)
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        subtotal, tax = totals.line_amounts(
            quantity=line.quantity,
            price=line.price + line.price_adjustment,
            discount_rate=line.discount_rate,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
        line.__dict__['subtotal'] = subtotal.quantize(totals.CENTS)
        line.__dict__['tax_total'] = tax.quantize(totals.CENTS)
        line.__dict__['total'] = line.__dict__['subtotal'] + line.__dict__['tax_total']

    computed = totals.document_totals(
        [
            totals.Line(
                quantity=line.quantity,
                price=line.price + line.price_adjustment,
                discount_rate=line.discount_rate,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
            for line in lines
        ]
    )

    quote.__dict__['lines'] = lines
    quote.__dict__['subtotal'] = computed.subtotal
    quote.__dict__['tax_total'] = computed.tax_total
    quote.__dict__['total'] = computed.total
    quote.__dict__['has_expired'] = has_expired(quote)
    quote.__dict__['status'] = derive_status(
        completed=quote.completed, cancelled=quote.cancelled
    ).value
    return quote


async def attach_summary_totals(db: AsyncSession, quotes: Sequence[SalesQuote]) -> None:
    """Totals for a whole page in one query, instead of one query per quote."""
    ids = [q.sales_quote_id for q in quotes]
    if not ids:
        return

    grouped: dict[int, list[totals.Line]] = {qid: [] for qid in ids}
    rows = (
        (await db.execute(select(SalesQuoteDetail).where(SalesQuoteDetail.sales_quote.in_(ids))))
        .scalars()
        .all()
    )
    for row in rows:
        grouped[row.sales_quote].append(
            totals.Line(
                quantity=row.quantity,
                price=row.price + row.price_adjustment,
                discount_rate=row.discount_rate,
                tax_rate=row.tax_rate,
                tax_included=row.tax_included,
            )
        )

    for quote in quotes:
        computed = totals.document_totals(grouped.get(quote.sales_quote_id, []))
        quote.__dict__['subtotal'] = computed.subtotal
        quote.__dict__['tax_total'] = computed.tax_total
        quote.__dict__['total'] = computed.total
        quote.__dict__['has_expired'] = has_expired(quote)
        quote.__dict__['status'] = derive_status(
            completed=quote.completed, cancelled=quote.cancelled
        ).value


# ── Header ────────────────────────────────────────────────────────────────────



async def _customer_or_404(db: AsyncSession, customer_id: int) -> Customer:
    customer = await db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Customer not found')
    return customer


async def create_quote(
    db: AsyncSession, data: SalesQuoteCreate, *, current: CurrentUser
) -> SalesQuote:
    employee = current.employee_id
    if current.facility_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='No facility is configured for your user',
        )

    customer = await _customer_or_404(
        db, data.customer if data.customer is not None else settings.default_customer_id
    )
    now = data.date or datetime.now()
    currency = data.currency if data.currency is not None else settings.default_currency
    terms = data.payment_terms if data.payment_terms is not None else PaymentTerms.IMMEDIATE

    quote = SalesQuote(
        facility=current.facility_id,
        serial=None,
        date=now,
        salesperson=data.salesperson
        or documents.default_salesperson(customer.salesperson, employee),
        customer=customer.customer_id,
        payment_terms=int(terms),
        due_date=data.due_date
        or default_due_date(now, validity_days=settings.default_quotation_due_days),
        completed=False,
        cancelled=False,
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        contact=data.contact,
        ship_to=data.ship_to,
        comment=data.comment,
        currency=currency,
        exchange_rate=Decimal(1),
    )
    db.add(quote)
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


async def get_quote(db: AsyncSession, sales_quote_id: int) -> SalesQuote | None:
    return await db.get(SalesQuote, sales_quote_id)


async def list_quotes(
    db: AsyncSession,
    *,
    current: CurrentUser,
    mine: bool = False,
    customer: int | None = None,
    salesperson: int | None = None,
    quote_status: str | None = None,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[SalesQuote], int]:
    base = select(SalesQuote)
    count_q = select(func.count()).select_from(SalesQuote)

    def both(clause):  # noqa: ANN001, ANN202
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    both(SalesQuote.facility == current.facility_id)
    if mine and current.employee_id is not None:
        both(
            or_(
                SalesQuote.creator == current.employee_id,
                SalesQuote.updater == current.employee_id,
                SalesQuote.salesperson == current.employee_id,
            )
        )
    if customer is not None:
        both(SalesQuote.customer == customer)
    if salesperson is not None:
        both(SalesQuote.salesperson == salesperson)
    if quote_status == 'draft':
        both(SalesQuote.completed.is_(False))
        both(SalesQuote.cancelled.is_(False))
    elif quote_status == 'completed':
        both(SalesQuote.completed.is_(True))
        both(SalesQuote.cancelled.is_(False))
    elif quote_status == 'cancelled':
        both(SalesQuote.cancelled.is_(True))
    if search and search.isdigit():
        both(or_(SalesQuote.sales_quote_id == int(search), SalesQuote.serial == int(search)))

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(SalesQuote.sales_quote_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    await attach_summary_totals(db, items)
    return items, total


async def update_quote(
    db: AsyncSession, quote: SalesQuote, data: SalesQuoteUpdate, *, current: CurrentUser
) -> SalesQuote:
    documents.assert_editable(quote)
    employee = current.employee_id
    changes = data.model_dump(exclude_unset=True)

    if 'customer' in changes and changes['customer'] is not None:
        customer = await _customer_or_404(db, changes['customer'])
        moved = customer.customer_id != quote.customer
        quote.customer = customer.customer_id
        # Same rule as `update_order` (#195). `moved` matters because re-deriving on a `PUT`
        # that has not moved the customer would overwrite a deliberate assignment.
        if moved and customer.salesperson is not None:
            quote.salesperson = customer.salesperson
    if 'payment_terms' in changes and changes['payment_terms'] is not None:
        quote.payment_terms = int(PaymentTerms(changes['payment_terms']))
    if 'currency' in changes and changes['currency'] is not None:
        quote.currency = CurrencyCode(changes['currency'])
    for field in ('due_date', 'contact', 'ship_to', 'comment'):
        if field in changes:
            setattr(quote, field, changes[field])
    if changes.get('salesperson') is not None:
        quote.salesperson = changes['salesperson']

    quote.updater = employee
    quote.modification_time = datetime.now()
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


# ── Lines ─────────────────────────────────────────────────────────────────────


async def get_line(
    db: AsyncSession, quote: SalesQuote, line_id: int
) -> SalesQuoteDetail | None:
    line = await db.get(SalesQuoteDetail, line_id)
    if line is None or line.sales_quote != quote.sales_quote_id:
        return None
    return line


async def _listed_price(db: AsyncSession, product: Product, customer: Customer) -> Decimal:
    from app.models.product import ProductPrice

    row = (
        await db.execute(
            select(ProductPrice).where(
                ProductPrice.product == product.product_id,
                ProductPrice.price_list == customer.price_list,
            )
        )
    ).scalar_one_or_none()
    return row.price if row else Decimal(0)


async def add_line(
    db: AsyncSession, quote: SalesQuote, data: SalesQuoteLineCreate, *, current: CurrentUser
) -> SalesQuote:
    documents.assert_editable(quote)
    employee = current.employee_id

    product = await db.get(Product, data.product)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')

    customer = await _customer_or_404(db, quote.customer)
    quantity = data.quantity if data.quantity is not None else Decimal(product.min_order_qty)
    sales_order_service.assert_quantity_allowed(quantity, min_order_qty=product.min_order_qty)

    db.add(
        SalesQuoteDetail(
            sales_quote=quote.sales_quote_id,
            product=product.product_id,
            quantity=quantity,
            price=data.price
            if data.price is not None
            else await _listed_price(db, product, customer),
            price_adjustment=data.price_adjustment,
            discount_rate=data.discount_rate,
            tax_rate=product.tax_rate,
            product_code=product.code,
            product_name=product.name,
            exchange_rate=quote.exchange_rate,
            currency=quote.currency,
            tax_included=product.tax_included,
            comment=data.comment if data.comment is not None else product.comment,
        )
    )
    quote.updater = employee
    quote.modification_time = datetime.now()
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


async def update_line(
    db: AsyncSession,
    quote: SalesQuote,
    line: SalesQuoteDetail,
    data: SalesQuoteLineUpdate,
    *,
    current: CurrentUser,
) -> SalesQuote:
    documents.assert_editable(quote)
    changes = data.model_dump(exclude_unset=True)

    if 'quantity' in changes and changes['quantity'] is not None:
        product = await db.get(Product, line.product)
        sales_order_service.assert_quantity_allowed(
            changes['quantity'], min_order_qty=product.min_order_qty if product else 1
        )
        line.quantity = changes['quantity']
    for field in ('price', 'price_adjustment', 'discount_rate', 'comment'):
        if field in changes and changes[field] is not None:
            setattr(line, field, changes[field])

    quote.updater = current.employee_id
    quote.modification_time = datetime.now()
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


async def remove_line(
    db: AsyncSession, quote: SalesQuote, line: SalesQuoteDetail, *, current: CurrentUser
) -> SalesQuote:
    documents.assert_editable(quote)
    quote.updater = current.employee_id
    quote.modification_time = datetime.now()
    await db.delete(line)
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


# ── Transitions ───────────────────────────────────────────────────────────────


async def confirm_quote(
    db: AsyncSession, quote: SalesQuote, *, current: CurrentUser
) -> SalesQuote:
    """Assign the folio and freeze the offer (FR-032)."""
    documents.assert_editable(quote)
    quote.serial = await documents.assign_folio(db, SalesQuote, facility=quote.facility)
    quote.completed = True
    quote.updater = current.employee_id
    quote.modification_time = datetime.now()
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


async def cancel_quote(db: AsyncSession, quote: SalesQuote, *, current: CurrentUser) -> SalesQuote:
    if quote.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Quote is already cancelled'
        )
    quote.cancelled = True
    quote.updater = current.employee_id
    quote.modification_time = datetime.now()
    await db.commit()
    await db.refresh(quote)
    return await attach_derived(db, quote)


async def duplicate_quote(
    db: AsyncSession, quote: SalesQuote, *, current: CurrentUser
) -> SalesQuote:
    """A fresh editable quote dated today, re-priced from the customer's current list (FR-033)."""
    employee = current.employee_id
    now = datetime.now()
    customer = await _customer_or_404(db, quote.customer)

    copy = SalesQuote(
        facility=quote.facility,
        serial=None,
        date=now,
        salesperson=quote.salesperson,
        customer=quote.customer,
        payment_terms=quote.payment_terms,
        due_date=default_due_date(now, validity_days=settings.default_quotation_due_days),
        completed=False,
        cancelled=False,
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        contact=quote.contact,
        ship_to=quote.ship_to,
        comment=quote.comment,
        currency=quote.currency,
        exchange_rate=quote.exchange_rate,
    )
    db.add(copy)
    await db.flush()

    lines = (
        (
            await db.execute(
                select(SalesQuoteDetail).where(
                    SalesQuoteDetail.sales_quote == quote.sales_quote_id
                )
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        product = await db.get(Product, line.product)
        db.add(
            SalesQuoteDetail(
                sales_quote=copy.sales_quote_id,
                product=line.product,
                quantity=line.quantity,
                # Re-fetched, not copied: yesterday's price is not today's offer
                price=await _listed_price(db, product, customer) if product else line.price,
                price_adjustment=line.price_adjustment,
                discount_rate=line.discount_rate,
                tax_rate=line.tax_rate,
                product_code=line.product_code,
                product_name=line.product_name,
                exchange_rate=line.exchange_rate,
                currency=line.currency,
                tax_included=line.tax_included,
                comment=line.comment,
            )
        )

    await db.commit()
    await db.refresh(copy)
    return await attach_derived(db, copy)


async def convert_to_order(
    db: AsyncSession, quote: SalesQuote, *, current: CurrentUser
) -> SalesOrder:
    """Produce a draft order carrying the quote's terms and lines (FR-034)."""
    employee = current.employee_id
    assert_convertible(quote)

    point_sale = current.point_sale_id
    if point_sale is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='No point of sale is configured for your user; set one or supply it explicitly',
        )

    customer = await _customer_or_404(db, quote.customer)
    now = datetime.now()
    terms = PaymentTerms(quote.payment_terms)

    order = SalesOrder(
        facility=quote.facility,
        serial=None,
        point_sale=point_sale,
        salesperson=quote.salesperson,
        customer=quote.customer,
        customer_name=None,
        sales_quote=quote.sales_quote_id,
        payment_terms=int(terms),
        date=now,
        promise_date=now + timedelta(days=settings.max_days_to_deliver_stockables),
        due_date=sales_order_service.derive_due_date(
            now, terms, credit_days=customer.credit_days or 0
        ),
        completed=False,
        cancelled=False,
        paid=False,
        delivered=False,
        creator=employee,
        updater=employee,
        creation_time=now,
        modification_time=now,
        balance_zeroed_time=None,
        contact=quote.contact,
        ship_to=quote.ship_to,
        recipient=None,
        recipient_name=None,
        recipient_address=None,
        comment=quote.comment,
        currency=quote.currency,
        exchange_rate=quote.exchange_rate,
        customer_shipto=None,
        priority=1,
        partial_deliveries=None,
        # A quote has no fulfilment intent to carry: it is asked at the counter when the sale is
        # taken, which is after this. The order starts "not recorded" and the point of sale sets it
        # with a `PUT` (#170).
        fulfillment_intent=None,
    )
    db.add(order)
    await db.flush()

    lines = (
        (
            await db.execute(
                select(SalesQuoteDetail).where(
                    SalesQuoteDetail.sales_quote == quote.sales_quote_id
                )
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        db.add(
            SalesOrderDetail(
                sales_order=order.sales_order_id,
                product=line.product,
                quantity=line.quantity,
                cost=Decimal(0),
                # The adjustment is folded into the order line's price; orders have no
                # price_adjustment column of their own
                price=line.price + line.price_adjustment,
                discount_rate=line.discount_rate,
                tax_rate=line.tax_rate,
                product_code=line.product_code,
                product_name=line.product_name,
                warehouse=None,
                exchange_rate=line.exchange_rate,
                currency=line.currency,
                tax_included=line.tax_included,
                comment=line.comment,
            )
        )

    await db.commit()
    await db.refresh(order)
    return await sales_order_service.attach_derived(db, order)
